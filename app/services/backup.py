"""SQL Server 备份：平台可部署在任意位置，BACKUP 始终在数据库服务器本机落盘。"""
from __future__ import annotations

import io
import logging
import os
import posixpath
import re
import socket
import stat
import threading
import time
from contextlib import contextmanager, nullcontext
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

    @property
    def description(self):
        return getattr(self._cur, "description", None)


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


def _assert_backup_allowed(conn: Any, dbname: str, backup_type: str) -> None:
    """差异要有完整备份；日志备份不能用于 SIMPLE。"""
    bt = (backup_type or "full").lower()
    if bt not in {"diff", "log"}:
        return
    cur = conn.cursor()
    cur.execute(
        "SELECT CAST(recovery_model_desc AS NVARCHAR(60)) FROM sys.databases WHERE name = " + _sql_nv(dbname)
    )
    row = cur.fetchone()
    model = str(row[0] or "").strip().upper() if row else ""
    log.info("[backup] 库 %s 恢复模式=%s 类型=%s", dbname, model or "?", bt)
    if bt == "log" and (not model or model == "SIMPLE"):
        raise RuntimeError(
            f"数据库 {dbname} 是简单恢复模式（SIMPLE），不能做日志备份。"
            "请改用完整备份，或把恢复模式改为完整 / 大容量日志。"
        )
    if bt != "diff":
        return
    last_full = None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT TOP 1 backup_finish_date FROM msdb.dbo.backupset "
            f"WHERE database_name = {_sql_nv(dbname)} AND type = N'D' "
            "AND ISNULL(is_copy_only, 0) = 0 "
            "ORDER BY backup_finish_date DESC"
        )
        row = cur.fetchone()
        last_full = row[0] if row else None
    except Exception as e:  # noqa: BLE001
        log.info("[backup] 带 is_copy_only 查询失败，改试旧写法: %s", e)
        cur = conn.cursor()
        cur.execute(
            "SELECT TOP 1 backup_finish_date FROM msdb.dbo.backupset "
            f"WHERE database_name = {_sql_nv(dbname)} AND type = N'D' "
            "ORDER BY backup_finish_date DESC"
        )
        row = cur.fetchone()
        last_full = row[0] if row else None
    if not last_full:
        raise RuntimeError(f"数据库 {dbname} 还没有完整备份，不能做差异备份。请先做一次完整备份。")
    log.info("[backup] 库 %s 最近完整备份时间=%s", dbname, last_full)


def _assert_disk_space(conn: Any, root: str, need_bytes: int) -> None:
    """Windows：用 xp_fixeddrives 看备份盘剩余空间。读不到就跳过，不挡备份。"""
    if not _is_windows_path(root):
        return
    drive = root[0].upper()
    need_mb = max(int(need_bytes / (1024 * 1024) * 1.3) + 200, 300)
    try:
        cur = conn.cursor()
        cur.execute("EXEC master.dbo.xp_fixeddrives")
        rows = cur.fetchall() or []
        _drain_cursor(cur)
    except Exception as e:  # noqa: BLE001
        log.info("[backup] 无法读取磁盘剩余空间: %s", e)
        return
    free = None
    for row in rows:
        letter = str(row[0] or "").strip().upper()[:1]
        if letter == drive:
            try:
                free = int(row[1] or 0)
            except (TypeError, ValueError):
                free = None
            break
    if free is None:
        log.info("[backup] xp_fixeddrives 没有 %s: 盘", drive)
        return
    log.info("[backup] %s: 剩余 %s MB，预估需要 %s MB", drive, free, need_mb)
    if free < need_mb:
        raise RuntimeError(
            f"备份盘 {drive}: 剩余 {free} MB，按上次备份估算至少需要 {need_mb} MB。请先清理磁盘。"
        )


