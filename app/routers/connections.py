from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import DbConnection, RemoteTarget, Schedule, SshProxy
from app.schemas import ConnectionIn, ConnectionOut, ConnectionProbeIn
from app.security import decrypt_secret, encrypt_secret
from app.services.backup import format_database_label, probe_instance, test_connection
from app.services.ssh_util import ssh_params
from app.services import progress as prog
from app.services import scheduler as sched_svc

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _to_out(row: DbConnection, target_name: str = "", proxy_name: str = "") -> ConnectionOut:
    return ConnectionOut(
        id=row.id,
        name=row.name,
        db_type=row.db_type,
        host=row.host,
        port=row.port,
        database=row.database or "",
        username=row.username,
        has_password=bool(row.password_enc),
        connect_mode=row.connect_mode,
        backup_dir=row.backup_dir,
        ssh_proxy_id=int(row.ssh_proxy_id or 0),
        ssh_proxy_name=proxy_name,
        ssh_host=row.ssh_host,
        ssh_port=row.ssh_port,
        ssh_user=row.ssh_user,
        has_ssh_password=bool(row.ssh_password_enc),
        has_ssh_key=bool((row.ssh_key or "").strip()),
        remote_enabled=bool(row.remote_enabled),
        remote_target_id=int(row.remote_target_id or 0),
        remote_target_name=target_name,
        created_at=row.created_at,
    )


def _copy_proxy(row: DbConnection, proxy: SshProxy) -> None:
    row.ssh_host = proxy.host
    row.ssh_port = int(proxy.port)
    row.ssh_user = proxy.username
    row.ssh_password_enc = proxy.password_enc
    row.ssh_key = proxy.key or ""


def _apply(row: DbConnection, body: ConnectionIn, db, *, is_new: bool) -> None:
    row.name = body.name.strip()
    row.db_type = (body.db_type or "sqlserver").strip().lower()
    row.host = body.host.strip()
    row.port = int(body.port)
    row.database = (body.database or "").strip()
    row.username = body.username.strip()
    row.connect_mode = body.connect_mode
    row.backup_dir = (body.backup_dir or "").strip()
    row.remote_enabled = bool(body.remote_enabled)
    row.remote_target_id = int(body.remote_target_id or 0) if body.remote_enabled else 0
    if body.remote_enabled and not row.remote_target_id:
        raise HTTPException(status_code=400, detail="请选择远程备份（群晖）配置")
    if body.password:
        row.password_enc = encrypt_secret(body.password)
    elif is_new:
        raise HTTPException(status_code=400, detail="请填写数据库密码")
    if body.connect_mode == "ssh":
        pid = int(body.ssh_proxy_id or 0)
        if not pid:
            raise HTTPException(status_code=400, detail="请选择 SSH 代理")
        proxy = db.query(SshProxy).filter(SshProxy.id == pid).one_or_none()
        if not proxy:
            raise HTTPException(status_code=400, detail="SSH 代理不存在，请到「配置中心」添加")
        row.ssh_proxy_id = pid
        _copy_proxy(row, proxy)
    else:
        row.ssh_proxy_id = 0
        row.ssh_host = ""
        row.ssh_port = 22
        row.ssh_user = ""
        row.ssh_password_enc = ""
        row.ssh_key = ""


@router.get("")
def list_connections(_user: CurrentUser, db: DbSess) -> dict:
    rows = db.query(DbConnection).order_by(DbConnection.id.desc()).all()
    names = {t.id: t.name for t in db.query(RemoteTarget).all()}
    proxies = {p.id: p.name for p in db.query(SshProxy).all()}
    items = []
    for r in rows:
        item = _to_out(
            r,
            names.get(int(r.remote_target_id or 0), ""),
            proxies.get(int(r.ssh_proxy_id or 0), ""),
        ).model_dump()
        item["database_label"] = format_database_label(r.database)
        items.append(item)
    return {"ok": True, "items": items}


@router.post("")
def create_connection(_admin: AdminUser, body: ConnectionIn, db: DbSess) -> dict:
    row = DbConnection(password_enc="", ssh_password_enc="")
    _apply(row, body, db, is_new=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.put("/{cid}")
def update_connection(cid: int, _admin: AdminUser, body: ConnectionIn, db: DbSess) -> dict:
    row = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="连接不存在")
    _apply(row, body, db, is_new=False)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.delete("/{cid}")
def delete_connection(cid: int, _admin: AdminUser, db: DbSess) -> dict:
    row = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="连接不存在")
    if prog.is_running(cid):
        raise HTTPException(status_code=409, detail="该连接正在备份或恢复，请稍后再试")
    for sch in db.query(Schedule).filter(Schedule.connection_id == cid).all():
        sched_svc.remove_job(sch.id)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/probe")
def probe_conn(body: ConnectionProbeIn, _user: CurrentUser, db: DbSess) -> dict:
    """新增/编辑弹窗内测试：连通后返回全部数据库（含系统库标记）。"""
    password = (body.password or "").strip()
    ssh_host = (body.ssh_host or "").strip()
    ssh_port = int(body.ssh_port)
    ssh_user = (body.ssh_user or "").strip()
    ssh_password = (body.ssh_password or "").strip()
    ssh_key = body.ssh_key or ""
    row = None
    if body.id:
        row = db.query(DbConnection).filter(DbConnection.id == body.id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")
        if not password:
            password = decrypt_secret(row.password_enc)
    if body.connect_mode == "ssh":
        pid = int(body.ssh_proxy_id or 0) or int(getattr(row, "ssh_proxy_id", 0) or 0)
        if pid:
            proxy = db.query(SshProxy).filter(SshProxy.id == pid).one_or_none()
            if not proxy:
                raise HTTPException(status_code=400, detail="SSH 代理不存在，请到「配置中心」重新选择")
            ssh_host = proxy.host
            ssh_port = int(proxy.port)
            ssh_user = proxy.username
            ssh_password = decrypt_secret(proxy.password_enc)
            ssh_key = proxy.key or ""
        elif row is not None:
            params = ssh_params(row, db)
            ssh_host, ssh_port, ssh_user, ssh_password, ssh_key = (
                params.host,
                params.port,
                params.user,
                params.password,
                params.key,
            )
        if not ssh_user or not ssh_host:
            raise HTTPException(status_code=400, detail="请选择 SSH 代理")
    if not password:
        raise HTTPException(status_code=400, detail="请填写数据库密码")
    try:
        msg, databases = probe_instance(
            host=body.host.strip(),
            port=int(body.port),
            username=body.username.strip(),
            password=password,
            connect_mode=body.connect_mode,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
        )
        return {"ok": True, "message": msg, "databases": databases}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"测试失败：{e}") from e


@router.post("/{cid}/test")
def test_conn(cid: int, _user: CurrentUser, db: DbSess) -> dict:
    row = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="连接不存在")
    try:
        msg = test_connection(row)
        return {"ok": True, "message": msg}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"测试失败：{e}") from e
