from __future__ import annotations

from app.config import ensure_utf8_stdio

ensure_utf8_stdio()

from app.main import run_server

if __name__ == "__main__":
    run_server()
