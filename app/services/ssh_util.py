"""从配置中心的 SSH 代理解析跳板凭据。"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple

from sqlalchemy.orm import object_session

from app.security import decrypt_secret

log = logging.getLogger(__name__)


class SshParams(NamedTuple):
    host: str
    port: int
    user: str
    password: str
    key: str


def ssh_params(conn_row: Any, db: Any = None) -> SshParams:
    """优先用配置中心的 SSH 代理；没有代理时回退到连接上旧的手填字段。"""
    pid = int(getattr(conn_row, "ssh_proxy_id", 0) or 0)
    if pid:
        from app.models import SshProxy

        sess = db if db is not None else object_session(conn_row)
        proxy = None
        if sess is not None:
            proxy = sess.query(SshProxy).filter(SshProxy.id == pid).one_or_none()
        if proxy is None:
            raise RuntimeError("所选 SSH 代理不存在，请到「配置中心」重新选择")
        host = (proxy.host or "").strip()
        user = (proxy.username or "").strip()
        if not host or not user:
            raise RuntimeError("SSH 代理未填写地址或用户名")
        log.info("[ssh] 使用配置中心代理 id=%s %s@%s:%s", proxy.id, user, host, proxy.port)
        return SshParams(
            host=host,
            port=int(proxy.port or 22),
            user=user,
            password=decrypt_secret(proxy.password_enc),
            key=proxy.key or "",
        )
    host = (getattr(conn_row, "ssh_host", "") or getattr(conn_row, "host", "") or "").strip()
    user = (getattr(conn_row, "ssh_user", "") or "").strip()
    if not host or not user:
        raise RuntimeError("SSH 模式请先在「配置中心」添加跳板，再在连接里选择")
    log.info("[ssh] 使用连接内旧字段 %s@%s:%s", user, host, getattr(conn_row, "ssh_port", 22))
    return SshParams(
        host=host,
        port=int(getattr(conn_row, "ssh_port", 22) or 22),
        user=user,
        password=decrypt_secret(getattr(conn_row, "ssh_password_enc", "") or ""),
        key=getattr(conn_row, "ssh_key", "") or "",
    )
