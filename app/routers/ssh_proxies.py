from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import DbConnection, SshProxy
from app.schemas import SshProxyIn, SshProxyOut
from app.security import decrypt_secret, encrypt_secret
from app.services.backup import test_ssh

router = APIRouter(prefix="/api/ssh-proxies", tags=["ssh-proxies"])


def _to_out(row: SshProxy) -> SshProxyOut:
    return SshProxyOut(
        id=row.id,
        name=row.name,
        host=row.host,
        port=row.port,
        username=row.username,
        has_password=bool(row.password_enc),
        has_key=bool((row.key or "").strip()),
        created_at=row.created_at,
    )


def _apply(row: SshProxy, body: SshProxyIn, *, is_new: bool) -> None:
    row.name = body.name.strip()
    row.host = body.host.strip()
    row.port = int(body.port)
    row.username = body.username.strip()
    if body.password:
        row.password_enc = encrypt_secret(body.password)
    # 新增写入密钥；编辑时密钥留空表示沿用
    if is_new or (body.key or "").strip():
        row.key = body.key or ""
    if not row.password_enc and not (row.key or "").strip():
        raise HTTPException(status_code=400, detail="请填写 SSH 密码或私钥")


def _sync_connection_snapshot(db, proxy: SshProxy) -> None:
    """连接上保留一份快照，旧逻辑读字段时也能工作。"""
    for conn in db.query(DbConnection).filter(DbConnection.ssh_proxy_id == proxy.id).all():
        conn.ssh_host = proxy.host
        conn.ssh_port = proxy.port
        conn.ssh_user = proxy.username
        conn.ssh_password_enc = proxy.password_enc
        conn.ssh_key = proxy.key or ""


@router.get("")
def list_proxies(_user: CurrentUser, db: DbSess) -> dict:
    rows = db.query(SshProxy).order_by(SshProxy.id.desc()).all()
    return {"ok": True, "items": [_to_out(r).model_dump() for r in rows]}


@router.post("")
def create_proxy(_admin: AdminUser, body: SshProxyIn, db: DbSess) -> dict:
    row = SshProxy(password_enc="", key="")
    _apply(row, body, is_new=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.put("/{pid}")
def update_proxy(pid: int, _admin: AdminUser, body: SshProxyIn, db: DbSess) -> dict:
    row = db.query(SshProxy).filter(SshProxy.id == pid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="SSH 代理不存在")
    _apply(row, body, is_new=False)
    _sync_connection_snapshot(db, row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.delete("/{pid}")
def delete_proxy(pid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(SshProxy).filter(SshProxy.id == pid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="SSH 代理不存在")
    used = db.query(DbConnection).filter(DbConnection.ssh_proxy_id == pid).all()
    if used:
        names = "、".join(c.name for c in used[:5])
        extra = " 等" if len(used) > 5 else ""
        raise HTTPException(status_code=400, detail=f"仍有连接在使用该代理：{names}{extra}，请先改连接")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/probe")
def probe_proxy(body: SshProxyIn, _admin: AdminUser, db: DbSess) -> dict:
    password = (body.password or "").strip()
    key = body.key or ""
    if body.host and not password and not (key or "").strip():
        row = (
            db.query(SshProxy)
            .filter(SshProxy.host == body.host.strip(), SshProxy.username == body.username.strip())
            .order_by(SshProxy.id.desc())
            .first()
        )
        if row:
            password = decrypt_secret(row.password_enc)
            if not (key or "").strip():
                key = row.key or ""
    if not password and not (key or "").strip():
        raise HTTPException(status_code=400, detail="请填写 SSH 密码或私钥")
    try:
        msg = test_ssh(body.host.strip(), int(body.port), body.username.strip(), password, key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)[:800]) from e
    return {"ok": True, "message": msg}
