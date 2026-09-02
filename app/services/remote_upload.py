"""本地备份完成后，把 .bak 上传到已配置的群晖。"""
from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import DbConnection, RemoteTarget
from app.security import decrypt_secret
from app.services.backup import (
    _is_windows_path,
    _sftp_download,
    _ssh_client,
    _sql_temp_enable,
    open_sql_session,
    xp_cmdshell_lines,
)
from app.services.ssh_util import ssh_params
from app.services import progress as prog
from app.services.synology import (
    cleanup_synology_days,
    test_synology,
    upload_to_synology,
)

log = logging.getLogger(__name__)

# 整包 OPENROWSET 只用于小文件，避免 SQL / 容器内存被撑爆
_MAX_SQL_BLOB = 32 * 1024 * 1024
# 每块一次 PowerShell 冷启动约 10 秒，256KB 会把 800MB 拖成数小时
_CHUNK = 8 * 1024 * 1024


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
        raise RuntimeError("未找到群晖备份配置，请先在「配置中心」添加群晖")
    password = decrypt_secret(target.password_enc)
    if not password:
        raise RuntimeError("群晖密码为空，请到「配置中心」重新保存")
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
    size_mb = max(int((rec.file_size or 0) / (1024 * 1024)), 0)
    try:
        prog.set_message(int(conn_row.id), f"正在上传群晖 {dbname} {size_mb}MB", dbname)
    except Exception:  # noqa: BLE001
        pass
    dest_dir = f"{(syno['remote_dir'] or '').rstrip('/')}/{dest_subdir}".replace("\\", "/")
    staged: Path | None = None
    local = _existing_local(rec)
    try:
        remote = ""
        direct_err = ""
        # Windows SQL：一律先本机直传，平台预建目录会浪费时间且可能 No route
        if _is_windows_path(src_path):
            log.info("[remote] 先走 SQL 本机直传 %s size=%s", src_path, rec.file_size or 0)
            try:
                remote = _upload_via_sql_direct(
                    conn_row,
                    src_path,
                    syno,
                    dest_dir,
                    filename,
                    int(rec.file_size or 0),
                )
                log.info("[remote] SQL 本机直传群晖成功 %s", remote)
            except Exception as e:  # noqa: BLE001
                direct_err = str(e)
                log.warning("[remote] SQL 本机直传失败，改走平台代传: %s", e)
        if not remote:
            if not local:
                staged = _stage_remote_file(conn_row, rec)
                local = staged
            if not local or not local.is_file():
                msg = (
                    f"本地备份已完成，但平台读不到文件 {src_path}，无法上传群晖。"
                    "请确认平台能访问该备份盘，且群晖 File Station（HTTP 5000 / HTTPS 5001）网络互通。"
                )
                if direct_err:
                    msg = f"SQL 本机直传失败：{direct_err}；{msg}"
                raise RuntimeError(msg)
            try:
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
            except Exception as e:  # noqa: BLE001
                if direct_err:
                    raise RuntimeError(f"SQL 本机直传失败：{direct_err}；平台代传失败：{e}") from e
                raise
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
        ssh = ssh_params(conn_row)
        client = _ssh_client(ssh.host, ssh.port, ssh.user, ssh.password, ssh.key)
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


def _ps_lit(value: str) -> str:
    """PowerShell 单引号字面量。"""
    return "'" + str(value or "").replace("'", "''") + "'"


def _ps_encoded_cmd(script: str) -> str:
    """PowerShell -EncodedCommand：UTF-16LE，不依赖本机临时文件。"""
    enc = base64.b64encode((script or "").encode("utf-16-le")).decode("ascii")
    return _ps_exe() + " -EncodedCommand " + enc


def _sql_run(dbc: Any, cmd: str) -> list[str]:
    lines = xp_cmdshell_lines(dbc, cmd)
    if lines:
        log.info("[remote] xp_cmdshell 输出: %s", " | ".join(lines)[:500])
    return lines


def _ps_exe() -> str:
    """xp_cmdshell 里用这个启动 PowerShell 2～5。"""
    return "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass"