def _verify_backup(conn: Any, disk: str) -> None:
    """RESTORE VERIFYONLY：文件 SQL Server 自己读得过才算成功。"""
    cur = conn.cursor()
    log.info("[backup] 校验备份 %s", disk)
    try:
        cur.execute("RESTORE VERIFYONLY FROM DISK = " + _sql_nv(disk))
        _drain_cursor(cur)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"备份文件校验失败（VERIFYONLY）：{disk}。{e}") from e
    log.info("[backup] VERIFYONLY 通过 %s", disk)


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
    _assert_backup_allowed(dbc, dbname, backup_type)
    info = _query_instance_info(dbc)
    use_compress = bool(compress) and bool(info.get("compress_ok"))
    if compress and not use_compress:
        log.info("[backup] 当前实例不支持备份压缩，已改为不压缩")
    root = _resolve_backup_root(configured_dir, info)
    remote_path = _join_backup_path(root, dbname, day, filename)
    remote_dir = _dir_of(remote_path)
    sql = backup_sql(backup_type, dbname, remote_path.replace("'", "''"), use_compress)
    log.info("[backup] 实际备份路径 %s", remote_path)
    est = _query_last_backup_size(dbc, dbname) or 200 * 1024 * 1024
    _assert_disk_space(dbc, root, est)
    started = _now()
    _run_backup_sql(dbc, sql, remote_dir)
    size = _assert_backup_written(dbc, dbname, remote_path, started)
    _verify_backup(dbc, remote_path)
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
    opts = ["INIT", "CHECKSUM", "NAME = N'sql_backup'"]
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


def _sql_ident(name: str) -> str:
    return "[" + str(name or "").replace("]", "]]") + "]"


_DBNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff.\-]{0,127}$")


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


def _is_expired_day(name: str, retain_days: int) -> bool:
    """保留 N 天：今天算第 1 天。N=2 时只留今天和昨天，更早的日期目录删除。"""
    try:
        day = datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (_now().date() - day).days >= max(int(retain_days), 1)


def _win_sftp_paths(folder: str) -> list[str]:
    """Windows OpenSSH 对盘符路径的几种写法。"""
    raw = (folder or "").strip().rstrip("/\\")
    if not raw:
        return []
    unix = raw.replace("\\", "/")
    seen: list[str] = []
    for p in (raw, unix):
        if p and p not in seen:
            seen.append(p)
    if _is_windows_path(raw):
        drive = raw[0]
        rest = raw[2:].replace("\\", "/").lstrip("/")
        for p in (f"/{drive}:/{rest}", f"/{drive}/{rest}", f"/{drive}:/{rest}/"):
            p = p.rstrip("/") if p.count("/") > 1 else p
            if p and p not in seen:
                seen.append(p)
    return seen


def _sftp_rmdir(client: paramiko.SSHClient, folder: str) -> None:
    """SFTP 删目录（先清文件）。Windows 盘符会试多种路径。"""
    last: Exception | None = None
    sftp = client.open_sftp()
    try:
        for path in _win_sftp_paths(folder):
            try:
                for f in sftp.listdir_attr(path):
                    child = f"{path.rstrip('/')}\\{f.filename}" if "\\" in path else f"{path.rstrip('/')}/{f.filename}"
                    if stat.S_ISDIR(f.st_mode or 0):
                        continue
                    sftp.remove(child)
                sftp.rmdir(path)
                log.info("[backup] SFTP 已删除目录 %s", path)
                return
            except FileNotFoundError:
                last = FileNotFoundError(path)
                log.info("[backup] SFTP 路径不存在，改试下一种 %s", path)
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("[backup] SFTP 删目录尝试失败 %s: %s", path, e)
    finally:
        sftp.close()
    raise RuntimeError(str(last) if last else f"SFTP 无法删除 {folder}")


def _ssh_rmdir(client: paramiko.SSHClient, folder: str) -> None:
    """用 SSH 删 Windows/Linux 目录，不依赖 xp_cmdshell / OLE。"""
    raw = (folder or "").strip().rstrip("/\\").replace('"', "")
    if not raw:
        return
    cmds: list[str] = []
    if _is_windows_path(raw):
        win = raw.replace("/", "\\")
        cmds.append(f'cmd.exe /c if exist "{win}" rmdir /s /q "{win}"')
        cmds.append(
            "powershell.exe -NoProfile -NonInteractive -Command "
            f"\"if (Test-Path -LiteralPath '{win}') {{ Remove-Item -LiteralPath '{win}' -Recurse -Force }}\""
        )
    else:
        cmds.append(f"rm -rf {_quote_cmd(raw)}")
    last: Exception | str | None = None
    for cmd in cmds:
        try:
            code, out, err = _ssh_exec(client, cmd, timeout=90)
            if code == 0:
                log.info("[backup] SSH 已删除目录 %s", folder)
                return
            last = (err or out or f"exit {code}").strip()
            log.info("[backup] SSH 删目录未成功 %s: %s", folder, last)
        except Exception as e:  # noqa: BLE001
            last = e
            log.info("[backup] SSH 删目录异常 %s: %s", folder, e)
    try:
        _sftp_rmdir(client, folder)
        return
    except Exception as e:  # noqa: BLE001
        last = e
    raise RuntimeError(f"无法删除目录 {folder}: {last}")


