"""APScheduler：每天 / 每周 / 指定时间。"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

from app.models import Schedule
from app.services.runner import execute_schedule_job

log = logging.getLogger(__name__)
TZ = "Asia/Shanghai"

scheduler = BackgroundScheduler(timezone=TZ)


def _job_id(schedule_id: int) -> str:
    return f"backup_schedule_{schedule_id}"


def _parse_hm(run_time: str) -> tuple[int, int]:
    raw = (run_time or "02:00").strip()
    parts = raw.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return 2, 0
    return max(0, min(23, h)), max(0, min(59, m))


def upsert_job(sch: Schedule) -> None:
    jid = _job_id(sch.id)
    try:
        scheduler.remove_job(jid)
    except Exception:  # noqa: BLE001
        pass
    if not sch.enabled:
        log.info("[scheduler] 任务 #%s 已暂停，不注册", sch.id)
        return
    hour, minute = _parse_hm(sch.run_time)
    kind = (sch.schedule_type or "daily").lower()
    if kind == "once":
        when = sch.once_at
        if when is None:
            log.warning("[scheduler] 一次性任务 #%s 未设置 once_at", sch.id)
            return
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.now().astimezone().tzinfo)
        if when < datetime.now(when.tzinfo):
            log.info("[scheduler] 一次性任务 #%s 时间已过，跳过", sch.id)
            return
        trigger = DateTrigger(run_date=when, timezone=TZ)
    elif kind == "weekly":
        # APScheduler: mon=0 ... sun=6 与我们 weekday 一致
        trigger = CronTrigger(day_of_week=int(sch.weekday or 0), hour=hour, minute=minute, timezone=TZ)
    else:
        trigger = CronTrigger(hour=hour, minute=minute, timezone=TZ)
    scheduler.add_job(
        execute_schedule_job,
        trigger=trigger,
        id=jid,
        args=[sch.id],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("[scheduler] 已注册任务 #%s type=%s time=%s", sch.id, kind, sch.run_time)


def remove_job(schedule_id: int) -> None:
    try:
        scheduler.remove_job(_job_id(schedule_id))
        log.info("[scheduler] 已移除任务 #%s", schedule_id)
    except Exception:  # noqa: BLE001
        pass


def reload_all(db: Session) -> None:
    rows = db.query(Schedule).all()
    for sch in rows:
        upsert_job(sch)
    log.info("[scheduler] 已加载 %s 条定时任务", len(rows))


def start(db: Session) -> None:
    if not scheduler.running:
        scheduler.start()
        log.info("[scheduler] 调度器已启动")
    reload_all(db)
