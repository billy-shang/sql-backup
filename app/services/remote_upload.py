"""本地备份完成后，把 .bak 上传到已配置的群晖。"""
from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import DbConnection, RemoteTarget
from app.security import decrypt_secret
from app.services.backup import (
    _is_windows_path,
    _sftp_download,
    _sql_temp_enable,
    open_sql_session,
    xp_cmdshell_lines,
)
from app.services.synology import cleanup_synology_days, test_synology, upload_to_synology

log = logging.getLogger(__name__)

# 整包 OPENROWSET 只用于小文件，避免 SQL / 容器内存被撑爆
_MAX_SQL_BLOB = 32 * 1024 * 1024
_CHUNK = 256 * 1024


def _safe_folder(name: str) -> str:
    """群晖目录名：去掉路径分隔符，保留点号（连接名如 UNIQUE_192.168.1.3）。"""
    s = (name or "").strip().replace("\\", "_").replace("/", "_")
    return s.strip(" .")


def _file_basename(path: str) -> str:
    """Windows 路径在 Linux 容器里 Path.name 会整段当文件名，必须先把反斜杠换成 /。"""
    s = str(path or "").replace("\\", "/").rstrip("/")
    name = s.split("/")[-1].strip() if s else ""
    return name or "backup.bak"


def probe_remote_target(
    host: str,
    port: int,
    username: str,
    password: str,
    https: bool,
    remote_dir: str,
) -> str:
    return test_synology(host, port, username, password, https, remote_dir)