def _sftp_cleanup(client: paramiko.SSHClient, remote_root: str, database: str, retain_days: int) -> None:
    if retain_days <= 0:
        return
    db_root = _join_backup_path(remote_root, database, "", "").rstrip("/\\")
    names: list[str] = []
    sftp = client.open_sftp()
    try:
        last: Exception | None = None
        for path in _win_sftp_paths(db_root) or [db_root]:
            try:
                names = [item.filename for item in sftp.listdir_attr(path)]
                last = None
                break
            except FileNotFoundError:
                last = FileNotFoundError(path)
                continue
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("[backup] SFTP 列举失败 %s: %s", path, e)
        if last and not names:
            log.warning("[backup] SFTP 列举备份目录失败 %s: %s", db_root, last)
            return
    finally:
        sftp.close()
    for name in names:
        if not _is_expired_day(name, retain_days):
            continue
        folder = _join_backup_path(remote_root, database, name, "").rstrip("/\\")
        try:
            _ssh_rmdir(client, folder)
            log.info("[backup] 已删除过期远程目录 %s", folder)
        except Exception as e:  # noqa: BLE001
            log.warning("[backup] 删除远程过期备份失败 %s: %s", folder, e)


def _sql_list_day_folders(conn: Any, db_root: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(f"EXEC master.sys.xp_dirtree {_sql_nv(db_root)}, 1, 1")
    names: list[str] = []
    try:
        while True:
            try:
                rows = cur.fetchall() or []
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                name = str(row[0] or "").strip()
                is_file = int(row[2] or 0) if len(row) > 2 else 0
                if name and not is_file:
                    names.append(name)
            if not cur.nextset():
                break
    except Exception:  # noqa: BLE001
        pass
    return names


def _sql_delete_bak_in_folder(conn: Any, folder: str) -> None:
    """xp_delete_file 只认 SQL Server 本机路径；截止日期用很远的将来，清掉该目录全部 .bak。"""
    cur = conn.cursor()
    cur.execute(
        "EXEC master.dbo.xp_delete_file 0, "
        f"{_sql_nv(folder)}, N'bak', N'2099-01-01T00:00:00', 0"
    )
    _drain_cursor(cur)


def _sql_config_int(conn: Any, name: str) -> int | None:
    cur = conn.cursor()
    cur.execute(
        "SELECT CAST(value_in_use AS INT) FROM master.sys.configurations WHERE name = " + _sql_nv(name)
    )
    row = cur.fetchone()
    _drain_cursor(cur)
    if row and row[0] is not None:
        return int(row[0])
    return None


def _sql_reconfigure(conn: Any, name: str, value: int) -> None:
    """只允许改外围选项；name 来自白名单。"""
    allowed = {"show advanced options", "xp_cmdshell", "Ole Automation Procedures"}
    if name not in allowed:
        raise ValueError(name)
    cur = conn.cursor()
    cur.execute(f"EXEC master.dbo.sp_configure {_sql_nv(name)}, {int(value)}")
    _drain_cursor(cur)
    cur = conn.cursor()
    try:
        cur.execute("RECONFIGURE")
        _drain_cursor(cur)
    except Exception:  # noqa: BLE001
        cur = conn.cursor()
        cur.execute("RECONFIGURE WITH OVERRIDE")
        _drain_cursor(cur)


@contextmanager
def _sql_temp_enable(conn: Any, option: str):
    """临时打开 xp_cmdshell / OLE，清理完立刻恢复原值。"""
    current = _sql_config_int(conn, option)
    advanced = _sql_config_int(conn, "show advanced options")
    changed = False
    adv_changed = False
    try:
        if current == 1:
            yield True
            return
        if advanced != 1:
            _sql_reconfigure(conn, "show advanced options", 1)
            adv_changed = True
        _sql_reconfigure(conn, option, 1)
        changed = True
        log.info("[backup] 已临时开启 %s（原值=%s，结束后恢复）", option, current)
        yield True
    except Exception as e:  # noqa: BLE001
        log.warning("[backup] 无法开启 %s: %s", option, e)
        yield False
    finally:
        if changed:
            try:
                _sql_reconfigure(conn, option, int(current or 0))
                log.info("[backup] 已恢复 %s=%s", option, current)
            except Exception as e:  # noqa: BLE001
                log.warning("[backup] 恢复 %s 失败: %s", option, e)
        if adv_changed:
            try:
                _sql_reconfigure(conn, "show advanced options", int(advanced or 0))
            except Exception as e:  # noqa: BLE001
                log.warning("[backup] 恢复 show advanced options 失败: %s", e)


def _sql_folder_exists(conn: Any, folder: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"EXEC master.dbo.xp_fileexist {_sql_nv(folder)}")
    row = cur.fetchone()
    _drain_cursor(cur)
    if not row:
        return False
    exist = int(row[0] or 0)
    isdir = int(row[1] or 0) if len(row) > 1 else 0
    return bool(exist or isdir)


def _sql_rmdir(conn: Any, folder: str) -> None:
    """在 SQL Server 本机删目录。需要 xp_cmdshell 或 OLE（可临时开启）。"""
    win = folder.replace("/", "\\").replace('"', "")
    cur = conn.cursor()
    try:
        cur.execute(f"EXEC master.dbo.xp_cmdshell {_sql_nv(f'rmdir /s /q \"{win}\"')}")
        _drain_cursor(cur)
        if not _sql_folder_exists(conn, win):
            log.info("[backup] xp_cmdshell 已删除目录 %s", win)
            return
        raise RuntimeError("xp_cmdshell 执行后目录仍在")
    except Exception as e:  # noqa: BLE001
        log.info("[backup] xp_cmdshell 删目录失败，改试 OLE: %s", e)
    cur = conn.cursor()
    cur.execute(
        "DECLARE @fso int, @ok int; "
        "EXEC master.dbo.sp_OACreate 'Scripting.FileSystemObject', @fso OUT; "
        f"EXEC master.dbo.sp_OAMethod @fso, 'DeleteFolder', @ok OUT, {_sql_nv(win)}, 1; "
        "EXEC master.dbo.sp_OADestroy @fso;"
    )
    _drain_cursor(cur)
    if _sql_folder_exists(conn, win):
        raise RuntimeError(f"目录仍在 {win}")


def _remove_day_folder(folder: str, *, conn: Any, ssh_client: Any = None) -> None:
    """先删 .bak。Windows 目录走 SQL；SSH 只用于 Linux 本机路径（跳板机看不到 G:）。"""
    try:
        _sql_delete_bak_in_folder(conn, folder)
    except Exception as e:  # noqa: BLE001
        log.info("[backup] xp_delete_file 删 .bak 失败 %s: %s", folder, e)
    if _is_windows_path(folder):
        if ssh_client is not None:
            log.info("[backup] SSH 是跳板机，看不到 Windows 路径，改用 SQL 删目录 %s", folder)
        _sql_rmdir(conn, folder)
        return
    if ssh_client is not None:
        _ssh_rmdir(ssh_client, folder)
        return
    _sql_rmdir(conn, folder)


def _sql_cleanup_old_backups(
    conn: Any,
    root: str,
    database: str,
    retain_days: int,
    ssh_client: Any = None,
) -> None:
    """在 SQL Server 本机删除过期日期目录（D:\\sql_backup\\库名\\YYYY-MM-DD）。"""
    if retain_days <= 0 or not root or not database:
        return
    db_root = _join_backup_path(root, database, "", "").rstrip("/\\")
    log.info("[backup] 按保留 %s 天清理 SQL Server 目录 %s", retain_days, db_root)
    try:
        days = _sql_list_day_folders(conn, db_root)
    except Exception as e:  # noqa: BLE001
        log.warning("[backup] 列举备份日期目录失败: %s", e)
        days = []
    expired = [name for name in days if _is_expired_day(name, retain_days)]
    if not days:
        keep_from = _now().date() - timedelta(days=max(int(retain_days) - 1, 0))
        cutoff = f"{keep_from.isoformat()}T00:00:00"
        try:
            cur = conn.cursor()
            cur.execute(
                "EXEC master.dbo.xp_delete_file 0, "
                f"{_sql_nv(db_root)}, N'bak', {_sql_nv(cutoff)}, 1"
            )
            _drain_cursor(cur)
            log.info("[backup] 已按文件日期清理 %s 中早于 %s 的 .bak", db_root, cutoff)
        except Exception as e:  # noqa: BLE001
            log.warning("[backup] 按文件日期清理失败: %s", e)
        if ssh_client is not None and not _is_windows_path(db_root):
            try:
                _sftp_cleanup(ssh_client, root, database, retain_days)
            except Exception as e:  # noqa: BLE001
                log.warning("[backup] SSH 补清日期目录失败: %s", e)
        return
    if not expired:
        log.info("[backup] 没有超过 %s 天的日期目录", retain_days)
        return
    windows_days = _is_windows_path(db_root)
    with _sql_temp_enable(conn, "xp_cmdshell") as cmd_ok:
        extra = (
            _sql_temp_enable(conn, "Ole Automation Procedures")
            if windows_days and not cmd_ok
            else nullcontext(False)
        )
        with extra as ole_ok:
            if windows_days and not cmd_ok and not ole_ok:
                log.warning(
                    "[backup] 无法临时开启 xp_cmdshell/OLE，只能删 .bak，空日期目录会留下。"
                    "SQL 账号需要 sysadmin，且策略允许改 sp_configure。"
                )
            for name in expired:
                folder = _join_backup_path(root, database, name, "").rstrip("/\\")
                try:
                    _remove_day_folder(
                        folder,
                        conn=conn,
                        ssh_client=None if windows_days else ssh_client,
                    )
                    log.info("[backup] 已删除 SQL Server 过期备份 %s", folder)
                except Exception as e:  # noqa: BLE001
                    log.warning("[backup] 删除 SQL Server 过期备份失败 %s: %s", folder, e)


def _local_cleanup(database: str, retain_days: int) -> None:
    root = BACKUP_STORE / database
    if not root.exists() or retain_days <= 0:
        return
    for day_dir in root.iterdir():
        if not day_dir.is_dir() or not _is_expired_day(day_dir.name, retain_days):
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
                        if delete_old:
                            try:
                                _sql_cleanup_old_backups(
                                    dbc,
                                    result.get("root") or "",
                                    dbname,
                                    retain_days,
                                    ssh_client=client,
                                )
                            except Exception as e:  # noqa: BLE001
                                log.warning("[backup] SQL Server 清理过期备份失败: %s", e)
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
                if not result.get("is_windows"):
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
            if delete_old:
                try:
                    _sql_cleanup_old_backups(dbc, result.get("root") or "", dbname, retain_days)
                except Exception as e:  # noqa: BLE001
                    log.warning("[backup] SQL Server 清理过期备份失败: %s", e)
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


@contextmanager
def open_sql_session(conn_row: Any, timeout: int = 600):
    """打开一条到目标 SQL Server 的连接（直连或 SSH 隧道），用完关闭。"""
    password = decrypt_secret(conn_row.password_enc)
    mode = (conn_row.connect_mode or "direct").lower()
    if mode == "ssh":
        ssh_password = decrypt_secret(conn_row.ssh_password_enc)
        ssh_host = conn_row.ssh_host or conn_row.host
        client = _ssh_client(ssh_host, conn_row.ssh_port, conn_row.ssh_user, ssh_password, conn_row.ssh_key or "")
        try:
            dest = _tunnel_sql_host(ssh_host, conn_row.host)
            with _sql_tunnel(client, int(conn_row.port), dest) as local_port:
                dbc = _sql_connect(
                    "127.0.0.1",
                    local_port,
                    "master",
                    conn_row.username,
                    password,
                    timeout=timeout,
                    expect_sql_host=conn_row.host,
                )
                try:
                    yield dbc
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
        timeout=timeout,
        expect_sql_host=conn_row.host,
    )
    try:
        yield dbc
    finally:
        dbc.close()


