"""群晖 File Station 上传。登录用 POST；上传把 SID 放在 URL，并带 SynoToken。"""
from __future__ import annotations

import json
import logging
import posixpath
import time
from datetime import datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_FS_ERR = {
    100: "未知错误",
    101: "无效参数",
    102: "API 不存在",
    103: "方法不存在",
    104: "版本不支持",
    105: "权限不足",
    106: "会话超时",
    107: "会话被重复登录中断",
    119: "SID 无效（会话丢失）",
    400: "文件操作参数无效（检查远程目录是否为已有共享下的路径）",
    401: "文件操作未知错误",
    406: "没有此文件或目录",
    408: "文件已存在",
    414: "文件已存在",
    418: "文件名过长",
    419: "文件名非法",
}


def _base_url(host: str, port: int, https: bool) -> str:
    scheme = "https" if https else "http"
    return f"{scheme}://{host}:{int(port)}"


def _fs_path(remote_dir: str) -> str:
    p = (remote_dir or "/sql_backup").replace("\\", "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/sql_backup"


def _err_text(data: dict) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        code = err.get("code")
        hint = _FS_ERR.get(int(code), "") if str(code).isdigit() or isinstance(code, int) else ""
        return f"code={code} {hint}".strip()
    return str(err or data)[:400]


class SynologyClient:
    def __init__(self, host: str, port: int, username: str, password: str, https: bool = True) -> None:
        self.host = (host or "").strip()
        self.port = int(port or 5001)
        self.username = username
        self.password = password
        self.https = bool(https)
        self.sid = ""
        self.syno_token = ""
        self._client = httpx.Client(
            verify=False,
            timeout=httpx.Timeout(30.0, read=3600.0),
            follow_redirects=True,
        )

    def _headers(self) -> dict[str, str]:
        h = {}
        if self.syno_token:
            h["X-SYNO-TOKEN"] = self.syno_token
        return h

    def close(self) -> None:
        try:
            if self.sid:
                self._client.get(
                    f"{_base_url(self.host, self.port, self.https)}/webapi/auth.cgi",
                    params={
                        "api": "SYNO.API.Auth",
                        "version": "1",
                        "method": "logout",
                        "session": "FileStation",
                        "_sid": self.sid,
                    },
                    headers=self._headers(),
                )
        except Exception:  # noqa: BLE001
            pass
        self._client.close()

    def login(self) -> None:
        url = f"{_base_url(self.host, self.port, self.https)}/webapi/auth.cgi"
        last: Exception | None = None
        for ver in (7, 6, 3, 2):
            try:
                resp = self._client.post(
                    url,
                    data={
                        "api": "SYNO.API.Auth",
                        "version": str(ver),
                        "method": "login",
                        "account": self.username,
                        "passwd": self.password,
                        "session": "FileStation",
                        "format": "sid",
                        "enable_syno_token": "yes",
                    },
                )
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("[synology] 登录 version=%s 失败: %s", ver, e)
                continue
            if data.get("success") and data.get("data", {}).get("sid"):
                payload = data.get("data") or {}
                self.sid = str(payload.get("sid") or "")
                self.syno_token = str(payload.get("synotoken") or payload.get("SynoToken") or "")
                self._client.cookies.set("id", self.sid)
                if self.syno_token:
                    self._client.cookies.set("syno_token", self.syno_token)
                log.info("[synology] File Station 登录成功 %s:%s token=%s", self.host, self.port, bool(self.syno_token))
                return
            last = RuntimeError(_err_text(data))
        raise RuntimeError(f"群晖登录失败：{last}")

    def mkdir(self, folder: str) -> None:
        """按层级创建目录；共享根目录创建失败可忽略。"""
        parts = [p for p in _fs_path(folder).split("/") if p]
        cur = ""
        for i, part in enumerate(parts):
            parent = cur or "/"
            cur = f"{cur}/{part}"
            try:
                resp = self._client.get(
                    f"{_base_url(self.host, self.port, self.https)}/webapi/entry.cgi",
                    params={
                        "api": "SYNO.FileStation.CreateFolder",
                        "version": "2",
                        "method": "create",
                        "folder_path": parent,
                        "name": part,
                        "force_parent": "true",
                        "_sid": self.sid,
                    },
                    headers=self._headers(),
                )
                data = resp.json()
                if data.get("success"):
                    continue
                code = (data.get("error") or {}).get("code")
                # 408/414 已存在；400 常见于共享根或目录已存在
                if code in {400, 408, 414, 418}:
                    if i == 0:
                        log.info("[synology] 跳过共享根目录 %s: %s", cur, _err_text(data))
                    continue
                log.info("[synology] 创建目录 %s: %s", cur, _err_text(data))
            except Exception as e:  # noqa: BLE001
                log.info("[synology] 创建目录失败 %s: %s", cur, e)

    def upload(self, local_file: Path, dest_dir: str, filename: str) -> str:
        dest_dir = _fs_path(dest_dir)
        self.mkdir(dest_dir)
        url = f"{_base_url(self.host, self.port, self.https)}/webapi/entry.cgi"
        params = {
            "api": "SYNO.FileStation.Upload",
            "version": "2",
            "method": "upload",
            "_sid": self.sid,
        }
        last: Exception | None = None
        for ver in ("3", "2"):
            params["version"] = ver
            try:
                with local_file.open("rb") as fh:
                    files = {"file": (filename, fh, "application/octet-stream")}
                    data = {
                        "path": dest_dir,
                        "create_parents": "true",
                        "overwrite": "true",
                    }
                    resp = self._client.post(
                        url,
                        params=params,
                        data=data,
                        files=files,
                        headers=self._headers(),
                    )
                body = resp.json()
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("[synology] 上传 version=%s 异常: %s", ver, e)
                continue
            if body.get("success"):
                remote = posixpath.join(dest_dir, filename)
                log.info("[synology] 已上传 %s", remote)
                return remote
            last = RuntimeError(_err_text(body))
            log.info("[synology] 上传 version=%s 失败: %s", ver, last)
        raise RuntimeError(f"群晖上传失败：{last}")

    def list_names(self, folder: str) -> list[str]:
        resp = self._client.get(
            f"{_base_url(self.host, self.port, self.https)}/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.List",
                "version": "2",
                "method": "list",
                "folder_path": _fs_path(folder),
                "_sid": self.sid,
            },
            headers=self._headers(),
        )
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"列举群晖目录失败：{_err_text(data)}")
        files = ((data.get("data") or {}).get("files") or [])
        names: list[str] = []
        for item in files:
            if item.get("isdir") or item.get("is_dir"):
                names.append(str(item.get("name") or ""))
        return [n for n in names if n]

    def delete_path(self, path: str) -> None:
        target = _fs_path(path)
        resp = self._client.get(
            f"{_base_url(self.host, self.port, self.https)}/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.Delete",
                "version": "2",
                "method": "start",
                "path": json.dumps([target]),
                "accurate_progress": "true",
                "_sid": self.sid,
            },
            headers=self._headers(),
        )
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"删除群晖路径失败 {target}：{_err_text(data)}")
        taskid = str((data.get("data") or {}).get("taskid") or "")
        if not taskid:
            return
        for _ in range(40):
            st = self._client.get(
                f"{_base_url(self.host, self.port, self.https)}/webapi/entry.cgi",
                params={
                    "api": "SYNO.FileStation.Delete",
                    "version": "2",
                    "method": "status",
                    "taskid": taskid,
                    "_sid": self.sid,
                },
                headers=self._headers(),
            ).json()
            if st.get("success") and (st.get("data") or {}).get("finished"):
                err = (st.get("data") or {}).get("error")
                if err:
                    raise RuntimeError(f"删除群晖路径失败 {target}：{err}")
                return
            time.sleep(0.4)
        log.warning("[synology] 删除任务未在时限内结束 %s", target)


