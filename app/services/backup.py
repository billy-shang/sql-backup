"""SQL Server 备份：平台可部署在任意位置，BACKUP 始终在数据库服务器本机落盘。"""
from __future__ import annotations

import io
import logging
import os
import posixpath
import socket
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import paramiko

from app.config import BACKUP_STORE
from app.security import decrypt_secret

log = logging.getLogger(__name__)

_ODBC_DRIVERS = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server Native Client 10.0",
    "SQL Native Client",
    "SQL Server",
]

# SQL Server 系统库（列出时标注，默认可不备份）
_SYSTEM_DBS = {"master", "tempdb", "model", "msdb"}
_LIST_DB_SQL = (
    "SELECT name FROM sys.databases "
    "WHERE state = 0 "
    "ORDER BY CASE WHEN database_id <= 4 THEN 0 ELSE 1 END, name"
)


def parse_selected_databases(raw: str) -> list[str]:
    """空字符串表示全部用户库；否则按逗号/分号拆成已勾选的库名。"""
    names: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def format_database_label(raw: str) -> str:
    names = parse_selected_databases(raw)
    return "全部用户数据库" if not names else "、".join(names)


def is_system_db(name: str) -> bool:
    return (name or "").strip().lower() in _SYSTEM_DBS


def _to_db_items(names: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in names:
        name = (n or "").strip()
        if not name:
            continue
        low = name.lower()
        if low in seen:
            continue
        if low in {"name", "is_system"} or "rows affected" in low:
            continue
        seen.add(low)
        items.append({"name": name, "is_system": low in _SYSTEM_DBS})
    items.sort(key=lambda x: (not x["is_system"], str(x["name"]).lower()))
    return items


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _stamp(dt: datetime | None = None) -> tuple[str, str, str]:
    d = dt or _now()
    return d.strftime("%Y-%m-%d"), d.strftime("%Y%m%d_%H%M%S"), d.strftime("%Y-%m-%d %H:%M")


def _is_windows_path(p: str) -> bool:
    s = (p or "").strip()
    return len(s) >= 2 and s[1] == ":"


def _local_file_just_written(path: str, within_sec: int = 180) -> bool:
    """本机磁盘上刚出现该路径，说明 BACKUP 写到了管理平台这台电脑。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    return (time.time() - mtime) < within_sec


def _join_backup_path(root: str, database: str, day: str, filename: str) -> str:
    root = (root or "").rstrip("/\\")
    if _is_windows_path(root):
        return f"{root}\\{database}\\{day}\\{filename}"
    return posixpath.join(root, database, day, filename)


def _dir_of(path: str) -> str:
    if _is_windows_path(path):
        return path.rsplit("\\", 1)[0]
    return posixpath.dirname(path)


class _TunnelConnectError(RuntimeError):
    """SSH 隧道或数据库连不上。"""


class _DbConn:
    """统一 pyodbc / python-tds 的 cursor 用法。"""

    def __init__(self, raw: Any, kind: str) -> None:
        self._raw = raw
        self.kind = kind

    def cursor(self) -> "_DbCursor":
        return _DbCursor(self._raw.cursor(), self.kind)

    def close(self) -> None:
        self._raw.close()


class _DbCursor:
    def __init__(self, cur: Any, kind: str) -> None:
        self._cur = cur
        self.kind = kind
        self.messages = getattr(cur, "messages", None)

    def execute(self, sql: str, *args: Any):
        params: Any = None
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            params = tuple(args[0])
        elif args:
            params = args
        if self.kind == "tds":
            query = sql.replace("?", "%s") if params else sql
            return self._cur.execute(query, params)
        if params is None:
            return self._cur.execute(sql)
        if len(params) == 1:
            return self._cur.execute(sql, params[0])
        return self._cur.execute(sql, params)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def nextset(self):
        fn = getattr(self._cur, "nextset", None)
        if not fn:
            return False
        return fn()


def _query_instance_info(conn: Any) -> dict[str, Any]:
    """判断 Windows/Linux、默认备份目录、是否支持压缩。"""
    info: dict[str, Any] = {
        "is_windows": True,
        "default_backup_dir": "",
        "version": "",
        "compress_ok": True,
    }
    cur = conn.cursor()
    try:
        cur.execute("SELECT CAST(@@VERSION AS NVARCHAR(2048))")
        row = cur.fetchone()
        ver = str(row[0] or "") if row else ""
        info["version"] = ver
        vlow = ver.lower()
        info["is_windows"] = "linux" not in vlow
        if "express" in vlow:
            info["compress_ok"] = False
        elif "sql server 2008" in vlow or "10.0." in vlow or "10.50." in vlow:
            info["compress_ok"] = any(x in vlow for x in ("enterprise", "developer", "evaluation"))
        log.info("[backup] SQL 版本摘要: %s", ver.splitlines()[0] if ver else "")
    except Exception as e:  # noqa: BLE001
        log.warning("[backup] 读取 @@VERSION 失败: %s", e)
    try:
        cur.execute("SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS NVARCHAR(4000))")
        row = cur.fetchone()
        if row and row[0]:
            info["default_backup_dir"] = str(row[0]).rstrip("\\/")
    except Exception:  # noqa: BLE001
        pass
    if not info["default_backup_dir"]:
        try:
            cur.execute(
                "EXEC master.dbo.xp_instance_regread "
                "N'HKEY_LOCAL_MACHINE', "
                "N'Software\\Microsoft\\MSSQLServer\\MSSQLServer', "
                "N'BackupDirectory'"
            )
            row = cur.fetchone()
            if row:
                data = row[1] if len(row) > 1 else row[0]
                if data:
                    info["default_backup_dir"] = str(data).rstrip("\\/")
        except Exception as e:  # noqa: BLE001
            log.warning("[backup] 读取默认备份目录失败: %s", e)
    log.info("[backup] 默认备份目录=%s windows=%s compress_ok=%s", info["default_backup_dir"], info["is_windows"], info["compress_ok"])
    return info


def _resolve_backup_root(configured: str, info: dict[str, Any]) -> str:
    """备份目录必须是 SQL Server 本机路径；Windows 填 D:\\TEST，Linux 填 /backup/sqlserver。"""
    configured = (configured or "").strip()
    default_dir = str(info.get("default_backup_dir") or "").strip()
    if info.get("is_windows"):
        if not configured:
            if default_dir:
                log.info("[backup] 未填备份目录，使用实例默认目录 %s", default_dir)
                return default_dir.rstrip("\\/")
            raise RuntimeError("请填写数据库服务器本机备份目录，例如 D:\\TEST")
        if _is_windows_path(configured):
            root = configured.rstrip("\\/")
            log.info("[backup] 使用 SQL Server 本机目录 %s", root)
            return root
        raise RuntimeError(
            "当前 SQL Server 是 Windows，备份会写到数据库服务器本机。"
            f"请填写该机本地路径，例如 D:\\TEST，不要填写 {configured}（那不是 Windows 路径，也不是 SSH 跳板机目录）。"
        )
    if _is_windows_path(configured):
        raise RuntimeError(
            "当前 SQL Server 是 Linux，请填写该机本地路径，例如 /backup/sqlserver。"
        )
    return (configured or "/var/opt/mssql/data").rstrip("/\\")


def _backup_via_odbc(
    dbc: Any,
    *,
    configured_dir: str,
    dbname: str,
    day: str,
    filename: str,
    backup_type: str,
    compress: bool,
) -> dict[str, Any]:
    info = _query_instance_info(dbc)
    use_compress = bool(compress) and bool(info.get("compress_ok"))
    if compress and not use_compress:
        log.info("[backup] 当前实例不支持备份压缩，已改为不压缩")
    root = _resolve_backup_root(configured_dir, info)
    remote_path = _join_backup_path(root, dbname, day, filename)
    remote_dir = _dir_of(remote_path)
    sql = backup_sql(backup_type, dbname, remote_path.replace("'", "''"), use_compress)
    log.info("[backup] 实际备份路径 %s", remote_path)
    started = _now()
    _run_backup_sql(dbc, sql, remote_dir)
    size = _assert_backup_written(dbc, dbname, remote_path, started)
    return {
        "file_path": remote_path,
        "remote_dir": remote_dir,
        "file_size": size,
        "is_windows": bool(info.get("is_windows")),
        "root": root,
    }


def backup_sql(backup_type: str, dbname: str, disk: str, compress: bool) -> str:
    bt = (backup_type or "full").lower()
    name = dbname.replace("]", "]]")
    opts = ["INIT", "NAME = N'sql_backup'"]
    if compress:
        opts.insert(0, "COMPRESSION")
    extra = ", ".join(opts)
    if bt == "diff":
        return f"BACKUP DATABASE [{name}] TO DISK = N'{disk}' WITH DIFFERENTIAL, {extra}"
    if bt == "log":
        return f"BACKUP LOG [{name}] TO DISK = N'{disk}' WITH {extra}"
    return f"BACKUP DATABASE [{name}] TO DISK = N'{disk}' WITH {extra}"


def _sql_connect(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    timeout: int = 12,
    expect_sql_host: str | None = None,
):
    """连接目标 SQL Server。优先现代 ODBC，没有驱动时用纯 Python TDS（便于任意机器部署）。"""
    last: Exception | None = None
    try:
        raw = _pyodbc_connect(host, port, database, user, password, timeout, expect_sql_host)
        log.info("[backup] 使用 ODBC 连接 %s:%s", host, port)
        return _DbConn(raw, "odbc")
    except Exception as e:  # noqa: BLE001
        last = e
        log.info("[backup] ODBC 不可用，改用纯 Python TDS: %s", e)
    try:
        raw = _tds_connect(host, port, database, user, password, timeout)
        wrapped = _DbConn(raw, "tds")
        try:
            _assert_expected_sql_host(wrapped, expect_sql_host)
        except Exception:
            try:
                raw.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        log.info("[backup] 使用纯 Python TDS 连接 %s:%s", host, port)
        return wrapped
    except Exception as e:  # noqa: BLE001
        last = e
    target = expect_sql_host or host
    raise RuntimeError(f"无法连接 SQL Server {target}:{port}：{last}")


def _tds_connect(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    timeout: int = 12,
):
    """不依赖本机 ODBC，经 TCP 连到指定 host:port（含 SSH 隧道端口）。"""
    import pytds
    from pytds import tds_base

    last: Exception | None = None
    versions = [tds_base.TDS74, tds_base.TDS73A, tds_base.TDS72]
    login_timeout = min(int(timeout or 15), 60)
    stmt_timeout = int(timeout or 0)
    for ver in versions:
        try:
            log.info("[backup] 尝试 TDS %s SERVER=%s,%s", hex(ver), host, port)
            return pytds.connect(
                server=host,
                port=int(port),
                database=(database or "master"),
                user=user,
                password=password,
                autocommit=True,
                login_timeout=login_timeout,
                timeout=stmt_timeout,
                tds_version=ver,
                cafile=None,
                validate_host=False,
                enc_login_only=True,
            )
        except Exception as e:  # noqa: BLE001
            last = e
            log.info("[backup] TDS %s 失败: %s", hex(ver), e)
    raise RuntimeError(f"纯 Python TDS 连接失败：{last}")


def _pyodbc_connect(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    timeout: int = 12,
    expect_sql_host: str | None = None,
):
    """仅使用本机已安装的现代 ODBC 驱动；旧 MDAC「SQL Server」驱动不走自定义端口。"""
    try:
        import pyodbc
    except ImportError as e:
        raise RuntimeError("未安装 pyodbc") from e

    last: Exception | None = None
    servers = [f"{host},{int(port)}", f"tcp:{host},{int(port)}"]
    loopback = {"127.0.0.1", "localhost", "::1"}
    host_l = (host or "").strip().lower()
    try:
        installed = set(pyodbc.drivers())
    except Exception:  # noqa: BLE001
        installed = set()
    for drv in _ODBC_DRIVERS:
        if drv not in installed:
            continue
        # MDAC「SQL Server」驱动会忽略 127.0.0.1 的自定义端口，改连本机 1433
        if drv == "SQL Server" and host_l in loopback and int(port) != 1433:
            last = RuntimeError("旧版「SQL Server」ODBC 驱动无法走 SSH 隧道端口")
            log.warning("[backup] 跳过旧驱动 SQL Server：隧道端口 %s 会被忽略", port)
            continue
        for server in servers:
            extras = ["Connection Timeout={}".format(min(int(timeout), 60))]
            if "ODBC Driver" in drv or "Native Client" in drv:
                extras.append("TrustServerCertificate=yes")
            if drv == "SQL Server":
                extras.append("Network=DBMSSOCN")
            conn_str = (
                f"DRIVER={{{drv}}};SERVER={server};DATABASE={database};"
                f"UID={user};PWD={password};" + ";".join(extras) + ";"
            )
            try:
                log.info("[backup] 尝试 ODBC 驱动 %s SERVER=%s", drv, server)
                conn = pyodbc.connect(conn_str, timeout=timeout, autocommit=True)
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("[backup] 驱动 %s 失败: %s", drv, e)
                continue
            try:
                _assert_expected_sql_host(_DbConn(conn, "odbc"), expect_sql_host)
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("[backup] 连上的不是目标实例，关闭: %s", e)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                continue
            return conn
    raise RuntimeError(f"本机无可用 ODBC 驱动或未能连上目标实例：{last}")


def _sql_connection_info(conn: Any) -> dict[str, str]:
    info = {"machine": "", "netbios": "", "protocol": "", "listen": "", "client": ""}
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(128)), "
            "CAST(SERVERPROPERTY('ComputerNamePhysicalNetBIOS') AS NVARCHAR(128))"
        )
        row = cur.fetchone()
        if row:
            info["machine"] = str(row[0] or "").strip()
            info["netbios"] = str(row[1] or "").strip()
    except Exception as e:  # noqa: BLE001
        log.info("[backup] 读取机器名失败: %s", e)
    try:
        cur.execute(
            "SELECT protocol_type, local_net_address, client_net_address, local_tcp_port "
            "FROM sys.dm_exec_connections WHERE session_id = @@SPID"
        )
        row = cur.fetchone()
        if row:
            info["protocol"] = str(row[0] or "").strip()
            info["listen"] = str(row[1] or "").strip()
            info["client"] = str(row[2] or "").strip()
            info["port"] = str(row[3] or "").strip()
    except Exception as e:  # noqa: BLE001
        log.info("[backup] 读取连接端点失败: %s", e)
    return info


def _assert_expected_sql_host(conn: Any, expect_sql_host: str | None) -> None:
    ident = _sql_connection_info(conn)
    log.info(
        "[backup] SQL 身份 machine=%s netbios=%s protocol=%s listen=%s client=%s",
        ident.get("machine"),
        ident.get("netbios"),
        ident.get("protocol"),
        ident.get("listen"),
        ident.get("client"),
    )
    expect = (expect_sql_host or "").strip().lower()
    if not expect or expect in {"127.0.0.1", "localhost", "::1"}:
        return
    proto = (ident.get("protocol") or "").lower()
    listen = (ident.get("listen") or "").lower()
    if "shared memory" in proto or "named pipe" in proto:
        raise RuntimeError(
            "连到了本机 SQL Server（共享内存/命名管道），没有连上目标数据库服务器。"
        )
    if listen in {"127.0.0.1", "::1"} and expect not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            f"连到了 127.0.0.1，而目标是 {expect_sql_host}，备份会写到本机而不是数据库服务器。"
        )
    if (
        listen
        and listen not in {"0.0.0.0", "::", "127.0.0.1", "::1"}
        and "." in expect
        and listen != expect
    ):
        raise RuntimeError(
            f"连到了 {listen}，而目标是 {expect_sql_host}，备份不会写到目标数据库服务器。"
        )


def test_direct(host: str, port: int, database: str, user: str, password: str) -> str:
    # 未指定库时连 master，用于「全部数据库」场景
    db = (database or "").strip() or "master"
    conn = _sql_connect(host, port, db, user, password, expect_sql_host=host)
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return "直连成功"
    finally:
        conn.close()


def _list_dbs_direct(
    host: str,
    port: int,
    user: str,
    password: str,
    expect_sql_host: str | None = None,
) -> list[dict[str, Any]]:
    conn = _sql_connect(host, port, "master", user, password, expect_sql_host=expect_sql_host)
    try:
        cur = conn.cursor()
        cur.execute(_LIST_DB_SQL)
        rows = cur.fetchall() or []
        return _to_db_items([str(r[0]) for r in rows if r and r[0]])
    finally:
        conn.close()


def _list_dbs_over_ssh(
    client: paramiko.SSHClient,
    sql_port: int,
    sql_user: str,
    sql_password: str,
    sql_host: str = "127.0.0.1",
) -> list[dict[str, Any]]:
    """优先 SSH 隧道连目标 SQL；连不上再找远端 sqlcmd 列库。"""
    last_err: Exception | None = None
    dest = sql_host or "127.0.0.1"
    try:
        with _sql_tunnel(client, sql_port, dest) as local_port:
            log.info("[backup] 经 SSH 隧道列库 127.0.0.1:%s -> %s:%s", local_port, dest, sql_port)
            return _list_dbs_direct(
                "127.0.0.1", local_port, sql_user, sql_password, expect_sql_host=dest
            )
    except Exception as e:  # noqa: BLE001
        last_err = e
        log.info("[backup] SSH 隧道列库失败: %s", e)

    sqlcmd = _find_sqlcmd(client)
    if not sqlcmd:
        raise RuntimeError(
            "SSH 已连通，但无法列出数据库。"
            "远端没有 sqlcmd，且未能通过隧道连接 SQL Server。"
            "可改用「直连」模式，或在服务器安装 mssql-tools（/opt/mssql-tools*/bin/sqlcmd）。"
            f" 隧道原因：{last_err}"
        )
    q = _LIST_DB_SQL.replace('"', "'")
    cmd = (
        f'{_quote_cmd(sqlcmd)} -S {dest},{int(sql_port)} -U "{sql_user}" '
        f'-P "{sql_password}" -d master -h -1 -W -Q "SET NOCOUNT ON; {q}"'
    )
    code, out, err = _ssh_exec(client, cmd, timeout=60)
    if code != 0:
        raise RuntimeError((err or out or f"sqlcmd 退出码 {code}").strip()[:2000])
    names = []
    for line in (out or "").splitlines():
        name = line.strip()
        if name and "changed database" not in name.lower():
            names.append(name)
    return _to_db_items(names)


def _list_dbs_ssh(
    ssh_host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_password: str,
    ssh_key: str,
    sql_port: int,
    sql_user: str,
    sql_password: str,
    sql_host: str = "",
) -> list[dict[str, Any]]:
    client = _ssh_client(ssh_host, ssh_port, ssh_user, ssh_password, ssh_key)
    try:
        dest = _tunnel_sql_host(ssh_host, sql_host)
        return _list_dbs_over_ssh(client, sql_port, sql_user, sql_password, dest)
    finally:
        client.close()


def probe_instance(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    connect_mode: str,
    ssh_host: str = "",
    ssh_port: int = 22,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """测试连通并列出全部数据库（含系统库标记）。"""
    mode = (connect_mode or "direct").lower()
    if mode == "ssh":
        ssh_target = (ssh_host or host or "").strip()
        test_ssh(ssh_target, ssh_port, ssh_user, ssh_password, ssh_key)
        dbs = _list_dbs_ssh(
            ssh_target,
            ssh_port,
            ssh_user,
            ssh_password,
            ssh_key,
            port,
            username,
            password,
            sql_host=host,
        )
        sys_n = sum(1 for x in dbs if x.get("is_system"))
        return f"SSH 连接成功，发现 {len(dbs)} 个数据库（系统库 {sys_n} 个）", dbs
    test_direct(host, port, "master", username, password)
    dbs = _list_dbs_direct(host, port, username, password, expect_sql_host=host)
    sys_n = sum(1 for x in dbs if x.get("is_system"))
    return f"直连成功，发现 {len(dbs)} 个数据库（系统库 {sys_n} 个）", dbs


def list_database_items(conn_row: Any) -> list[dict[str, Any]]:
    password = decrypt_secret(conn_row.password_enc)
    mode = (conn_row.connect_mode or "direct").lower()
    if mode == "ssh":
        ssh_password = decrypt_secret(conn_row.ssh_password_enc)
        ssh_host = conn_row.ssh_host or conn_row.host
        return _list_dbs_ssh(
            ssh_host,
            conn_row.ssh_port,
            conn_row.ssh_user,
            ssh_password,
            conn_row.ssh_key or "",
            conn_row.port,
            conn_row.username,
            password,
            sql_host=conn_row.host,
        )
    return _list_dbs_direct(
        conn_row.host, conn_row.port, conn_row.username, password, expect_sql_host=conn_row.host
    )


def list_databases_for_row(conn_row: Any) -> list[str]:
    """未勾选时只返回用户库名（不含系统库）。"""
    return [str(x["name"]) for x in list_database_items(conn_row) if not x.get("is_system")]


def resolve_backup_databases(conn_row: Any) -> list[str]:
    """空配置=备份全部用户库；已勾选则按勾选（可含系统库）。"""
    selected = parse_selected_databases(getattr(conn_row, "database", "") or "")
    if selected:
        return selected
    names = list_databases_for_row(conn_row)
    if not names:
        raise RuntimeError("未发现可备份的用户数据库")
    return names


def test_ssh(host: str, port: int, user: str, password: str, key_text: str) -> str:
    client = _ssh_client(host, port, user, password, key_text)
    try:
        _, stdout, stderr = client.exec_command("echo ok", timeout=15)
        out = (stdout.read() or b"").decode("utf-8", errors="ignore").strip()
        err = (stderr.read() or b"").decode("utf-8", errors="ignore").strip()
        if out:
            return f"SSH 成功：{out}"
        raise RuntimeError(err or "SSH 无输出")
    finally:
        client.close()


def _ssh_client(host: str, port: int, user: str, password: str, key_text: str) -> paramiko.SSHClient:
    if not host or not user:
        raise RuntimeError("SSH 模式请填写 SSH 主机与用户名")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict[str, Any] = {
        "hostname": host,
        "port": int(port or 22),
        "username": user,
        "timeout": 20,
        "allow_agent": False,
        "look_for_keys": False,
    }
    key_text = (key_text or "").strip()
    if key_text:
        pkey = None
        last: Exception | None = None
        for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                pkey = cls.from_private_key(io.StringIO(key_text))
                break
            except Exception as e:  # noqa: BLE001
                last = e
        if pkey is None:
            raise RuntimeError(f"无法解析 SSH 私钥: {last}")
        kwargs["pkey"] = pkey
    elif password:
        kwargs["password"] = password
    else:
        raise RuntimeError("请填写 SSH 密码或私钥")
    client.connect(**kwargs)
    return client


def _find_sqlcmd(client: paramiko.SSHClient) -> str:
    """在远端 PATH 和常见安装目录里找 sqlcmd（Linux / Windows）。"""
    script = (
        "set +e; "
        "for p in sqlcmd sqlcmd.exe "
        "/opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools17/bin/sqlcmd "
        "/opt/mssql-tools/bin/sqlcmd /usr/bin/sqlcmd /usr/local/bin/sqlcmd "
        '"/opt/mssql-tools18/bin/sqlcmd"; do '
        '  if command -v "$p" >/dev/null 2>&1; then command -v "$p"; exit 0; fi; '
        '  if [ -x "$p" ]; then echo "$p"; exit 0; fi; '
        "done; "
        "exit 1"
    )
    code, out, _err = _ssh_exec(client, script, timeout=20)
    path = (out or "").strip().splitlines()
    if code == 0 and path:
        found = path[-1].strip()
        log.info("[backup] 远端 sqlcmd: %s", found)
        return found
    log.info("[backup] 远端未找到 sqlcmd")
    return ""


class _SshTunnel:
    """本机 127.0.0.1:随机端口 -> 远端 127.0.0.1:sql_port。"""

    def __init__(self, transport: paramiko.Transport, remote_host: str, remote_port: int) -> None:
        self._transport = transport
        self._remote_host = remote_host or "127.0.0.1"
        self._remote_port = int(remote_port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.local_port = int(self._sock.getsockname()[1])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, name="sql-ssh-tunnel", daemon=True)
        self._thread.start()
        log.info("[backup] SSH 隧道 127.0.0.1:%s -> %s:%s", self.local_port, self._remote_host, self._remote_port)

    def _accept_loop(self) -> None:
        self._sock.settimeout(1.0)
        while not self._stop.is_set():
            try:
                src, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._pipe, args=(src,), daemon=True).start()

    def _pipe(self, src: socket.socket) -> None:
        try:
            chan = self._transport.open_channel(
                "direct-tcpip",
                (self._remote_host, self._remote_port),
                src.getpeername() or ("127.0.0.1", 0),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[backup] 打开隧道通道失败: %s", e)
            try:
                src.close()
            except OSError:
                pass
            return

        def one_way(a: Any, b: Any) -> None:
            try:
                while True:
                    data = a.recv(32768)
                    if not data:
                        break
                    if hasattr(b, "sendall"):
                        b.sendall(data)
                    else:
                        b.send(data)
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass

        t = threading.Thread(target=one_way, args=(src, chan), daemon=True)
        t.start()
        one_way(chan, src)
        t.join(timeout=2)

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@contextmanager
def _sql_tunnel(client: paramiko.SSHClient, sql_port: int, remote_host: str = "127.0.0.1"):
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        raise RuntimeError("SSH 未建立，无法开隧道")
    tun = _SshTunnel(transport, remote_host, sql_port)
    try:
        yield tun.local_port
    finally:
        tun.close()


def _tunnel_sql_host(ssh_host: str, sql_host: str) -> str:
    sql_host = (sql_host or "").strip()
    ssh_host = (ssh_host or "").strip()
    if sql_host and sql_host not in {ssh_host, "127.0.0.1", "localhost", "::1"}:
        return sql_host
    return "127.0.0.1"


def _quote_cmd(path: str) -> str:
    if any(ch in path for ch in (" ", "\t")):
        return f'"{path}"'
    return path


def _ssh_exec(client: paramiko.SSHClient, cmd: str, timeout: int = 3600) -> tuple[int, str, str]:
    log.info("[backup] SSH 执行: %s", cmd[:240])
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = (stdout.read() or b"").decode("utf-8", errors="ignore")
    err = (stderr.read() or b"").decode("utf-8", errors="ignore")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def _drain_cursor(cur: Any) -> None:
    try:
        while True:
            try:
                cur.fetchall()
            except Exception:  # noqa: BLE001
                pass
            if not cur.nextset():
                break
    except Exception:  # noqa: BLE001
        pass
    msgs = getattr(cur, "messages", None)
    if msgs:
        log.info("[backup] ODBC 消息: %s", msgs[:8])


def _sql_nv(s: str) -> str:
    return "N'" + (s or "").replace("'", "''") + "'"


def _dir_chain(path: str) -> list[str]:
    """从盘符/根目录起逐级列出要创建的文件夹。"""
    p = (path or "").replace("/", "\\").rstrip("\\")
    if len(p) >= 2 and p[1] == ":":
        acc = p[:2]
        rest = p[3:] if len(p) > 2 and p[2] == "\\" else p[2:].lstrip("\\")
        out: list[str] = []
        for part in rest.split("\\"):
            if not part:
                continue
            acc = acc + "\\" + part
            out.append(acc)
        return out
    unix = (path or "").replace("\\", "/").rstrip("/")
    acc = ""
    out = []
    for part in unix.split("/"):
        if not part:
            continue
        acc = acc + "/" + part
        out.append(acc)
    return out


def _xp_create_subdir(conn: Any, folder: str) -> None:
    cur = conn.cursor()
    # python-tds 对 EXEC ... ? 走 RPC，路径会报「参数无效」；改用 N'字面量'
    cur.execute(f"EXEC master.sys.xp_create_subdir {_sql_nv(folder)}")
    _drain_cursor(cur)


def _ensure_backup_dir(conn: Any, mkdir_path: str) -> None:
    log.info("[backup] 创建目录 %s", mkdir_path)
    last_err: Exception | None = None
    for folder in _dir_chain(mkdir_path):
        try:
            _xp_create_subdir(conn, folder)
            log.info("[backup] 已确保目录 %s", folder)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[backup] xp_create_subdir %s: %s", folder, e)
    _file, is_dir, parent = _xp_fileexist(conn, mkdir_path)
    if is_dir or parent:
        return
    hint = (
        f"SQL Server 本机找不到备份目录 {mkdir_path}。"
        "请到数据库服务器上先创建该路径（例如 D:\\sql_backup），"
        "并给 SQL Server 服务账号写入权限；"
        "登录名执行 xp_create_subdir 通常需要 sysadmin。"
    )
    if last_err:
        hint += f" 建目录失败：{last_err}"
    raise RuntimeError(hint)


def _run_backup_sql(conn: Any, sql: str, mkdir_path: str) -> None:
    _ensure_backup_dir(conn, mkdir_path)
    # python-tds 同一连接只能有一个活动游标；建目录已用过游标，BACKUP 必须换新的
    cur = conn.cursor()
    log.info("[backup] 执行备份 SQL: %s", sql[:300])
    cur.execute(sql)
    _drain_cursor(cur)
    log.info("[backup] BACKUP 语句已执行完毕")


def _xp_fileexist(conn: Any, path: str) -> tuple[int, int, int]:
    cur = conn.cursor()
    cur.execute(f"EXEC master.dbo.xp_fileexist {_sql_nv(path)}")
    row = cur.fetchone()
    if not row:
        return 0, 0, 0
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def _assert_backup_written(conn: Any, database: str, disk: str, started_at: Any) -> int:
    """确认 bak 真的写到指定路径，避免把 msdb 里旧备份当成成功。"""
    exists, _isdir, parent = _xp_fileexist(conn, disk)
    log.info("[backup] xp_fileexist path=%s exist=%s parent=%s", disk, exists, parent)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT TOP 1
            f.physical_device_name,
            s.backup_finish_date,
            CAST(ISNULL(s.compressed_backup_size, s.backup_size) AS BIGINT)
        FROM msdb.dbo.backupset s
        INNER JOIN msdb.dbo.backupmediafamily f ON s.media_set_id = f.media_set_id
        WHERE s.database_name = ?
        ORDER BY s.backup_finish_date DESC
        """,
        database,
    )
    row = cur.fetchone()
    actual = str(row[0]).strip() if row and row[0] else ""
    finished = row[1] if row else None
    size = int(row[2] or 0) if row else 0
    disk_norm = disk.replace("/", "\\").rstrip().lower()
    actual_norm = actual.replace("/", "\\").rstrip().lower()
    path_ok = bool(actual_norm) and actual_norm == disk_norm
    start_naive = started_at.replace(tzinfo=None) if getattr(started_at, "tzinfo", None) else started_at
    time_ok = False
    if finished is not None:
        fin = finished.replace(tzinfo=None) if getattr(finished, "tzinfo", None) else finished
        try:
            time_ok = fin >= start_naive - timedelta(seconds=15)
        except Exception:  # noqa: BLE001
            time_ok = False
    if exists == 1 or (path_ok and time_ok):
        log.info("[backup] 已确认文件 %s size=%s exist=%s path_ok=%s", disk, size, exists, path_ok)
        return size
    hint = actual or "(msdb 无记录)"
    raise RuntimeError(
        f"备份未在指定目录生成文件：{disk}。"
        f" 磁盘检测 exist={exists}，父目录存在={parent}。"
        f" msdb 最近一次备份设备：{hint}。"
        "请到数据库服务器查看子目录 {库名}\\{日期}\\，"
        "并确认 SQL Server 服务账号对备份目录有写入权限。"
    )


