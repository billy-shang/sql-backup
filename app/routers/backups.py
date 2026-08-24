from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_

from app.db import SessionLocal
from app.deps import AdminUser, CurrentUser, DbSess, TokenUser
from app.models import BackupRecord, DbConnection
from app.schemas import BackupOut, BackupRunIn, RestoreIn
from app.services import progress as prog
from app.services.backup import inspect_backup_file, list_backup_catalog, restore_database
from app.services.notify import notify_backup_result
from app.services.remote_upload import upload_backup_to_remote
from app.services.runner import execute_backup

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backups", tags=["backups"])


def _downloadable(row: BackupRecord) -> bool:
    p = (row.local_path or "").strip() or (row.file_path or "").strip()
    return bool(p) and os.path.isfile(p)


def _to_out(row: BackupRecord, conn: DbConnection | None) -> BackupOut:
    return BackupOut(
        id=row.id,
        connection_id=row.connection_id,
        connection_name=(conn.name if conn else ""),
        database=((row.dbname or "").strip() or (conn.database if conn else "")),
        schedule_id=row.schedule_id,
        backup_type=row.backup_type,
        status=row.status,
        trigger=row.trigger,
        file_path=row.file_path,
        local_path=row.local_path,
        file_size=row.file_size,
        error_message=row.error_message,
        remote_path=getattr(row, "remote_path", "") or "",
        remote_status=getattr(row, "remote_status", "") or "",
        remote_error=getattr(row, "remote_error", "") or "",
        started_at=row.started_at,
        finished_at=row.finished_at,
        downloadable=_downloadable(row),
    )


@router.get("")
def list_backups(
    _user: CurrentUser,
    db: DbSess,
    connection_id: int | None = None,
    status: str | None = None,
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=200),
) -> dict:
    query = db.query(BackupRecord).outerjoin(DbConnection, BackupRecord.connection_id == DbConnection.id)
    if connection_id:
        query = query.filter(BackupRecord.connection_id == connection_id)
    if status:
        query = query.filter(BackupRecord.status == status.strip())
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        query = query.filter(
            or_(
                BackupRecord.dbname.ilike(like),
                BackupRecord.file_path.ilike(like),
                BackupRecord.error_message.ilike(like),
                BackupRecord.remote_path.ilike(like),
                DbConnection.name.ilike(like),
            )
        )
    total = query.count()
    rows = query.order_by(BackupRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    conn_map = {c.id: c for c in db.query(DbConnection).all()}
    items = [_to_out(r, conn_map.get(r.connection_id)).model_dump() for r in rows]
    return {"ok": True, "items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/catalog")
def backup_catalog(connection_id: int, _user: CurrentUser, db: DbSess) -> dict:
    conn = db.query(DbConnection).filter(DbConnection.id == connection_id).one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    try:
        items = list_backup_catalog(conn)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)[:500]) from e
    return {"ok": True, "root": conn.backup_dir, "items": items}


def _resolve_restore_file(db, body: RestoreIn) -> tuple[int, str]:
    file_path = (body.file_path or "").strip()
    cid = int(body.connection_id)
    if body.backup_id:
        row = db.query(BackupRecord).filter(BackupRecord.id == body.backup_id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="备份记录不存在")
        if (row.status or "") != "success":
            raise HTTPException(status_code=400, detail="只能从成功的备份恢复")
        file_path = file_path or (row.file_path or "").strip()
        if not cid:
            cid = int(row.connection_id)
    if not file_path:
        raise HTTPException(status_code=400, detail="请指定备份文件路径")
    if not cid:
        raise HTTPException(status_code=400, detail="请选择目标连接")
    return cid, file_path


@router.get("/restore/preview")
def restore_preview(
    _admin: AdminUser,
    db: DbSess,
    connection_id: int | None = None,
    backup_id: int | None = None,
    file_path: str = Query(""),
) -> dict:
    cid = int(connection_id or 0)
    path = (file_path or "").strip()
    if backup_id:
        row = db.query(BackupRecord).filter(BackupRecord.id == backup_id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="备份记录不存在")
        path = path or (row.file_path or "").strip()
        cid = cid or int(row.connection_id)
    conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    if not path:
        raise HTTPException(status_code=400, detail="请指定备份文件路径")
    try:
        info = inspect_backup_file(conn, path)
    except Exception as e:  # noqa: BLE001
        log.warning("[restore] 预览失败 cid=%s path=%s: %s", cid, path, e)
        raise HTTPException(status_code=400, detail=str(e)[:500]) from e
    return {"ok": True, "connection_id": cid, **info}