def xp_cmdshell_lines(conn: Any, cmd: str) -> list[str]:
    """执行 xp_cmdshell，返回非空输出行。"""
    cur = conn.cursor()
    cur.execute("EXEC master.dbo.xp_cmdshell " + _sql_nv(cmd))
    lines: list[str] = []
    try:
        while True:
            try:
                rows = cur.fetchall() or []
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                if row and row[0] is not None:
                    text = str(row[0]).rstrip("\r\n")
                    if text:
                        lines.append(text)
            if not cur.nextset():
                break
    except Exception:  # noqa: BLE001
        pass
    return lines


def list_backup_catalog(conn_row: Any) -> list[dict[str, Any]]:
    """列出 SQL Server 备份目录下的 库 / 日期 / 文件。"""
    root = (conn_row.backup_dir or "").strip()
    if not root:
        raise RuntimeError("该连接未填写备份目录")
    with open_sql_session(conn_row, timeout=60) as dbc:
        return _parse_backup_tree(dbc, root)


def _parse_backup_tree(conn: Any, root: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(f"EXEC master.sys.xp_dirtree {_sql_nv(root)}, 3, 1")
    raw: list[tuple[str, int, int]] = []
    try:
        while True:
            try:
                rows = cur.fetchall() or []
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                name = str(row[0] or "").strip()
                depth = int(row[1] or 0)
                is_file = int(row[2] or 0) if len(row) > 2 else 0
                if name:
                    raw.append((name, depth, is_file))
            if not cur.nextset():
                break
    except Exception:  # noqa: BLE001
        pass
    dbs: dict[str, dict[str, Any]] = {}
    cur_db = ""
    cur_day = ""
    for name, depth, is_file in raw:
        if depth == 1 and not is_file:
            cur_db = name
            cur_day = ""
            dbs.setdefault(cur_db, {"database": cur_db, "days": []})
            continue
        if not cur_db:
            continue
        if depth == 2 and not is_file:
            cur_day = name
            days: list[dict[str, Any]] = dbs[cur_db]["days"]
            if not any(d["name"] == cur_day for d in days):
                days.append({"name": cur_day, "files": []})
            continue
        if is_file and name.lower().endswith(".bak"):
            day = cur_day if depth >= 3 else ""
            if depth == 2:
                day = ""
                days = dbs[cur_db]["days"]
                if not any(d["name"] == "" for d in days):
                    days.append({"name": "", "files": []})
            folder = _join_backup_path(root, cur_db, day, "").rstrip("/\\") if day else _join_backup_path(root, cur_db, "", "").rstrip("/\\")
            path = _join_backup_path(root, cur_db, day, name) if day else f"{folder}\\{name}" if _is_windows_path(root) else f"{folder}/{name}"
            for item in dbs[cur_db]["days"]:
                if item["name"] == day:
                    item["files"].append({"name": name, "path": path})
                    break
    out = list(dbs.values())
    for dbitem in out:
        dbitem["days"].sort(key=lambda x: x["name"], reverse=True)
    out.sort(key=lambda x: str(x["database"]).lower())
    log.info("[backup] 目录浏览 %s 共 %s 个库", root, len(out))
    return out


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name or "file").strip(" .")
    return cleaned or "file"


