"""FastAPI 入口：建表、默认管理员、调度器、静态前端。"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import inspect, text

from app.config import APP_VERSION, DEFAULT_ADMIN_PASS, DEFAULT_ADMIN_USER, FRONTEND_DIST, HOST, PORT, ensure_dirs, ensure_utf8_stdio
from app.db import Base, SessionLocal, engine
from app.models import User  # noqa: F401  # 导入以注册全部表（含 RemoteTarget）
from app.routers import auth, backups, connections, dashboard, notify, remote, schedules, users
from app.security import hash_password
from app.services import scheduler as sched_svc

ensure_utf8_stdio()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
    encoding="utf-8",
)
log = logging.getLogger(__name__)


def _migrate() -> None:
    """给已有 SQLite 表补列（create_all 不会 ALTER）。"""
    try:
        insp = inspect(engine)
        names = set(insp.get_table_names())

        def add_col(table: str, col: str, ddl: str) -> None:
            if table not in names:
                return
            cols = {c["name"] for c in insp.get_columns(table)}
            if col in cols:
                return
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            log.info("[boot] %s 已增加 %s 列", table, col)

        add_col("backup_records", "dbname", "dbname VARCHAR(128) NOT NULL DEFAULT ''")
        add_col("backup_records", "remote_path", "remote_path VARCHAR(1024) NOT NULL DEFAULT ''")
        add_col("backup_records", "remote_status", "remote_status VARCHAR(16) NOT NULL DEFAULT ''")
        add_col("backup_records", "remote_error", "remote_error TEXT NOT NULL DEFAULT ''")
        add_col("db_connections", "remote_enabled", "remote_enabled BOOLEAN NOT NULL DEFAULT 0")
        add_col("db_connections", "remote_target_id", "remote_target_id INTEGER NOT NULL DEFAULT 0")
        add_col("notify_config", "wecom_webhook", "wecom_webhook VARCHAR(1024) NOT NULL DEFAULT ''")
        add_col("notify_config", "dingtalk_webhook", "dingtalk_webhook VARCHAR(1024) NOT NULL DEFAULT ''")
        add_col(
            "notify_config",
            "notify_channel",
            "notify_channel VARCHAR(64) NOT NULL DEFAULT 'feishu'",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[boot] 结构迁移跳过: %s", e)


def _bootstrap() -> None:
    ensure_dirs()
    Base.metadata.create_all(bind=engine)
    _migrate()
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(
                User(
                    username=DEFAULT_ADMIN_USER,
                    password_hash=hash_password(DEFAULT_ADMIN_PASS),
                    role="admin",
                )
            )
            db.commit()
            log.info("[boot] 已创建默认管理员 %s", DEFAULT_ADMIN_USER)
        sched_svc.start(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap()
    yield
    if sched_svc.scheduler.running:
        sched_svc.scheduler.shutdown(wait=False)
        log.info("[boot] 调度器已停止")


app = FastAPI(title="SQL Backup", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(connections.router)
app.include_router(backups.router)
app.include_router(schedules.router)
app.include_router(users.router)
app.include_router(notify.router)
app.include_router(remote.router)
app.include_router(dashboard.router)

if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API 已由 router 处理；其余交给前端路由
        if full_path.startswith("api"):
            return {"ok": False, "detail": "接口不存在"}
        index = FRONTEND_DIST / "index.html"
        cand = FRONTEND_DIST / full_path
        if full_path and cand.is_file():
            return FileResponse(cand)
        if index.is_file():
            return FileResponse(index)
        return {"ok": True, "message": "前端尚未构建，请先在 frontend 目录执行 npm run build"}
else:

    @app.get("/")
    def root():
        return {
            "ok": True,
            "message": "SQL Backup API 已启动。前端未构建：cd frontend && npm install && npm run build",
            "login": "POST /api/auth/login",
        }


def run_server() -> None:
    import uvicorn

    log.info("SQL Backup v%s 启动 http://127.0.0.1:%s", APP_VERSION, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
