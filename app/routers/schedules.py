from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import DbConnection, Schedule
from app.schemas import ScheduleIn, ScheduleOut
from app.services import progress as prog
from app.services import scheduler as sched_svc
from app.services.backup import format_database_label
from app.services.runner import execute_schedule_job, reconcile_stale_running

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _to_out(row: Schedule, conn: DbConnection | None) -> ScheduleOut:
    status = row.last_status or ""
    if not row.enabled:
        status = "paused"
    elif prog.is_running(int(row.connection_id)):
        status = "running"
    return ScheduleOut(
        id=row.id,
        name=row.name,
        connection_id=row.connection_id,
        connection_name=(conn.name if conn else ""),
        database=format_database_label(conn.database) if conn else "",
        schedule_type=row.schedule_type,
        run_time=row.run_time,
        weekday=row.weekday,
        once_at=row.once_at,
        backup_type=row.backup_type,
        retain_days=row.retain_days,
        compress=row.compress,
        delete_old=row.delete_old,
        enabled=row.enabled,
        last_status=status,
        last_run_at=row.last_run_at,
        next_run_at=sched_svc.next_expected_run(row),
        last_error=row.last_error or "",
        created_at=row.created_at,
    )


def _apply(row: Schedule, body: ScheduleIn) -> None:
    row.name = body.name.strip()
    row.connection_id = int(body.connection_id)
    row.schedule_type = body.schedule_type
    row.run_time = body.run_time.strip() or "02:00"
    row.weekday = int(body.weekday)
    row.once_at = body.once_at
    row.backup_type = body.backup_type
    row.retain_days = int(body.retain_days)
    row.compress = bool(body.compress)
    row.delete_old = bool(body.delete_old)
    row.enabled = bool(body.enabled)


@router.get("")
def list_schedules(_user: CurrentUser, db: DbSess) -> dict:
    reconcile_stale_running(db)
    rows = db.query(Schedule).order_by(Schedule.id.desc()).all()
    cmap = {c.id: c for c in db.query(DbConnection).all()}
    return {"ok": True, "items": [_to_out(r, cmap.get(r.connection_id)).model_dump() for r in rows]}


@router.post("")
def create_schedule(_admin: AdminUser, body: ScheduleIn, db: DbSess) -> dict:
    conn = db.query(DbConnection).filter(DbConnection.id == body.connection_id).one_or_none()
    if not conn:
        raise HTTPException(status_code=400, detail="数据库连接不存在")
    row = Schedule()
    _apply(row, body)
    if not row.enabled:
        row.last_status = "paused"
    db.add(row)
    db.commit()
    db.refresh(row)
    sched_svc.upsert_job(row)
    return {"ok": True, "item": _to_out(row, conn).model_dump()}


@router.put("/{sid}")
def update_schedule(sid: int, _admin: AdminUser, body: ScheduleIn, db: DbSess) -> dict:
    row = db.query(Schedule).filter(Schedule.id == sid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    conn = db.query(DbConnection).filter(DbConnection.id == body.connection_id).one_or_none()
    if not conn:
        raise HTTPException(status_code=400, detail="数据库连接不存在")
    _apply(row, body)
    if not row.enabled:
        row.last_status = "paused"
    db.commit()
    db.refresh(row)
    sched_svc.upsert_job(row)
    return {"ok": True, "item": _to_out(row, conn).model_dump()}


@router.delete("/{sid}")
def delete_schedule(sid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(Schedule).filter(Schedule.id == sid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    sched_svc.remove_job(sid)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/{sid}/pause")
def pause_schedule(sid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(Schedule).filter(Schedule.id == sid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    row.enabled = False
    row.last_status = "paused"
    db.commit()
    db.refresh(row)
    sched_svc.upsert_job(row)
    return {"ok": True}


@router.post("/{sid}/run")
def run_schedule_now(sid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(Schedule).filter(Schedule.id == sid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    cid = int(row.connection_id)
    if prog.is_running(cid):
        raise HTTPException(status_code=409, detail="该连接正在备份或恢复，请稍后再试")
    row.last_status = "running"
    row.last_error = ""
    db.commit()
    threading.Thread(target=execute_schedule_job, args=(sid,), kwargs={"ignore_enabled": True}, daemon=True).start()
    log.info("[schedules] 手动执行任务 #%s cid=%s", sid, cid)
    return {"ok": True, "started": True, "connection_id": cid}


@router.post("/{sid}/resume")
def resume_schedule(sid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(Schedule).filter(Schedule.id == sid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    row.enabled = True
    if row.last_status == "paused":
        row.last_status = ""
    db.commit()
    db.refresh(row)
    sched_svc.upsert_job(row)
    return {"ok": True}
