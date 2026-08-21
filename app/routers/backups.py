from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import BackupRecord, DbConnection
from app.schemas import BackupOut, BackupRunIn
from app.services.runner import execute_backup

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


@router.post("/run/{cid}")
def run_now(cid: int, body: BackupRunIn, _user: CurrentUser, db: DbSess) -> dict:
    conn = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    recs = execute_backup(
        db,
        cid,
        backup_type=body.backup_type,
        compress=body.compress,
        retain_days=body.retain_days,
        delete_old=body.delete_old,
        trigger="manual",
    )
    if not recs:
        raise HTTPException(status_code=400, detail="没有可备份的数据库")
    failed = [r for r in recs if r.status == "failed"]
    ok_recs = [r for r in recs if r.status == "success"]
    if not ok_recs:
        raise HTTPException(status_code=400, detail=failed[0].error_message or "备份失败")
    msg = f"已在数据库服务器 {conn.host} 生成 {len(ok_recs)} 个文件（请打开子目录，不在备份目录根下）："
    msg += "；".join((r.file_path or r.dbname) for r in ok_recs)
    if failed:
        msg += "；失败：" + "、".join(f"{r.dbname}" for r in failed)
    return {
        "ok": True,
        "message": msg,
        "items": [_to_out(r, conn).model_dump() for r in recs],
    }


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
