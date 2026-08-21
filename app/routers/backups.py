from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.db import SessionLocal
from app.deps import CurrentUser, DbSess, TokenUser
from app.models import BackupRecord, DbConnection
from app.schemas import BackupOut, BackupRunIn
from app.services import progress as prog
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
) -> dict:
    query = db.query(BackupRecord).order_by(BackupRecord.id.desc())
    if connection_id:
        query = query.filter(BackupRecord.connection_id == connection_id)
    if status:
        query = query.filter(BackupRecord.status == status.strip())
    rows = query.limit(500).all()
    conn_map = {c.id: c for c in db.query(DbConnection).all()}
    items = []
    needle = (q or "").strip().lower()
    for r in rows:
        c = conn_map.get(r.connection_id)
        item = _to_out(r, c)
        if needle:
            blob = " ".join(
                [
                    item.connection_name,
                    item.database,
                    item.file_path,
                    item.status,
                    item.backup_type,
                    item.error_message,
                ]
            ).lower()
            if needle not in blob:
                continue
        items.append(item.model_dump())
    return {"ok": True, "items": items}


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
