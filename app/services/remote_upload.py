"""本地备份完成后，把 .bak 上传到已配置的群晖。"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import DbConnection, RemoteTarget
from app.security import decrypt_secret
from app.services.backup import (
    _is_windows_path,
    _sftp_download,
    _sql_connect,
    _sql_tunnel,
    _ssh_client,
    _tunnel_sql_host,
)
from app.services.synology import test_synology, upload_to_synology

log = logging.getLogger(__name__)

_MAX_SQL_FETCH = 512 * 1024 * 1024


def _safe_folder(name: str) -> str:
    """群晖目录名：去掉路径分隔符，保留点号（连接名如 UNIQUE_192.168.1.3）。"""
    s = (name or "").strip().replace("\\", "_").replace("/", "_")
    return s.strip(" .")


def probe_remote_target(
    host: str,
    port: int,
    username: str,
    password: str,
    https: bool,
    remote_dir: str,
) -> str:
    return test_synology(host, port, username, password, https, remote_dir)


def upload_backup_to_remote(db: Session, conn_row: DbConnection, rec: Any) -> str:
    if not conn_row.remote_enabled or not conn_row.remote_target_id:
        return ""
    target = db.query(RemoteTarget).filter(RemoteTarget.id == conn_row.remote_target_id).one_or_none()
    if not target:
        raise RuntimeError("未找到远程备份配置，请先在「远程备份配置」中添加群晖")
    password = decrypt_secret(target.password_enc)
    if not password:
        raise RuntimeError("群晖密码为空，请重新保存远程备份配置")
    filename = Path(rec.file_path or rec.local_path or "backup.bak").name
    day = ""
    src_path = rec.file_path or rec.local_path or ""
    parts = src_path.replace("/", "\\").split("\\")
    for p in parts:
        if len(p) == 10 and p[4] == "-" and p[7] == "-":
            day = p
            break
    dbname = (rec.dbname or "").strip() or "db"
    conn_name = _safe_folder(conn_row.name) or f"conn_{conn_row.id}"
    dest_parts = [conn_name, _safe_folder(dbname) or "db"]
    if day:
        dest_parts.append(day)
    dest_subdir = "/".join(dest_parts)
    log.info("[remote] 群晖目标目录 %s/%s", target.remote_dir.rstrip("/"), dest_subdir)
    staged: Path | None = None
    local = _existing_local(rec)
    try:
        if not local:
            staged = _stage_remote_file(conn_row, rec)
            local = staged
        if not local or not local.is_file():
            raise RuntimeError(
                f"本地备份已完成，但平台读不到文件 {src_path}，无法上传群晖。"
                "请确认平台能访问该备份盘，且群晖 File Station（HTTP 5000 / HTTPS 5001）网络互通。"
            )
        remote = upload_to_synology(
            host=target.host,
            port=int(target.port or 5001),
            username=target.username,
            password=password,
            https=bool(target.https),
            remote_dir=target.remote_dir,
            local_file=local,
            dest_subdir=dest_subdir,
            filename=filename,
        )
        log.info("[remote] 已上传群晖 %s", remote)
        return remote
    finally:
        if staged and staged.exists():
            try:
                staged.unlink()
            except OSError:
                pass


def _existing_local(rec: Any) -> Path | None:
    for p in (rec.local_path, rec.file_path):
        if p and os.path.isfile(p):
            return Path(p)
    return None


def _stage_remote_file(conn_row: DbConnection, rec: Any) -> Path | None:
    src = (rec.file_path or "").strip()
    if not src:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="sqlbak_")) / Path(src.replace("\\", "/")).name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    mode = (conn_row.connect_mode or "direct").lower()
    if mode == "ssh" and not _is_windows_path(src):
        ssh_password = decrypt_secret(conn_row.ssh_password_enc)
        ssh_host = conn_row.ssh_host or conn_row.host
        client = _ssh_client(ssh_host, conn_row.ssh_port, conn_row.ssh_user, ssh_password, conn_row.ssh_key or "")
        try:
            _sftp_download(client, src, tmp)
            return tmp
        except Exception as e:  # noqa: BLE001
            log.warning("[remote] SSH 拉取备份失败: %s", e)
            return None
        finally:
            client.close()
    if _is_windows_path(src):
        size = int(rec.file_size or 0)
        if 0 < size <= _MAX_SQL_FETCH:
            try:
                _fetch_via_sql(conn_row, src, tmp)
                return tmp
            except Exception as e:  # noqa: BLE001
                log.warning("[remote] 经 SQL 读取 bak 失败: %s", e)
        else:
            log.warning("[remote] 文件过大或未知大小 size=%s，跳过经 SQL 拉取", size)
    return None


def _fetch_via_sql(conn_row: DbConnection, windows_path: str, dest: Path) -> None:
    """OPENROWSET 在 SQL Server 本机读文件，经 TDS 传回平台（适合 SSH 隧道场景）。"""
    password = decrypt_secret(conn_row.password_enc)
    escaped = windows_path.replace("'", "''")
    sql = f"SELECT BulkColumn FROM OPENROWSET(BULK N'{escaped}', SINGLE_BLOB) AS x"
    log.info("[remote] 经 SQL 读取 %s", windows_path)
    mode = (conn_row.connect_mode or "direct").lower()
    if mode == "ssh":
        ssh_password = decrypt_secret(conn_row.ssh_password_enc)
        ssh_host = conn_row.ssh_host or conn_row.host
        client = _ssh_client(ssh_host, conn_row.ssh_port, conn_row.ssh_user, ssh_password, conn_row.ssh_key or "")
        try:
            dest_host = _tunnel_sql_host(ssh_host, conn_row.host)
            with _sql_tunnel(client, int(conn_row.port), dest_host) as local_port:
                dbc = _sql_connect(
                    "127.0.0.1",
                    local_port,
                    "master",
                    conn_row.username,
                    password,
                    timeout=600,
                    expect_sql_host=conn_row.host,
                )
                try:
                    _write_bulk(dbc, sql, dest)
                finally:
                    dbc.close()
        finally:
            client.close()
        return
    dbc = _sql_connect(
        conn_row.host,
        conn_row.port,
        "master",
        conn_row.username,
        password,
        timeout=600,
        expect_sql_host=conn_row.host,
    )
    try:
        _write_bulk(dbc, sql, dest)
    finally:
        dbc.close()


def _write_bulk(dbc: Any, sql: str, dest: Path) -> None:
    cur = dbc.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    if not row or row[0] is None:
        raise RuntimeError("OPENROWSET 未返回文件内容（可能未开启 Ad Hoc Distributed Queries）")
    dest.write_bytes(bytes(row[0]))
    log.info("[remote] 已经 SQL 拉取 %s 字节", dest.stat().st_size)