def _validate_restore_dbname(name: str) -> str:
    name = (name or "").strip()
    if not name or not _DBNAME_RE.match(name):
        raise RuntimeError("目标库名只能含中文、字母、数字、下划线、点和短横线")
    if is_system_db(name):
        raise RuntimeError("不能恢复到系统库 master / model / msdb / tempdb")
    return name


def _cursor_row_dict(cur: Any, row: Any) -> dict[str, Any]:
    desc = getattr(cur, "description", None)
    if desc and row is not None:
        names = [str(c[0]) for c in desc]
        return {names[i]: row[i] for i in range(min(len(names), len(row)))}
    return {}


def _dict_get(data: dict[str, Any], *names: str) -> Any:
    lower = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _file_type(value: Any) -> str:
    if value is None:
        return "D"
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("ascii", "ignore")
    return str(value).strip().upper()[:1] or "D"


def _fmt_sql_dt(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).replace("T", " ")[:19]


def _join_os_path(dirpath: str, name: str) -> str:
    root = (dirpath or "").rstrip("/\\")
    if not root:
        return name
    if _is_windows_path(root) or "\\" in root:
        return f"{root}\\{name}"
    return f"{root}/{name}"


def _reg_instance_path(cur: Any, value_name: str) -> str:
    try:
        cur.execute(
            "EXEC master.dbo.xp_instance_regread "
            "N'HKEY_LOCAL_MACHINE', "
            "N'Software\\Microsoft\\MSSQLServer\\MSSQLServer', "
            + _sql_nv(value_name)
        )
        row = cur.fetchone()
        if row:
            data = row[1] if len(row) > 1 else row[0]
            if data:
                return str(data).rstrip("\\/")
    except Exception as e:  # noqa: BLE001
        log.info("[restore] 读注册表 %s 失败: %s", value_name, e)
    return ""