def _sql_write_text_file(dbc: Any, dest: str, text: str) -> None:
    """把脚本写成 SQL Server 本机临时文件。UTF-16 带 BOM，PowerShell 2 的 -File 才能读。"""
    raw = (text or "").encode("utf-16")
    b64 = base64.b64encode(raw).decode("ascii")
    dest_ps = dest.replace("'", "''")
    b64_path = dest + ".b64"
    b64_ps = b64_path.replace("'", "''")
    ps = _ps_exe()
    _sql_run(
        dbc,
        ps + " -Command "
        f"\"foreach($p in @('{dest_ps}','{b64_ps}')){{if([IO.File]::Exists($p)){{[IO.File]::Delete($p)}}}}\"",
    )
    # xp_cmdshell 大约 8000 字；2008 上每次冷启动很慢，块尽量大
    step = 3500
    for i in range(0, len(b64), step):
        piece = b64[i : i + step]
        if i == 0:
            cmd = f"[IO.File]::WriteAllText('{b64_ps}','{piece}')"
        else:
            cmd = f"[IO.File]::AppendAllText('{b64_ps}','{piece}')"
        _sql_run(dbc, ps + " -Command \"" + cmd + "\"")
    out = _sql_run(
        dbc,
        ps + " -Command "
        f"\"$t=[IO.File]::ReadAllText('{b64_ps}');"
        f"[IO.File]::WriteAllBytes('{dest_ps}',[Convert]::FromBase64String($t));"
        f"[IO.File]::Delete('{b64_ps}');"
        f"if([IO.File]::Exists('{dest_ps}')){{'SQLBAK_PS1_OK'}}else{{'SQLBAK_PS1_MISSING'}}\"",
    )
    if not any("SQLBAK_PS1_OK" in x for x in out):
        raise RuntimeError("未能在 SQL Server 本机写出上传脚本：" + " ".join(out)[:400])
    log.info("[remote] 已写入 SQL 本机脚本 %s (%s 字节)", dest, len(raw))


