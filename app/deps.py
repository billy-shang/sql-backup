"""登录用户依赖与角色校验。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import decode_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        payload = decode_token(cred.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from None
    username = str(payload.get("sub") or "")
    user = db.query(User).filter(User.username == username).one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
DbSess = Annotated[Session, Depends(get_db)]