def _query_default_file_dirs(conn: Any) -> tuple[str, str]:
    """数据/日志默认目录。2008 R2 没有 InstanceDefaultDataPath，用注册表或 master 文件兜底。"""
    data_dir, log_dir = "", ""
    cur = conn.cursor()
    for prop, slot in (("InstanceDefaultDataPath", "data"), ("InstanceDefaultLogPath", "log")):
        try:
            cur.execute(f"SELECT CAST(SERVERPROPERTY('{prop}') AS NVARCHAR(4000))")
            row = cur.fetchone()
            if row and row[0]:
                path = str(row[0]).rstrip("\\/")
                if slot == "data":
                    data_dir = path
                else:
                    log_dir = path
        except Exception:  # noqa: BLE001
            pass
    if not data_dir:
        data_dir = _reg_instance_path(cur, "DefaultData")
    if not log_dir:
        log_dir = _reg_instance_path(cur, "DefaultLog")
    if not data_dir or not log_dir:
        try:
            cur.execute("SELECT physical_name, type FROM master.sys.master_files WHERE database_id = 1")
            for row in cur.fetchall() or []:
                phys = str(row[0] or "")
                typ = _as_int(row[1])
                folder = _dir_of(phys)
                if typ == 0 and not data_dir:
                    data_dir = folder
                if typ == 1 and not log_dir:
                    log_dir = folder
        except Exception as e:  # noqa: BLE001
            log.info("[restore] 读 master 文件路径失败: %s", e)
    log.info("[restore] 默认数据目录=%s 日志目录=%s", data_dir or "?", log_dir or data_dir or "?")
    return data_dir, log_dir or data_dir