def upload_backup_to_remote(
    db: Session,
    conn_row: DbConnection,
    rec: Any,
    *,
    retain_days: int = 0,
    delete_old: bool = False,
) -> str:
    if not conn_row.remote_enabled or not conn_row.remote_target_id:
        return ""
    target = db.query(RemoteTarget).filter(RemoteTarget.id == conn_row.remote_target_id).one_or_none()
    if not target:
        raise RuntimeError("未找到远程备份配置，请先在「远程备份配置」中添加群晖")
    password = decrypt_secret(target.password_enc)
    if not password:
        raise RuntimeError("群晖密码为空，请重新保存远程备份配置")
    filename = _file_basename(rec.file_path or rec.local_path or "backup.bak")
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
    syno = {
        "host": target.host,
        "port": int(target.port or 5001),
        "username": target.username,
        "password": password,
        "https": bool(target.https),
        "remote_dir": target.remote_dir,
    }
    log.info("[remote] 群晖目标目录 %s/%s", syno["remote_dir"].rstrip("/"), dest_subdir)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
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
            host=syno["host"],
            port=syno["port"],
            username=syno["username"],
            password=syno["password"],
            https=syno["https"],
            remote_dir=syno["remote_dir"],
            local_file=local,
            dest_subdir=dest_subdir,
            filename=filename,
        )
        log.info("[remote] 已上传群晖 %s", remote)
        if delete_old and retain_days > 0:
            parent = "/".join(dest_parts[:-1] if day else dest_parts)
            try:
                cleanup_synology_days(
                    host=syno["host"],
                    port=syno["port"],
                    username=syno["username"],
                    password=syno["password"],
                    https=syno["https"],
                    remote_dir=syno["remote_dir"],
                    dest_subdir=parent,
                    retain_days=retain_days,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("[remote] 群晖清理过期目录失败: %s", e)
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
    tmp = Path(tempfile.mkdtemp(prefix="sqlbak_")) / _file_basename(src)
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
        try:
            _fetch_via_sql(conn_row, src, tmp, size)
            return tmp
        except Exception as e:  # noqa: BLE001
            log.warning("[remote] 经 SQL 读取 bak 失败: %s", e)
    return None


def _fetch_via_sql(conn_row: DbConnection, windows_path: str, dest: Path, size: int = 0) -> None:
    """从 SQL Server 本机读 .bak：小文件走 OPENROWSET，大文件分块走 xp_cmdshell。"""
    log.info("[remote] 经 SQL 读取 %s size=%s", windows_path, size)
    with open_sql_session(conn_row, timeout=8 * 3600) as dbc:
        if 0 < size <= _MAX_SQL_BLOB:
            try:
                _write_bulk(dbc, windows_path, dest)
                return
            except Exception as e:  # noqa: BLE001
                log.info("[remote] OPENROWSET 失败，改分块读取: %s", e)
        _write_chunked(dbc, windows_path, dest, size)


def _write_bulk(dbc: Any, windows_path: str, dest: Path) -> None:
    escaped = windows_path.replace("'", "''")
    sql = f"SELECT BulkColumn FROM OPENROWSET(BULK N'{escaped}', SINGLE_BLOB) AS x"
    cur = dbc.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    if not row or row[0] is None:
        raise RuntimeError("OPENROWSET 未返回文件内容（可能未开启 Ad Hoc Distributed Queries）")
    dest.write_bytes(bytes(row[0]))
    log.info("[remote] 已经 SQL 整包拉取 %s 字节", dest.stat().st_size)


def _write_chunked(dbc: Any, windows_path: str, dest: Path, size: int) -> None:
    """临时打开 xp_cmdshell，用 PowerShell 按块读文件。跳板机看不到 G: 盘。"""
    with _sql_temp_enable(dbc, "xp_cmdshell") as ok:
        if not ok:
            raise RuntimeError(
                f"文件约 {size} 字节，整包拉取不安全，且无法临时开启 xp_cmdshell 分块读取。"
                "请确认 SQL 账号是 sysadmin。"
            )
        actual = size if size > 0 else _sql_file_length(dbc, windows_path)
        if actual <= 0:
            raise RuntimeError(f"无法取得备份文件大小：{windows_path}")
        log.info("[remote] 分块拉取 %s 共 %s 字节，块=%s", windows_path, actual, _CHUNK)
        wrote = 0
        with dest.open("wb") as fh:
            while wrote < actual:
                chunk = _sql_read_chunk(dbc, windows_path, wrote, min(_CHUNK, actual - wrote))
                if not chunk:
                    break
                fh.write(chunk)
                wrote += len(chunk)
                if wrote == actual or len(chunk) < _CHUNK:
                    if wrote % (8 * _CHUNK) < _CHUNK:
                        log.info("[remote] 分块进度 %s / %s", wrote, actual)
        if wrote <= 0:
            raise RuntimeError("分块读取未得到数据")
        log.info("[remote] 分块拉取完成 %s 字节", wrote)


def _sql_file_length(dbc: Any, windows_path: str) -> int:
    p = windows_path.replace("'", "''")
    cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        f"\"$ProgressPreference='SilentlyContinue';(Get-Item -LiteralPath '{p}').Length\""
    )
    for line in xp_cmdshell_lines(dbc, cmd):
        text = line.strip()
        if text.isdigit():
            return int(text)
    return 0


def _sql_read_chunk(dbc: Any, windows_path: str, offset: int, length: int) -> bytes:
    p = windows_path.replace("'", "''")
    cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        "\"$ProgressPreference='SilentlyContinue';"
        f"$fs=[IO.File]::OpenRead('{p}');$fs.Position={int(offset)};"
        f"$b=New-Object byte[] {int(length)};$r=$fs.Read($b,0,{int(length)});$fs.Close();"
        "$s=[Convert]::ToBase64String($b,0,$r);"
        "for($i=0;$i -lt $s.Length;$i+=240){$s.Substring($i,[Math]::Min(240,$s.Length-$i))}\""
    )
    parts = [ln.strip() for ln in xp_cmdshell_lines(dbc, cmd) if re.fullmatch(r"[A-Za-z0-9+/=]+", ln.strip())]
    if not parts:
        return b""
    return base64.b64decode("".join(parts))
