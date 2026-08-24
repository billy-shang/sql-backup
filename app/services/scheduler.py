"""APScheduler：每天 / 每周 / 指定时间。"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Schedule
from app.services.runner import execute_schedule_job

log = logging.getLogger(__name__)
TZ = "Asia/Shanghai"
_TZINFO = ZoneInfo(TZ)
_CATCHUP_HOURS = 36

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


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TZINFO)
    return dt.astimezone(_TZINFO)


def last_expected_run(sch: Schedule, now: datetime) -> datetime | None:
    """上一次按计划应该触发的时间（上海时区）。"""
    now = _aware(now) or datetime.now(_TZINFO)
    kind = (sch.schedule_type or "daily").lower()
    if kind == "once":
        return _aware(sch.once_at)
    hour, minute = _parse_hm(sch.run_time)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "weekly":
        wd = int(sch.weekday or 0)
        delta = (candidate.weekday() - wd) % 7
        if delta == 0 and candidate > now:
            delta = 7
        return candidate - timedelta(days=delta)
    if candidate <= now:
        return candidate
    return candidate - timedelta(days=1)


def next_expected_run(sch: Schedule, now: datetime | None = None) -> datetime | None:
    """下一次按计划应该触发的时间（上海时区）。暂停或过期的一次性任务返回空。"""
    now = _aware(now) or datetime.now(_TZINFO)
    if not sch.enabled:
        return None
    kind = (sch.schedule_type or "daily").lower()
    if kind == "once":
        when = _aware(sch.once_at)
        return when if when and when > now else None
    last = last_expected_run(sch, now)
    if last is None:
        return None
    if last > now:
        return last
    step = timedelta(days=7) if kind == "weekly" else timedelta(days=1)
    return last + step


def _should_catch_up(sch: Schedule, now: datetime) -> datetime | None:
    expected = last_expected_run(sch, now)
    if expected is None:
        return None
    if expected > now:
        return None
    age = now - expected
    if age <= timedelta(0) or age > timedelta(hours=_CATCHUP_HOURS):
        return None
    last = _aware(sch.last_run_at)
    if last and last >= expected - timedelta(minutes=2):
        return None
    return expected


def catch_up_missed(db: Session) -> int:
    """启动后补跑最近 36 小时内错过的定时任务，避免容器重启当天漏备。"""
    now = datetime.now(_TZINFO)
    rows = db.query(Schedule).filter(Schedule.enabled.is_(True)).order_by(Schedule.id).all()
    by_conn: dict[int, tuple[int, datetime]] = {}
    for sch in rows:
        expected = _should_catch_up(sch, now)
        if expected is None:
            continue
        prev = by_conn.get(int(sch.connection_id))
        if prev is None or expected < prev[1]:
            by_conn[int(sch.connection_id)] = (sch.id, expected)
            log.info("[scheduler] 任务 #%s 错过 %s，将补跑", sch.id, expected.strftime("%Y-%m-%d %H:%M"))
    todo = list(by_conn.values())
    for sid, expected in todo:
        try:
            execute_schedule_job(sid)
            log.info("[scheduler] 已补跑任务 #%s（原计划 %s）", sid, expected.strftime("%Y-%m-%d %H:%M"))
        except Exception as e:  # noqa: BLE001
            log.warning("[scheduler] 补跑任务 #%s 失败: %s", sid, e)
    if todo:
        log.info("[scheduler] 补跑结束，共 %s 条", len(todo))
    else:
        log.info("[scheduler] 没有需要补跑的定时任务")
    return len(todo)


def _catch_up_async() -> None:
    time.sleep(5)
    db = SessionLocal()
    try:
        catch_up_missed(db)
    except Exception as e:  # noqa: BLE001
        log.warning("[scheduler] 补跑检查失败: %s", e)
    finally:
        db.close()


def start(db: Session) -> None:
    if not scheduler.running:
        scheduler.start()
        log.info("[scheduler] 调度器已启动")
    reload_all(db)
    threading.Thread(target=_catch_up_async, name="schedule-catchup", daemon=True).start()
