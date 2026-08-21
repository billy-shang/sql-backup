from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import CurrentUser, DbSess
from app.models import User
from app.schemas import LoginBody, PasswordBody, UserOut
from app.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginBody, db: DbSess) -> dict:
    user = db.query(User).filter(User.username == body.username.strip()).one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    token = create_token(user.username, user.role)
    return {"ok": True, "token": token, "user": UserOut.model_validate(user).model_dump()}


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {"ok": True, "user": UserOut.model_validate(user).model_dump()}


@router.post("/password")
def change_password(body: PasswordBody, user: CurrentUser, db: DbSess) -> dict:
    fresh = db.query(User).filter(User.id == user.id).one()
    if not verify_password(body.old_password, fresh.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    fresh.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True, "message": "密码已更新"}