def _upload_ps_script(syno: dict[str, Any], src: str, dest_dir: str, filename: str) -> str:
    """SQL 本机直传脚本：只用 PowerShell 2 / .NET 2 API，Windows 2008～2019 都能跑。"""
    scheme = "https" if syno.get("https") else "http"
    base = f"{scheme}://{syno['host']}:{int(syno['port'])}"
    return f"""
$ErrorActionPreference = 'Stop'
if ($PSVersionTable -and [int]$PSVersionTable.PSVersion.Major -ge 3) {{ $ProgressPreference = 'SilentlyContinue' }}
try {{ [Net.ServicePointManager]::ServerCertificateValidationCallback = {{ $true }} }} catch {{}}
try {{ [Net.ServicePointManager]::Expect100Continue = $false }} catch {{}}
try {{ [Net.WebRequest]::DefaultWebProxy = $null }} catch {{}}
foreach ($p in @(4080, 3072, 192, 48)) {{
  try {{ [Net.ServicePointManager]::SecurityProtocol = $p; break }} catch {{}}
}}
$noproxy = $null
try {{ $noproxy = [Net.GlobalProxySelection]::GetEmptyWebProxy() }} catch {{}}
$base = {_ps_lit(base)}
$user = {_ps_lit(str(syno.get("username") or ""))}
$pass = {_ps_lit(str(syno.get("password") or ""))}
$src = {_ps_lit(src)}
$dest = {_ps_lit(dest_dir.replace("\\\\", "/").rstrip("/"))}
$fn = {_ps_lit(filename)}
if (-not [IO.File]::Exists($src)) {{ throw ('file not found ' + $src) }}
Write-Output ('SQLBAK_PS=' + [string]$PSVersionTable.PSVersion)
function Enc([string]$s) {{ return [Uri]::EscapeDataString($s) }}
function HttpLogin([string]$loginUrl, [string]$form) {{
  $wc = New-Object System.Net.WebClient
  if ($noproxy) {{ $wc.Proxy = $noproxy }}
  $wc.Headers.Add('Content-Type','application/x-www-form-urlencoded')
  $bytes = [Text.Encoding]::UTF8.GetBytes($form)
  $txt = [Text.Encoding]::UTF8.GetString($wc.UploadData($loginUrl, 'POST', $bytes))
  $wc.Dispose()
  return $txt
}}
$curlExe = ''
try {{
  $cmd = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($cmd -ne $null) {{
    $p = [string]$cmd.Path
    if (-not $p) {{ $p = [string]$cmd.Definition }}
    if ($p -like '*curl.exe') {{ $curlExe = $p }}
  }}
}} catch {{}}
Write-Output ('SQLBAK_CURL=' + $curlExe)
$sid = ''
$tok = ''
$txt = ''
foreach ($ver in @('7','6','3','2')) {{
  $loginUrl = $base + '/webapi/auth.cgi'
  $form = 'api=SYNO.API.Auth&version=' + $ver + '&method=login&account=' + (Enc $user) + '&passwd=' + (Enc $pass) + '&session=FileStation&format=sid&enable_syno_token=yes'
  $txt = ''
  if ($curlExe) {{
    try {{ $txt = & $curlExe -sS -k --noproxy '*' --connect-timeout 20 -X POST $loginUrl -d $form }} catch {{ $txt = '' }}
  }}
  if (-not $txt) {{ $txt = HttpLogin $loginUrl $form }}
  if ($txt -match '"sid"\\s*:\\s*"([^"]+)"') {{
    $sid = $Matches[1]
    if ($txt -match '"synotoken"\\s*:\\s*"([^"]+)"') {{ $tok = $Matches[1] }}
    break
  }}
}}
if (-not $sid) {{ throw ('synology login fail ' + $txt) }}
$u = $base + '/webapi/entry.cgi?api=SYNO.FileStation.Upload&version=2&method=upload&_sid=' + $sid
$out = ''
$usedCurl = $false
if ($curlExe) {{
  try {{
    $cargs = @('-sS','-k','--noproxy','*','--connect-timeout','20','--max-time','28800','-X','POST',$u,
      '-F',('path=' + $dest),'-F','create_parents=true','-F','overwrite=true',
      '-F',('file=@' + $src + ';filename=' + $fn))
    if ($tok) {{ $cargs += @('-H',('X-SYNO-TOKEN: ' + $tok)) }}
    $out = & $curlExe @cargs
    $usedCurl = $true
  }} catch {{
    $usedCurl = $false
    $out = ''
  }}
}}
if (-not $usedCurl) {{
  $boundary = '----sqlbak' + [guid]::NewGuid().ToString('N')
  $enc = [Text.Encoding]::UTF8
  $CRLF = [char]13 + [char]10
  function Part([string]$name, [string]$val) {{
    return '--' + $boundary + $CRLF + 'Content-Disposition: form-data; name="' + $name + '"' + $CRLF + $CRLF + $val + $CRLF
  }}
  $pre = $enc.GetBytes((Part 'path' $dest) + (Part 'create_parents' 'true') + (Part 'overwrite' 'true') + '--' + $boundary + $CRLF + 'Content-Disposition: form-data; name="file"; filename="' + $fn + '"' + $CRLF + 'Content-Type: application/octet-stream' + $CRLF + $CRLF)
  $post = $enc.GetBytes($CRLF + '--' + $boundary + '--' + $CRLF)
  $fi = New-Object IO.FileInfo($src)
  $req = [Net.HttpWebRequest]::Create($u)
  $req.Method = 'POST'
  if ($noproxy) {{ $req.Proxy = $noproxy }}
  $req.Timeout = 28800000
  try {{ $req.ReadWriteTimeout = 28800000 }} catch {{}}
  $req.AllowWriteStreamBuffering = $false
  $req.ContentType = 'multipart/form-data; boundary=' + $boundary
  $req.ContentLength = $pre.Length + $fi.Length + $post.Length
  if ($tok) {{ $req.Headers.Add('X-SYNO-TOKEN', $tok) }}
  $rs = $req.GetRequestStream()
  $rs.Write($pre, 0, $pre.Length)
  $fs = $fi.OpenRead()
  $buf = New-Object byte[] 65536
  while (($n = $fs.Read($buf, 0, $buf.Length)) -gt 0) {{ $rs.Write($buf, 0, $n) }}
  $fs.Close()
  $rs.Write($post, 0, $post.Length)
  $rs.Close()
  try {{
    $resp = $req.GetResponse()
    $sr = New-Object IO.StreamReader($resp.GetResponseStream())
    $out = $sr.ReadToEnd()
    $sr.Close(); $resp.Close()
  }} catch {{
    $ex = $_.Exception
    if ($ex.Response) {{
      $sr = New-Object IO.StreamReader($ex.Response.GetResponseStream())
      $out = $sr.ReadToEnd(); $sr.Close()
      throw ('http ' + $out)
    }}
    throw
  }}
}}
Write-Output $out
if ($out -match '"success"\\s*:\\s*true') {{ Write-Output 'SQLBAK_UP_OK' }} else {{ throw ('upload fail ' + $out) }}
""".strip() + "\n"


