from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, DbSess
from app.models import DbConnection, RemoteTarget, Schedule
from app.schemas import ConnectionIn, ConnectionOut, ConnectionProbeIn
from app.security import decrypt_secret, encrypt_secret
from app.services.backup import format_database_label, probe_instance, test_connection
from app.services import progress as prog
from app.services import scheduler as sched_svc

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _to_out(row: DbConnection, target_name: str = "") -> ConnectionOut:
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


def _apply(row: DbConnection, body: ConnectionIn, *, is_new: bool) -> None:
    row.name = body.name.strip()
    row.db_type = (body.db_type or "sqlserver").strip().lower()
    row.host = body.host.strip()
    row.port = int(body.port)
    row.database = (body.database or "").strip()
    row.username = body.username.strip()
    row.connect_mode = body.connect_mode
    row.backup_dir = (body.backup_dir or "").strip()
    row.ssh_host = (body.ssh_host or "").strip()
    row.ssh_port = int(body.ssh_port)
    row.ssh_user = (body.ssh_user or "").strip()
    row.ssh_key = body.ssh_key or ""
    row.remote_enabled = bool(body.remote_enabled)
    row.remote_target_id = int(body.remote_target_id or 0) if body.remote_enabled else 0
    if body.remote_enabled and not row.remote_target_id:
        raise HTTPException(status_code=400, detail="请选择远程备份（群晖）配置")
    if body.password:
        row.password_enc = encrypt_secret(body.password)
    elif is_new:
        raise HTTPException(status_code=400, detail="请填写数据库密码")
    if body.ssh_password:
        row.ssh_password_enc = encrypt_secret(body.ssh_password)


@router.get("")
def list_connections(_user: CurrentUser, db: DbSess) -> dict:
    rows = db.query(DbConnection).order_by(DbConnection.id.desc()).all()
    names = {t.id: t.name for t in db.query(RemoteTarget).all()}
    items = []
    for r in rows:
        item = _to_out(r, names.get(int(r.remote_target_id or 0), "")).model_dump()
        item["database_label"] = format_database_label(r.database)
        items.append(item)
    return {"ok": True, "items": items}


@router.post("")
def create_connection(_admin: AdminUser, body: ConnectionIn, db: DbSess) -> dict:
    row = DbConnection(password_enc="", ssh_password_enc="")
    _apply(row, body, is_new=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _to_out(row).model_dump()}


@router.put("/{cid}")
def update_connection(cid: int, _admin: AdminUser, body: ConnectionIn, db: DbSess) -> dict:
    row = db.query(DbConnection).filter(DbConnection.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="连接不存在")
    _apply(row, body, is_new=False)
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
    ssh_password = (body.ssh_password or "").strip()
    ssh_key = body.ssh_key or ""
    if body.id:
        row = db.query(DbConnection).filter(DbConnection.id == body.id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="连接不存在")
        if not password:
            password = decrypt_secret(row.password_enc)
        if not ssh_password:
            ssh_password = decrypt_secret(row.ssh_password_enc)
        if not (ssh_key or "").strip():
            ssh_key = row.ssh_key or ""
    if not password:
        raise HTTPException(status_code=400, detail="请填写数据库密码")
    try:
        msg, databases = probe_instance(
            host=body.host.strip(),
            port=int(body.port),
            username=body.username.strip(),
            password=password,
            connect_mode=body.connect_mode,
            ssh_host=(body.ssh_host or "").strip(),
            ssh_port=int(body.ssh_port),
            ssh_user=(body.ssh_user or "").strip(),
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
