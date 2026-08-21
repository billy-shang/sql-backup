from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from app.deps import CurrentUser, DbSess
from app.models import BackupRecord, DbConnection, Schedule

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
log = logging.getLogger(__name__)


@router.get("")
def dashboard(_user: CurrentUser, db: DbSess) -> dict:
    conn_n = db.query(DbConnection).count()
    sch_n = db.query(Schedule).count()
    ok_n = db.query(BackupRecord).filter(BackupRecord.status == "success").count()
    fail_n = db.query(BackupRecord).filter(BackupRecord.status == "failed").count()
    run_n = db.query(BackupRecord).filter(BackupRecord.status == "running").count()
    recent = (
        db.query(BackupRecord).order_by(BackupRecord.id.desc()).limit(8).all()
    )
    cmap = {c.id: c for c in db.query(DbConnection).all()}
    items = []
    for r in recent:
        c = cmap.get(r.connection_id)
        items.append(
            {
                "id": r.id,
                "database": (r.dbname or "").strip() or (c.database if c else ""),
                "name": c.name if c else "",
                "status": r.status,
                "backup_type": r.backup_type,
                "started_at": r.started_at.isoformat() if r.started_at else "",
                "file_size": r.file_size,
            }
        )
    return {
        "ok": True,
        "stats": {
            "connections": conn_n,
            "schedules": sch_n,
            "success": ok_n,
            "failed": fail_n,
            "running": run_n,
        },
        "recent": items,
    }


@router.delete("/logs")
def clear_logs(_user: CurrentUser, db: DbSess) -> dict:
    """清空概览备份日志：删除已结束的备份记录，不删数据库服务器上的 .bak。"""
    rows = db.query(BackupRecord).filter(BackupRecord.status != "running").all()
    deleted = 0
    for row in rows:
        for p in (row.local_path, row.file_path):
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError as e:
                    log.warning("[dashboard] 删除本机文件失败 %s: %s", p, e)
        db.delete(row)
        deleted += 1
    db.commit()
    log.info("[dashboard] 已清空概览日志 %s 条", deleted)
    return {"ok": True, "deleted": deleted, "message": f"已清空 {deleted} 条备份日志"}