def _query_last_backup_size(conn: Any, database: str) -> int:
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TOP 1 CAST(compressed_backup_size AS BIGINT)
            FROM msdb.dbo.backupset
            WHERE database_name = ?
            ORDER BY backup_finish_date DESC
            """,
            database,
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
        cur.execute(
            """
            SELECT TOP 1 CAST(backup_size AS BIGINT)
            FROM msdb.dbo.backupset
            WHERE database_name = ?
            ORDER BY backup_finish_date DESC
            """,
            database,
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except Exception as e:  # noqa: BLE001
        log.warning("[backup] 读取备份大小失败: %s", e)
    return 0


def _sftp_download(client: paramiko.SSHClient, remote: str, local: Path) -> int:
    local.parent.mkdir(parents=True, exist_ok=True)
    candidates = [remote]
    if _is_windows_path(remote):
        candidates.append(remote.replace("\\", "/"))
    sftp = client.open_sftp()
    last: Exception | None = None
    try:
        for path in candidates:
            try:
                log.info("[backup] SFTP %s -> %s", path, local)
                sftp.get(path, str(local))
                return int(local.stat().st_size)
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("[backup] SFTP 尝试失败 %s: %s", path, e)
        raise RuntimeError(str(last) if last else "SFTP 下载失败")
    finally:
        sftp.close()


def _sftp_cleanup(client: paramiko.SSHClient, remote_root: str, database: str, retain_days: int) -> None:
    if retain_days <= 0:
        return
    cutoff = _now() - timedelta(days=retain_days)
    db_root = _join_backup_path(remote_root, database, "", "").rstrip("/\\")
    sftp = client.open_sftp()
    try:
        try:
            days = sftp.listdir_attr(db_root)
        except FileNotFoundError:
            return
        for item in days:
            name = item.filename
            try:
                day_dt = datetime.strptime(name, "%Y-%m-%d").replace(tzinfo=_now().tzinfo)
            except ValueError:
                continue
            if day_dt.date() >= cutoff.date():
                continue
            folder = _join_backup_path(remote_root, database, name, "").rstrip("/\\")
            try:
                for f in sftp.listdir_attr(folder):
                    if not stat.S_ISDIR(f.st_mode or 0):
                        sftp.remove(_join_backup_path(remote_root, database, name, f.filename))
                sftp.rmdir(folder)
                log.info("[backup] 已删除过期远程目录 %s", folder)
            except Exception as e:  # noqa: BLE001
                log.warning("[backup] 删除远程过期备份失败 %s: %s", folder, e)
    finally:
        sftp.close()


def _local_cleanup(database: str, retain_days: int) -> None:
    root = BACKUP_STORE / database
    if not root.exists() or retain_days <= 0:
        return
    cutoff = _now() - timedelta(days=retain_days)
    for day_dir in root.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            day_dt = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=_now().tzinfo)
        except ValueError:
            continue
        if day_dt.date() >= cutoff.date():
            continue
        for f in day_dir.glob("*.bak"):
            try:
                f.unlink()
            except OSError as e:
                log.warning("[backup] 删除本地过期文件失败 %s: %s", f, e)
        try:
            next(day_dir.iterdir())
        except StopIteration:
            day_dir.rmdir()


def run_backup(
    conn_row: Any,
    *,
    dbname: str,
    backup_type: str,
    compress: bool,
    retain_days: int,
    delete_old: bool,
) -> dict[str, Any]:
    """备份指定库，返回 file_path / local_path / file_size。"""
    password = decrypt_secret(conn_row.password_enc)
    ssh_password = decrypt_secret(conn_row.ssh_password_enc)
    day, ts, _when = _stamp()
    dbname = (dbname or "").strip()
    if not dbname:
        raise RuntimeError("未指定要备份的数据库")
    filename = f"{dbname}_{ts}_{backup_type}.bak"
    mode = (conn_row.connect_mode or "direct").lower()
    log.info("[backup] 开始 %s 模式 库=%s 类型=%s 配置目录=%s", mode, dbname, backup_type, conn_row.backup_dir)

    local_path = ""
    size = 0
    remote_path = ""
    if mode == "ssh":
        ssh_host = conn_row.ssh_host or conn_row.host
        client = _ssh_client(ssh_host, conn_row.ssh_port, conn_row.ssh_user, ssh_password, conn_row.ssh_key or "")
        try:
            result: dict[str, Any] | None = None
            last_err: Exception | None = None
            try:
                with _sql_tunnel(client, int(conn_row.port), _tunnel_sql_host(ssh_host, conn_row.host)) as local_port:
                    log.info("[backup] 经 SSH 隧道执行备份 127.0.0.1:%s", local_port)
                    try:
                        dbc = _sql_connect(
                            "127.0.0.1",
                            local_port,
                            "master",
                            conn_row.username,
                            password,
                            timeout=8 * 3600,
                            expect_sql_host=conn_row.host,
                        )
                    except Exception as e:  # noqa: BLE001
                        raise _TunnelConnectError(str(e)) from e
                    try:
                        result = _backup_via_odbc(
                            dbc,
                            configured_dir=conn_row.backup_dir,
                            dbname=dbname,
                            day=day,
                            filename=filename,
                            backup_type=backup_type,
                            compress=compress,
                        )
                    finally:
                        dbc.close()
            except _TunnelConnectError as e:
                last_err = e
                log.info("[backup] SSH 隧道/ODBC 连接失败，尝试 sqlcmd: %s", e)
            except Exception as e:  # noqa: BLE001
                # 已连上 SQL Server，备份语句失败（路径、权限、压缩等）不要误报成隧道失败
                raise RuntimeError(f"备份失败：{e}") from e
            if result is None:
                raise RuntimeError(
                    "SSH 已连通，但未能通过隧道连接 SQL Server。"
                    f" 原因：{last_err}"
                ) from last_err
            remote_path = result["file_path"]
            size = int(result.get("file_size") or 0)
            # 备份必须留在数据库服务器。若本机刚写出同一路径，说明连到了本机 SQL。
            if result.get("is_windows") and _local_file_just_written(remote_path):
                raise RuntimeError(
                    f"备份写到了管理平台本机 {remote_path}，没有写到数据库服务器 {conn_row.host}。"
                )
            if result.get("is_windows"):
                log.info("[backup] Windows 实例：文件留在数据库服务器 %s，不拉回本机 data/backups", remote_path)
            else:
                dest = BACKUP_STORE / dbname / day / filename
                try:
                    size = _sftp_download(client, remote_path, dest) or size
                    local_path = str(dest)
                except Exception as e:  # noqa: BLE001
                    log.warning("[backup] SFTP 拉回失败，备份文件留在数据库服务器 %s : %s", remote_path, e)
            if delete_old:
                if result.get("is_windows"):
                    log.info("[backup] Windows 路径无法经 Linux 跳板 SFTP 删旧，跳过远程清理")
                else:
                    try:
                        _sftp_cleanup(client, result.get("root") or conn_row.backup_dir, dbname, retain_days)
                    except Exception as e:  # noqa: BLE001
                        log.warning("[backup] 远程清理失败: %s", e)
                _local_cleanup(dbname, retain_days)
        finally:
            client.close()
    else:
        dbc = _sql_connect(
            conn_row.host,
            conn_row.port,
            "master",
            conn_row.username,
            password,
            timeout=8 * 3600,
            expect_sql_host=conn_row.host,
        )
        try:
            result = _backup_via_odbc(
                dbc,
                configured_dir=conn_row.backup_dir,
                dbname=dbname,
                day=day,
                filename=filename,
                backup_type=backup_type,
                compress=compress,
            )
        finally:
            dbc.close()
        remote_path = result["file_path"]
        size = int(result.get("file_size") or 0)
        if os.path.isfile(remote_path) and _local_file_just_written(remote_path):
            local_path = remote_path
            try:
                size = max(size, int(os.path.getsize(remote_path)))
            except OSError:
                pass
        if delete_old:
            _local_cleanup(dbname, retain_days)

    if size <= 0 and local_path and os.path.isfile(local_path):
        size = int(os.path.getsize(local_path))
    log.info("[backup] 完成 库=%s size=%s local=%s", dbname, size, local_path)
    return {"file_path": remote_path, "local_path": local_path, "file_size": size}


def test_connection(conn_row: Any) -> str:
    password = decrypt_secret(conn_row.password_enc)
    mode = (conn_row.connect_mode or "direct").lower()
    if mode == "ssh":
        ssh_password = decrypt_secret(conn_row.ssh_password_enc)
        ssh_host = conn_row.ssh_host or conn_row.host
        msg = test_ssh(ssh_host, conn_row.ssh_port, conn_row.ssh_user, ssh_password, conn_row.ssh_key or "")
        return msg
    return test_direct(conn_row.host, conn_row.port, "master", conn_row.username, password)
