from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import User
from app.schemas import UserCreate, UserOut
from app.security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/roles")
def roles(_user: CurrentUser) -> dict:
    return {"ok": True, "items": ["admin", "operator"]}


@router.get("")
def list_users(_admin: AdminUser, db: DbSess) -> dict:
    rows = db.query(User).order_by(User.id).all()
    return {"ok": True, "items": [UserOut.model_validate(r).model_dump() for r in rows]}


@router.post("")
def create_user(_admin: AdminUser, body: UserCreate, db: DbSess) -> dict:
    exists = db.query(User).filter(User.username == body.username.strip()).one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    row = User(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": UserOut.model_validate(row).model_dump()}


@router.delete("/{uid}")
def delete_user(uid: int, admin: AdminUser, db: DbSess) -> dict:
    if uid == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    row = db.query(User).filter(User.id == uid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