def _parse_syno_upload_ok(lines: list[str], dest_dir: str, filename: str) -> str:
    text = "\n".join(lines or [])
    remote = dest_dir.rstrip("/") + "/" + filename
    if any("SQLBAK_UP_OK" in x for x in (lines or [])):
        return remote
    compact = text.replace(" ", "")
    if '"success":true' in compact:
        return remote
    raise RuntimeError((text or "群晖直传无输出")[:800])


def _upload_via_sql_direct(
    conn_row: DbConnection,
    windows_path: str,
    syno: dict[str, Any],
    dest_dir: str,
    filename: str,
    size: int,
) -> str:
    """让 SQL Server 本机用 curl/HttpClient 直传群晖，700MB 级文件按局域网速度走。"""
    dest_dir = (dest_dir or "").replace("\\", "/").rstrip("/")
    if not dest_dir:
        raise RuntimeError("群晖目标目录为空")
    script = _upload_ps_script(syno, windows_path, dest_dir, filename)
    encoded = _ps_encoded_cmd(script)
    stamp = str(int(time.time()))
    ps1 = f"C:\\Windows\\Temp\\sqlbak_up_{stamp}.ps1"
    log.info("[remote] SQL 本机直传 %s -> %s/%s size=%s", windows_path, dest_dir, filename, size)
    lines: list[str] = []
    with open_sql_session(conn_row, timeout=8 * 3600) as dbc:
        with _sql_temp_enable(dbc, "xp_cmdshell") as ok:
            if not ok:
                raise RuntimeError("无法临时开启 xp_cmdshell，SQL 账号需要 sysadmin")
            # EncodedCommand 过长会被 xp_cmdshell（约 4000/8000 字）截断。
            # 优先写 .ps1；只有写出失败且命令够短时才改 EncodedCommand。
            wrote = False
            try:
                _sql_write_text_file(dbc, ps1, script)
                wrote = True
                lines = _sql_run(dbc, _ps_exe() + " -File \"" + ps1 + "\"")
            except Exception as write_err:  # noqa: BLE001
                if wrote or len(encoded) >= 3500:
                    raise
                log.warning("[remote] 写脚本失败，改 EncodedCommand: %s", write_err)
                lines = _sql_run(dbc, encoded)
            finally:
                try:
                    _sql_run(dbc, f'cmd.exe /c del /f /q "{ps1}"')
                except Exception:  # noqa: BLE001
                    pass
    remote = _parse_syno_upload_ok(lines, dest_dir, filename)
    log.info("[remote] SQL 本机直传完成 %s", remote)
    return remote


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
                log.info("[remote] 分块进度 %s / %s", wrote, actual)
        if wrote <= 0:
            raise RuntimeError("分块读取未得到数据")
        log.info("[remote] 分块拉取完成 %s 字节", wrote)


def _sql_file_length(dbc: Any, windows_path: str) -> int:
    p = windows_path.replace("'", "''")
    cmd = (
        _ps_exe() + " -Command "
        f"\"(Get-Item -LiteralPath '{p}').Length\""
    )
    for line in xp_cmdshell_lines(dbc, cmd):
        text = line.strip()
        if text.isdigit():
            return int(text)
    return 0


def _sql_read_chunk(dbc: Any, windows_path: str, offset: int, length: int) -> bytes:
    p = windows_path.replace("'", "''")
    cmd = (
        _ps_exe() + " -Command "
        f"\"$fs=[IO.File]::OpenRead('{p}');$fs.Position={int(offset)};"
        f"$b=New-Object byte[] {int(length)};$r=$fs.Read($b,0,{int(length)});$fs.Close();"
        "$s=[Convert]::ToBase64String($b,0,$r);"
        "for($i=0;$i -lt $s.Length;$i+=240){$s.Substring($i,[Math]::Min(240,$s.Length-$i))}\""
    )
    parts = [ln.strip() for ln in xp_cmdshell_lines(dbc, cmd) if re.fullmatch(r"[A-Za-z0-9+/=]+", ln.strip())]
    if not parts:
        return b""
    return base64.b64decode("".join(parts))