def _run_restore_job(cid: int, file_path: str, target_db: str, replace: bool, recovery: bool) -> None:
    db = SessionLocal()
    try:
        conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
        if not conn:
            prog.finish(cid, "failed", "连接不存在")
            return
        prog.set_message(cid, f"正在恢复到 {target_db}", target_db)
        log.info("[restore] 后台恢复 cid=%s target=%s file=%s", cid, target_db, file_path)
        restore_database(conn, file_path, target_db, replace=replace, recovery=recovery)
        msg = f"已恢复到 {target_db}" + ("" if recovery else "（NORECOVERY，库尚未联机）")
        prog.finish(cid, "success", msg)
        log.info("[restore] 后台恢复成功 cid=%s target=%s", cid, target_db)
        notify_backup_result(
            db,
            ok=True,
            database=target_db,
            when=datetime.now().strftime("%Y-%m-%d %H:%M"),
            conn_name=conn.name or "",
            host=conn.host or "",
            port=int(conn.port or 1433),
            file_path=file_path,
            kind="恢复",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[restore] 后台恢复失败 cid=%s: %s", cid, e)
        prog.finish(cid, "failed", str(e)[:500])
        conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
        try:
            notify_backup_result(
                db,
                ok=False,
                database=target_db,
                when=datetime.now().strftime("%Y-%m-%d %H:%M"),
                conn_name=conn.name if conn else "",
                host=conn.host if conn else "",
                port=int(conn.port or 1433) if conn else 1433,
                file_path=file_path,
                error=str(e)[:500],
                kind="恢复",
            )
        except Exception as ne:  # noqa: BLE001
            log.warning("[restore] 恢复失败通知未发出: %s", ne)
    finally:
        db.close()


@router.post("/restore")
def restore_now(body: RestoreIn, _admin: AdminUser, db: DbSess) -> dict:
    cid, file_path = _resolve_restore_file(db, body)
    conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    target = (body.target_database or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="请填写目标库名")
    if not prog.start_job(cid, 1, kind="restore"):
        raise HTTPException(status_code=409, detail="该连接正在备份或恢复，请稍后再试")
    threading.Thread(
        target=_run_restore_job,
        args=(cid, file_path, target, body.replace, body.recovery),
        daemon=True,
    ).start()
    log.info("[restore] 已启动后台恢复 cid=%s target=%s", cid, target)
    return {"ok": True, "started": True, "connection_id": cid}


def _run_manual_job(cid: int, body: BackupRunIn) -> None:
    db = SessionLocal()
    try:
        recs = execute_backup(
            db,
            cid,
            backup_type=body.backup_type,
            compress=body.compress,
            retain_days=body.retain_days,
            delete_old=body.delete_old,
            trigger="manual",
        )
        conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
        host = conn.host if conn else ""
        if not recs:
            prog.finish(cid, "failed", "没有可备份的数据库")
            return
        failed = [r for r in recs if r.status == "failed"]
        ok_recs = [r for r in recs if r.status == "success"]
        if not ok_recs:
            prog.finish(cid, "failed", failed[0].error_message or "备份失败")
            return
        msg = f"已在数据库服务器 {host} 生成 {len(ok_recs)} 个文件"
        if failed:
            msg += "；失败：" + "、".join(r.dbname for r in failed)
        prog.finish(cid, "success" if not failed else "failed", msg)
        log.info("[backups] 手动备份结束 cid=%s ok=%s failed=%s", cid, len(ok_recs), len(failed))
    except Exception as e:  # noqa: BLE001
        log.warning("[backups] 手动备份异常 cid=%s: %s", cid, e)
        prog.finish(cid, "failed", str(e)[:500])
    finally:
        db.close()


@router.post("/run/{cid}")
def run_now(cid: int, body: BackupRunIn, _user: CurrentUser, db: DbSess) -> dict:
    conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    if not prog.start_job(cid, 1):
        raise HTTPException(status_code=409, detail="该连接正在备份，请稍后再试")
    threading.Thread(target=_run_manual_job, args=(cid, body), daemon=True).start()
    log.info("[backups] 已启动后台备份 cid=%s type=%s", cid, body.backup_type)
    return {"ok": True, "started": True, "connection_id": cid}


@router.get("/progress")
def backup_progress_all(_user: TokenUser) -> dict:
    return {"ok": True, "items": prog.list_jobs()}


@router.get("/progress/{cid}")
def backup_progress(cid: int, _user: TokenUser) -> dict:
    item = prog.get_job(cid)
    return {"ok": True, "item": item}


@router.post("/{bid}/retry-remote")
def retry_remote(bid: int, _user: CurrentUser, db: DbSess) -> dict:
    row = db.query(BackupRecord).filter(BackupRecord.id == bid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    if (row.status or "") != "success":
        raise HTTPException(status_code=400, detail="只有备份成功的记录才能重新归档")
    conn = db.query(DbConnection).filter(DbConnection.id == row.connection_id).one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    if not conn.remote_enabled or not conn.remote_target_id:
        raise HTTPException(status_code=400, detail="该连接未开启群晖归档")
    try:
        log.info("[backups] 重试归档 bid=%s cid=%s", bid, conn.id)
        remote = upload_backup_to_remote(db, conn, row, retain_days=0, delete_old=False)
        row.remote_status = "success"
        row.remote_path = remote or row.remote_path or ""
        row.remote_error = ""
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("[backups] 重试归档失败 bid=%s: %s", bid, e)
        row.remote_status = "failed"
        row.remote_error = str(e)[:500]
        db.commit()
        raise HTTPException(status_code=400, detail=str(e)[:500]) from e
    return {"ok": True, "remote_path": row.remote_path}


@router.get("/{bid}/download")
def download(bid: int, _user: CurrentUser, db: DbSess) -> FileResponse:
    row = db.query(BackupRecord).filter(BackupRecord.id == bid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    path = (row.local_path or "").strip() or (row.file_path or "").strip()
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="文件不在本机，无法下载。请使用 SSH 模式备份或把备份目录挂到本机。")
    return FileResponse(path, filename=Path(path).name, media_type="application/octet-stream")


@router.delete("/{bid}")
def delete_backup(bid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(BackupRecord).filter(BackupRecord.id == bid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    for p in (row.local_path, row.file_path):
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db.delete(row)
    db.commit()
    return {"ok": True}
