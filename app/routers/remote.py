from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import DbConnection, RemoteTarget
from app.schemas import RemoteTargetIn, RemoteTargetOut
from app.security import decrypt_secret, encrypt_secret
from app.services.remote_upload import probe_remote_target

router = APIRouter(prefix="/api/remote-targets", tags=["remote-targets"])


def _to_out(row: RemoteTarget) -> RemoteTargetOut:
    return RemoteTargetOut(
        id=row.id,
        name=row.name,
        host=row.host,
        port=row.port,
        https=bool(row.https),
        username=row.username,
        has_password=bool(row.password_enc),
        remote_dir=row.remote_dir,
        created_at=row.created_at,
    )


def _apply(row: RemoteTarget, body: RemoteTargetIn, *, is_new: bool) -> None:
    row.name = body.name.strip()
    row.host = body.host.strip()
    row.port = int(body.port)
    row.https = bool(body.https)
    row.username = body.username.strip()
    row.remote_dir = (body.remote_dir or "/sql_backup").strip() or "/sql_backup"
    if body.password:
        row.password_enc = encrypt_secret(body.password)
    elif is_new:
        raise HTTPException(status_code=400, detail="请填写群晖密码")


@router.get("")
def list_targets(_user: CurrentUser, db: DbSess) -> dict:
    rows = db.query(RemoteTarget).order_by(RemoteTarget.id.desc()).all()
    return {"ok": True, "items": [_to_out(r).model_dump() for r in rows]}


@router.post("")
def create_target(_admin: AdminUser, body: RemoteTargetIn, db: DbSess) -> dict:
    row = RemoteTarget(password_enc="")
    _apply(row, body, is_new=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.put("/{tid}")
def update_target(tid: int, _admin: AdminUser, body: RemoteTargetIn, db: DbSess) -> dict:
    row = db.query(RemoteTarget).filter(RemoteTarget.id == tid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="群晖备份配置不存在")
    _apply(row, body, is_new=False)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.delete("/{tid}")
def delete_target(tid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(RemoteTarget).filter(RemoteTarget.id == tid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="群晖备份配置不存在")
    for conn in db.query(DbConnection).filter(DbConnection.remote_target_id == tid).all():
        conn.remote_target_id = 0
        conn.remote_enabled = False
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/probe")
def probe_target(body: RemoteTargetIn, _admin: AdminUser, db: DbSess) -> dict:
    password = (body.password or "").strip()
    if body.host and not password:
        # 编辑时允许用已存密码测试：用名称+地址匹配最近一条
        row = (
            db.query(RemoteTarget)
            .filter(RemoteTarget.host == body.host.strip(), RemoteTarget.username == body.username.strip())
            .order_by(RemoteTarget.id.desc())
            .first()
        )
        if row:
            password = decrypt_secret(row.password_enc)
    if not password:
        raise HTTPException(status_code=400, detail="请填写群晖密码")
    try:
        msg = probe_remote_target(
            body.host.strip(),
            int(body.port),
            body.username.strip(),
            password,
            bool(body.https),
            body.remote_dir,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)[:800]) from e
    return {"ok": True, "message": msg}
