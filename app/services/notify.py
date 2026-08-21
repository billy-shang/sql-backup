"""飞书自定义机器人通知（卡片：成功绿色 / 失败红色）。"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import NotifyConfig

log = logging.getLogger(__name__)


def get_notify(db: Session) -> NotifyConfig:
    row = db.query(NotifyConfig).filter(NotifyConfig.id == 1).one_or_none()
    if row:
        return row
    row = NotifyConfig(id=1)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def format_size(n: int) -> str:
    if n <= 0:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{n}B"


def _plain(value: Any) -> str:
    """飞书 lark_md 纯文本，转义以免被当成斜体。"""
    text = str(value or "").strip() or "—"
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "")


def _grey(value: Any) -> str:
    return f"<font color='grey'>{_plain(value)}</font>"


def _kv(label: str, value: str, short: bool = True) -> dict[str, Any]:
    return {
        "is_short": short,
        "text": {"tag": "lark_md", "content": f"**{label}**\n{_grey(value)}"},
    }


def build_backup_card(
    *,
    ok: bool,
    conn_name: str,
    host: str,
    port: int,
    database: str,
    when: str,
    file_path: str = "",
    size: int = 0,
    error: str = "",
) -> dict[str, Any]:
    addr = f"{host}:{int(port)}" if host else "—"
    if ok:
        title = "✅ SQL Server 备份成功"
        color = "green"
    else:
        title = "❌ SQL Server 备份失败"
        color = "red"
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "fields": [
                _kv("数据名称", conn_name),
                _kv("数据地址", addr),
                _kv("数据库", database),
                _kv("时间", when),
            ],
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**备份路径**\n{_grey(file_path)}"},
        },
    ]
    if not ok:
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**失败原因**\n{_grey(error or '未知错误')}"},
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": elements,
    }


def send_feishu(webhook: str, payload: dict[str, Any]) -> None:
    url = (webhook or "").strip()
    if not url:
        log.info("[notify] 未配置飞书 Webhook，跳过")
        return
    try:
        resp = httpx.post(url, json=payload, timeout=12.0)
        log.info("[notify] 飞书 HTTP %s", resp.status_code)
        body = (resp.text or "")[:400]
        if resp.status_code >= 400:
            log.warning("[notify] 飞书返回: %s", body)
        else:
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
            code = data.get("code", data.get("Code", data.get("StatusCode", 0)))
            try:
                if int(code or 0) != 0:
                    log.warning("[notify] 飞书业务失败: %s", body)
            except (TypeError, ValueError):
                pass
    except Exception as e:  # noqa: BLE001
        log.warning("[notify] 飞书发送失败: %s", e)


def notify_backup_result(
    db: Session,
    *,
    ok: bool,
    database: str,
    when: str,
    conn_name: str = "",
    host: str = "",
    port: int = 1433,
    file_path: str = "",
    size: int = 0,
    error: str = "",
) -> None:
    cfg = get_notify(db)
    if not cfg.enabled:
        return
    if ok and not cfg.notify_on_success:
        return
    if not ok and not cfg.notify_on_fail:
        return
    card = build_backup_card(
        ok=ok,
        conn_name=conn_name,
        host=host,
        port=port,
        database=database,
        when=when,
        file_path=file_path,
        size=size,
        error=error,
    )
    log.info("[notify] 发送飞书卡片 ok=%s db=%s conn=%s", ok, database, conn_name)
    send_feishu(cfg.feishu_webhook, {"msg_type": "interactive", "card": card})