def _backup_type_from_header(value: Any) -> str:
    code = _as_int(value)
    if code == 2:
        return "log"
    if code == 5:
        return "diff"
    return "full"


def _restore_header(conn: Any, disk: str) -> dict[str, Any]:
    cur = conn.cursor()
    log.info("[restore] HEADERONLY %s", disk)
    cur.execute("RESTORE HEADERONLY FROM DISK = " + _sql_nv(disk))
    row = cur.fetchone()
    data = _cursor_row_dict(cur, row) if row else {}
    _drain_cursor(cur)
    if not row:
        raise RuntimeError("无法读取备份头，文件可能不是有效的 .bak，或不在该 SQL Server 本机。")
    backup_type = _backup_type_from_header(_dict_get(data, "BackupType") if data else None)
    if not data:
        backup_type = _backup_type_from_header(row[2] if len(row) > 2 else 1)
        source = str(row[9] or "").strip() if len(row) > 9 else ""
        finished = _fmt_sql_dt(row[18] if len(row) > 18 else None)
    else:
        source = str(_dict_get(data, "DatabaseName") or "").strip()
        finished = _fmt_sql_dt(_dict_get(data, "BackupFinishDate") or _dict_get(data, "BackupStartDate"))
    if not data:
        data = {"BackupType": row[2] if len(row) > 2 else 1, "DatabaseName": source}
    log.info("[restore] 备份头 库=%s 类型=%s 完成=%s", source, backup_type, finished)
    return {
        "source_database": source,
        "backup_type": backup_type,
        "backup_finish": finished,
        "raw": data,
    }


