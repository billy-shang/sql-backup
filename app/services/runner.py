"""把一次备份写入历史、更新任务状态并发送飞书。"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import BackupRecord, DbConnection, Schedule
from app.services import progress as prog
from app.services.backup import resolve_backup_databases, run_backup
from app.services.notify import notify_job_result
from app.services.remote_upload import upload_backup_to_remote

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _backup_one(
    db: Session,
    conn: DbConnection,
    dbname: str,
    *,
    backup_type: str,
    compress: bool,
    retain_days: int,
    delete_old: bool,
    schedule_id: int | None,
    trigger: str,
) -> BackupRecord:
    rec = BackupRecord(
        connection_id=conn.id,
        schedule_id=schedule_id,
        dbname=dbname,
        backup_type=backup_type,
        status="running",
        trigger=trigger,
        started_at=_now(),
    )
    db.add(rec)
    db.commit()
    rec_id = rec.id
    log.info("[runner] 备份开始 id=%s conn=%s db=%s type=%s", rec_id, conn.id, dbname, backup_type)
    try:
        result = run_backup(
            conn,
            dbname=dbname,
            backup_type=backup_type,
            compress=compress,
            retain_days=retain_days,
            delete_old=delete_old,
        )
        rec.status = "success"
        rec.file_path = result.get("file_path") or ""
        rec.local_path = result.get("local_path") or ""
        rec.file_size = int(result.get("file_size") or 0)
        rec.finished_at = _now()
        rec.error_message = ""
        rec.remote_status = ""
        rec.remote_path = ""
        rec.remote_error = ""
        db.commit()
        if conn.remote_enabled and conn.remote_target_id:
            try:
                rec.remote_path = upload_backup_to_remote(
                    db,
                    conn,
                    rec,
                    retain_days=retain_days,
                    delete_old=delete_old,
                ) or ""
                rec.remote_status = "success"
                log.info("[runner] 群晖上传成功 id=%s path=%s", rec.id, rec.remote_path)
            except Exception as re:  # noqa: BLE001
                rec.remote_status = "failed"
                rec.remote_error = str(re)[:2000]
                log.warning("[runner] 群晖上传失败 id=%s: %s", rec.id, re)
            db.commit()
        log.info("[runner] 备份成功 id=%s db=%s size=%s", rec.id, dbname, rec.file_size)
    except Exception as e:  # noqa: BLE001
        rec.status = "failed"
        rec.error_message = str(e)[:2000]
        rec.finished_at = _now()
        db.commit()
        log.warning("[runner] 备份失败 id=%s db=%s: %s", rec.id, dbname, e)
    db.refresh(rec)
    return rec


def execute_backup(
    db: Session,
    connection_id: int,
    *,
    backup_type: str = "full",
    compress: bool = True,
    retain_days: int = 7,
    delete_old: bool = True,
    schedule_id: int | None = None,
    trigger: str = "manual",
) -> list[BackupRecord]:
    conn = db.query(DbConnection).filter(DbConnection.id == connection_id).one_or_none()
    if not conn:
        raise RuntimeError("数据库连接不存在")
    names = resolve_backup_databases(conn)
    log.info("[runner] 连接 #%s 将备份 %s 个库: %s", connection_id, len(names), names)
    if schedule_id:
        sch = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
        if sch:
            sch.last_status = "running"
            sch.last_run_at = _now()
            sch.last_error = ""
            db.commit()
    recs: list[BackupRecord] = []
    total = max(len(names), 1)
    if names:
        prog.set_total(connection_id, total, names[0])
    for i, name in enumerate(names):
        prog.mark_db_start(connection_id, name, i, total)
        recs.append(
            _backup_one(
                db,
                conn,
                name,
                backup_type=backup_type,
                compress=compress,
                retain_days=retain_days,
                delete_old=delete_old,
                schedule_id=schedule_id,
                trigger=trigger,
            )
        )
        prog.mark_db_done(connection_id, name, i + 1, total)
    if schedule_id:
        sch = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
        if sch:
            failed = [r for r in recs if r.status == "failed"]
            if not recs:
                sch.last_status = "failed"
                sch.last_error = "没有可备份的数据库"
            elif failed and len(failed) == len(recs):
                sch.last_status = "failed"
                sch.last_error = failed[0].error_message
            elif failed:
                sch.last_status = "failed"
                sch.last_error = "部分失败：" + "; ".join(f"{r.dbname}:{r.error_message}" for r in failed)[:1800]
            else:
                sch.last_status = "success"
                sch.last_error = ""
            db.commit()
    try:
        notify_job_result(
            db,
            conn_name=conn.name,
            host=conn.host,
            port=int(conn.port or 1433),
            when=_now().strftime("%Y-%m-%d %H:%M"),
            backup_type=backup_type,
            recs=recs,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[runner] 汇总通知失败: %s", e)
    return recs


def _finish_schedule_progress(cid: int, recs: list[BackupRecord]) -> None:
    failed = [r for r in recs if r.status == "failed"]
    if not recs:
        prog.finish(cid, "failed", "没有可备份的数据库")
        return
    if failed:
        names = "、".join(r.dbname for r in failed if r.dbname)
        prog.finish(cid, "failed", f"失败 {len(failed)}/{len(recs)}" + (f"：{names}" if names else ""))
        return
    prog.finish(cid, "success", f"定时备份完成 {len(recs)} 个库")


def execute_schedule_job(schedule_id: int, *, ignore_enabled: bool = False) -> None:
    db = SessionLocal()
    cid = 0
    locked = False
    try:
        sch = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
        if not sch or (not sch.enabled and not ignore_enabled):
            log.info("[runner] 跳过任务 #%s（不存在或已暂停）", schedule_id)
            return
        cid = int(sch.connection_id)
        if not prog.start_job(cid, 1):
            log.info("[runner] 连接 #%s 正在备份，定时任务 #%s 跳过", cid, schedule_id)
            sch.last_status = "failed"
            sch.last_run_at = _now()
            sch.last_error = "该连接正在备份，本次定时已跳过"
            db.commit()
            return
        locked = True
        recs = execute_backup(
            db,
            sch.connection_id,
            backup_type=sch.backup_type,
            compress=bool(sch.compress),
            retain_days=int(sch.retain_days or 7),
            delete_old=bool(sch.delete_old),
            schedule_id=sch.id,
            trigger="schedule",
        )
        _finish_schedule_progress(cid, recs)
        locked = False
    except Exception as e:  # noqa: BLE001
        log.exception("[runner] 定时任务 #%s 异常: %s", schedule_id, e)
        try:
            sch = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
            if sch:
                sch.last_status = "failed"
                sch.last_error = str(e)[:2000]
                db.commit()
        except Exception:  # noqa: BLE001
            pass
        if locked and cid:
            prog.finish(cid, "failed", str(e)[:500])
            locked = False
    finally:
        if locked and cid:
            prog.finish(cid, "failed", "定时备份异常结束")
        db.close()
