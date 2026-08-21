"""连接级备份进度：按已完成库数计算，当前库内用时间缓升，避免假进度卡在 92%。"""
from __future__ import annotations

import math
import threading
import time
from typing import Any

_lock = threading.Lock()
_jobs: dict[int, dict[str, Any]] = {}


def is_running(cid: int) -> bool:
    with _lock:
        job = _jobs.get(int(cid))
        return bool(job and job.get("status") == "running")


def start_job(cid: int, total: int = 1) -> bool:
    cid = int(cid)
    with _lock:
        job = _jobs.get(cid)
        if job and job.get("status") == "running":
            return False
        now = time.time()
        _jobs[cid] = {
            "connection_id": cid,
            "status": "running",
            "total": max(int(total or 1), 1),
            "done": 0,
            "current_db": "",
            "percent": 1,
            "message": "正在准备备份",
            "started_at": now,
            "db_started_at": now,
        }
        return True


def set_total(cid: int, total: int, current_db: str = "") -> None:
    cid = int(cid)
    with _lock:
        job = _jobs.get(cid)
        if not job or job.get("status") != "running":
            return
        job["total"] = max(int(total or 1), 1)
        if current_db:
            job["current_db"] = current_db
            job["db_started_at"] = time.time()
            job["message"] = f"正在备份 {current_db}（1/{job['total']}）"


def mark_db_start(cid: int, name: str, index: int, total: int) -> None:
    cid = int(cid)
    total = max(int(total or 1), 1)
    with _lock:
        job = _jobs.get(cid)
        if not job or job.get("status") != "running":
            return
        job["total"] = total
        job["done"] = max(int(index), 0)
        job["current_db"] = name or ""
        job["db_started_at"] = time.time()
        job["message"] = f"正在备份 {name}（{index + 1}/{total}）"


def mark_db_done(cid: int, name: str, done: int, total: int) -> None:
    cid = int(cid)
    total = max(int(total or 1), 1)
    done = max(int(done), 0)
    with _lock:
        job = _jobs.get(cid)
        if not job or job.get("status") != "running":
            return
        job["total"] = total
        job["done"] = min(done, total)
        job["current_db"] = name or job.get("current_db") or ""
        job["db_started_at"] = time.time()
        if done >= total:
            job["percent"] = 99
            job["message"] = "正在收尾"
        else:
            job["message"] = f"已完成 {done}/{total}"


def finish(cid: int, status: str, message: str) -> None:
    cid = int(cid)
    with _lock:
        job = _jobs.get(cid) or {"connection_id": cid, "total": 1, "done": 0, "current_db": ""}
        job["status"] = "success" if status == "success" else "failed"
        job["percent"] = 100 if job["status"] == "success" else max(int(job.get("percent") or 0), 8)
        job["message"] = message or ("备份完成" if job["status"] == "success" else "备份失败")
        job["finished_at"] = time.time()
        _jobs[cid] = job


def get_job(cid: int) -> dict[str, Any] | None:
    cid = int(cid)
    with _lock:
        job = _jobs.get(cid)
        if not job:
            return None
        out = dict(job)
    if out.get("status") == "running":
        out["percent"] = _live_percent(out)
    return out


def _live_percent(job: dict[str, Any]) -> int:
    total = max(int(job.get("total") or 1), 1)
    done = min(max(int(job.get("done") or 0), 0), total)
    if done >= total:
        return 99
    floor = done / total * 100.0
    span = 100.0 / total
    elapsed = max(time.time() - float(job.get("db_started_at") or job.get("started_at") or time.time()), 0.0)
    # 当前库：8% 起步，随时间逼近本段 82%，不会提前顶到 92% 后假死
    frac = 0.08 + 0.74 * (1.0 - math.exp(-elapsed / 70.0))
    percent = floor + span * frac
    return max(1, min(99, int(percent)))