def test_synology(host: str, port: int, username: str, password: str, https: bool, remote_dir: str) -> str:
    cli = SynologyClient(host, port, username, password, https)
    try:
        cli.login()
        cli.mkdir(_fs_path(remote_dir))
        return f"群晖 File Station 连接成功：{host}:{port}"
    finally:
        cli.close()


def upload_to_synology(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    https: bool,
    remote_dir: str,
    local_file: Path,
    dest_subdir: str,
    filename: str,
) -> str:
    dest = posixpath.join(_fs_path(remote_dir), dest_subdir.replace("\\", "/").strip("/"))
    cli = SynologyClient(host, port, username, password, https)
    try:
        cli.login()
        return cli.upload(local_file, dest, filename)
    finally:
        cli.close()


def cleanup_synology_days(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    https: bool,
    remote_dir: str,
    dest_subdir: str,
    retain_days: int,
) -> None:
    """删除群晖 连接名/库名 下超过保留天数的 YYYY-MM-DD 目录。"""
    if retain_days <= 0:
        return
    parent = posixpath.join(_fs_path(remote_dir), dest_subdir.replace("\\", "/").strip("/"))
    today = datetime.now().astimezone().date()
    cli = SynologyClient(host, port, username, password, https)
    try:
        cli.login()
        names = cli.list_names(parent)
        expired = []
        for name in names:
            try:
                day = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if (today - day).days >= max(int(retain_days), 1):
                expired.append(name)
        if not expired:
            log.info("[synology] 没有超过 %s 天的目录 %s", retain_days, parent)
            return
        for name in expired:
            folder = posixpath.join(parent, name)
            try:
                cli.delete_path(folder)
                log.info("[synology] 已删除过期目录 %s", folder)
            except Exception as e:  # noqa: BLE001
                log.warning("[synology] 删除过期目录失败 %s: %s", folder, e)
    finally:
        cli.close()
