"""登录用户依赖与角色校验。"""
from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import decode_token

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


def _username_from_token(cred: HTTPAuthorizationCredentials | None) -> str:
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        payload = decode_token(cred.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from None
    username = str(payload.get("sub") or "")
    if not username:
        raise HTTPException(status_code=401, detail="请先登录")
    return username


def get_login_name(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """只校验 JWT，不访问 SQLite。进度轮询等高频接口使用，避免备份写入时 database is locked。"""
    return _username_from_token(cred)


def get_current_user(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    username = _username_from_token(cred)
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            user = db.query(User).filter(User.username == username).one_or_none()
            if not user:
                raise HTTPException(status_code=401, detail="用户不存在")
            return user
        except OperationalError as e:
            last_err = e
            if "locked" not in str(e).lower() or attempt == 5:
                raise
            log.warning("[auth] SQLite 忙，重试 %s/6", attempt + 1)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.08 * (attempt + 1))
    raise last_err  # type: ignore[misc]


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
TokenUser = Annotated[str, Depends(get_login_name)]
DbSess = Annotated[Session, Depends(get_db)]