def _restore_filelist(conn: Any, disk: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    log.info("[restore] FILELISTONLY %s", disk)
    cur.execute("RESTORE FILELISTONLY FROM DISK = " + _sql_nv(disk))
    rows = []
    try:
        rows = cur.fetchall() or []
    except Exception:  # noqa: BLE001
        rows = []
    files: list[dict[str, Any]] = []
    for row in rows:
        data = _cursor_row_dict(cur, row)
        if data:
            logical = str(_dict_get(data, "LogicalName") or "").strip()
            physical = str(_dict_get(data, "PhysicalName") or "").strip()
            ftype = _file_type(_dict_get(data, "Type"))
        else:
            logical = str(row[0] or "").strip() if row else ""
            physical = str(row[1] or "").strip() if row and len(row) > 1 else ""
            ftype = _file_type(row[2] if row and len(row) > 2 else "D")
        if logical:
            files.append({"logical": logical, "physical": physical, "type": ftype})
    _drain_cursor(cur)
    log.info("[restore] 备份内 %s 个文件", len(files))
    return files


def _db_exists(conn: Any, name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sys.databases WHERE name = " + _sql_nv(name))
    return bool(cur.fetchone())


def _set_db_access(conn: Any, name: str, single: bool) -> None:
    cur = conn.cursor()
    mode = "SINGLE_USER WITH ROLLBACK IMMEDIATE" if single else "MULTI_USER"
    log.info("[restore] ALTER DATABASE %s SET %s", name, mode)
    cur.execute(f"ALTER DATABASE {_sql_ident(name)} SET {mode}")
    _drain_cursor(cur)


def _build_move_clause(files: list[dict[str, Any]], target_db: str, data_dir: str, log_dir: str) -> list[str]:
    moves: list[str] = []
    data_i = 0
    log_i = 0
    for item in files:
        logical = item["logical"]
        physical = item.get("physical") or ""
        ftype = item.get("type") or "D"
        fallback = _dir_of(physical)
        if ftype == "L":
            log_i += 1
            suffix = "" if log_i == 1 else f"_{log_i}"
            dest = _join_os_path(log_dir or fallback, f"{_safe_filename(target_db)}{suffix}_log.ldf")
        elif ftype == "S":
            dest = _join_os_path(data_dir or fallback, _safe_filename(f"{target_db}_{logical}"))
        else:
            data_i += 1
            ext = ".mdf" if data_i == 1 and ftype == "D" else ".ndf"
            extra = "" if data_i == 1 else f"_{data_i}"
            dest = _join_os_path(data_dir or fallback, f"{_safe_filename(target_db)}{extra}{ext}")
        moves.append(f"MOVE {_sql_nv(logical)} TO {_sql_nv(dest)}")
        log.info("[restore] MOVE %s -> %s", logical, dest)
    return moves


def inspect_backup_file(conn_row: Any, disk: str) -> dict[str, Any]:
    """读取 .bak 头，给恢复向导预填来源库和类型。"""
    disk = (disk or "").strip()
    if not disk:
        raise RuntimeError("请指定备份文件路径")
    with open_sql_session(conn_row, timeout=120) as dbc:
        header = _restore_header(dbc, disk)
        files = _restore_filelist(dbc, disk)
    backup_type = header["backup_type"]
    labels = {"full": "完整", "diff": "差异", "log": "日志"}
    can_restore = backup_type == "full"
    reason = "" if can_restore else "当前只支持从完整备份恢复，差异 / 日志请先恢复对应的完整备份。"
    return {
        "file_path": disk,
        "source_database": header["source_database"],
        "backup_type": backup_type,
        "backup_type_label": labels.get(backup_type, backup_type),
        "backup_finish": header["backup_finish"],
        "can_restore": can_restore,
        "reason": reason,
        "files": files,
    }


def restore_database(
    conn_row: Any,
    disk: str,
    target_database: str,
    *,
    replace: bool = False,
    recovery: bool = True,
) -> dict[str, Any]:
    """在目标 SQL Server 上执行 RESTORE DATABASE。文件必须在该机本机可见。"""
    disk = (disk or "").strip()
    if not disk:
        raise RuntimeError("请指定备份文件路径")
    target = _validate_restore_dbname(target_database)
    log.info("[restore] 开始恢复 file=%s target=%s replace=%s recovery=%s", disk, target, replace, recovery)
    with open_sql_session(conn_row, timeout=28800) as dbc:
        header = _restore_header(dbc, disk)
        if header["backup_type"] != "full":
            kind = {"diff": "差异", "log": "日志"}.get(header["backup_type"], header["backup_type"])
            raise RuntimeError(f"当前只支持从完整备份恢复，这份文件是{kind}备份。")
        source = (header["source_database"] or "").strip()
        files = _restore_filelist(dbc, disk)
        if not files:
            raise RuntimeError("备份里没有数据文件")
        exists = _db_exists(dbc, target)
        log.info("[restore] 来源库=%s 目标库=%s 已存在=%s", source, target, exists)
        if exists and not replace:
            raise RuntimeError(f"目标库 {target} 已存在。如需覆盖请勾选「覆盖已有库」。")
        same_name = bool(source) and source.lower() == target.lower()
        options: list[str] = []
        if not same_name:
            data_dir, log_dir = _query_default_file_dirs(dbc)
            if not data_dir:
                raise RuntimeError("无法确定目标实例的数据目录，请把库恢复到原库名，或检查 SQL Server 默认路径。")
            options.extend(_build_move_clause(files, target, data_dir, log_dir))
        if exists or replace or same_name:
            options.append("REPLACE")
        options.append("RECOVERY" if recovery else "NORECOVERY")
        options.append("STATS = 10")
        sql = (
            f"RESTORE DATABASE {_sql_ident(target)} FROM DISK = {_sql_nv(disk)} "
            f"WITH {', '.join(options)}"
        )
        log.info("[restore] 执行 SQL: %s", sql[:500])
        if exists:
            _set_db_access(dbc, target, True)
        cur = dbc.cursor()
        try:
            cur.execute(sql)
            _drain_cursor(cur)
        except Exception:
            if exists:
                try:
                    _set_db_access(dbc, target, False)
                except Exception as e:  # noqa: BLE001
                    log.warning("[restore] 恢复失败后改回 MULTI_USER 失败: %s", e)
            raise
        if exists and recovery:
            try:
                _set_db_access(dbc, target, False)
            except Exception as e:  # noqa: BLE001
                log.info("[restore] 恢复后 MULTI_USER：%s", e)
    log.info("[restore] 完成 target=%s recovery=%s", target, recovery)
    return {
        "target_database": target,
        "source_database": source,
        "file_path": disk,
        "recovery": recovery,
    }
