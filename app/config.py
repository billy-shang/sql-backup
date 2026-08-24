"""运行配置：端口、JWT、数据目录、默认管理员。"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def ensure_utf8_stdio() -> None:
    """Windows 控制台默认 GBK，中文日志会显示成乱码；统一改为 UTF-8。"""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
            if enc != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


ensure_utf8_stdio()

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """持久化目录：容器里映射到 /data，本机默认项目下 data/。"""
    raw = (os.environ.get("SQL_BACKUP_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return BASE_DIR / "data"


DATA_DIR = _data_dir()
BACKUP_STORE = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "sql_backup.db"
SECRET_FILE = DATA_DIR / "secret.key"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

HOST = os.environ.get("SQL_BACKUP_HOST", "0.0.0.0")
PORT = int(os.environ.get("SQL_BACKUP_PORT", "8788"))
JWT_EXPIRE_HOURS = int(os.environ.get("SQL_BACKUP_JWT_HOURS", "12"))

DEFAULT_ADMIN_USER = os.environ.get("SQL_BACKUP_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASS = os.environ.get("SQL_BACKUP_ADMIN_PASS", "admin@123")
APP_VERSION = os.environ.get("SQL_BACKUP_VERSION", "1.0.38")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_STORE.mkdir(parents=True, exist_ok=True)
    log.info("[config] 数据目录 %s", DATA_DIR)
