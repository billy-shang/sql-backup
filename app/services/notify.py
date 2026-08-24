"""飞书、企业微信、钉钉自定义机器人通知。"""
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
    kind: str = "备份",
) -> dict[str, Any]:
    addr = f"{host}:{int(port)}" if host else "—"
    action = kind or "备份"
    if ok:
        title = f"✅ SQL Server {action}成功"
        color = "green"
    else:
        title = f"❌ SQL Server {action}失败"
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
            "text": {"tag": "lark_md", "content": f"**备份文件**\n{_grey(file_path)}"},
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


def build_wecom_markdown(
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
    kind: str = "备份",
) -> str:
    """构建企业微信群机器人 Markdown 消息。"""
    action = kind or "备份"
    status = f'<font color="info">{action}成功</font>' if ok else f'<font color="warning">{action}失败</font>'
    lines = [
        f"### {'✅' if ok else '❌'} SQL Server {status}",
        f"> 数据名称：{_plain(conn_name)}",
        f"> 数据地址：{_plain(f'{host}:{int(port)}' if host else '—')}",
        f"> 数据库：{_plain(database)}",
        f"> 时间：{_plain(when)}",
        f"> 备份文件：{_plain(file_path)}",
    ]
    if ok and size:
        lines.append(f"> 文件大小：{format_size(size)}")
    if not ok:
        lines.append(f"> 失败原因：<font color=\"warning\">{_plain(error or '未知错误')}</font>")
    return "\n".join(lines)


def enabled_channels(cfg: NotifyConfig) -> set[str]:
    raw = (getattr(cfg, "notify_channel", "") or "feishu").strip().lower()
    if raw == "both":
        return {"feishu", "wecom"}
    if raw in {"all", "feishu,wecom,dingtalk"}:
        return {"feishu", "wecom", "dingtalk"}
    allowed = {"feishu", "wecom", "dingtalk"}
    parts = {p.strip() for p in raw.replace(";", ",").split(",") if p.strip() in allowed}
    return parts or {"feishu"}


def send_wecom(webhook: str, content: str) -> None:
    """发送企业微信群机器人 Markdown 消息。"""
    url = (webhook or "").strip()
    if not url:
        log.info("[notify] 未配置企微 Webhook，跳过")
        return
    try:
        resp = httpx.post(
            url,
            json={"msgtype": "markdown", "markdown": {"content": content}},
            timeout=12.0,
        )
        log.info("[notify] 企微 HTTP %s", resp.status_code)
        body = (resp.text or "")[:400]
        if resp.status_code >= 400:
            log.warning("[notify] 企微返回: %s", body)
            return
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if int(data.get("errcode", 0) or 0) != 0:
            log.warning("[notify] 企微业务失败: %s", body)
    except Exception as e:  # noqa: BLE001
        log.warning("[notify] 企微发送失败: %s", e)


def build_dingtalk_markdown(
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
    kind: str = "备份",
) -> tuple[str, str]:
    """构建钉钉群机器人 Markdown：title + text。"""
    action = kind or "备份"
    title = f"SQL Server {action}成功" if ok else f"SQL Server {action}失败"
    lines = [
        f"### {'✅' if ok else '❌'} {title}",
        f"- 数据名称：{_plain(conn_name)}",
        f"- 数据地址：{_plain(f'{host}:{int(port)}' if host else '—')}",
        f"- 数据库：{_plain(database)}",
        f"- 时间：{_plain(when)}",
        f"- 备份文件：{_plain(file_path)}",
    ]
    if ok and size:
        lines.append(f"- 文件大小：{format_size(size)}")
    if not ok:
        lines.append(f"- 失败原因：{_plain(error or '未知错误')}")
    return title, "\n".join(lines)


def send_dingtalk(webhook: str, title: str, text: str) -> None:
    """发送钉钉自定义机器人 Markdown 消息。"""
    url = (webhook or "").strip()
    if not url:
        log.info("[notify] 未配置钉钉 Webhook，跳过")
        return
    try:
        resp = httpx.post(
            url,
            json={"msgtype": "markdown", "markdown": {"title": title, "text": text}},
            timeout=12.0,
        )
        log.info("[notify] 钉钉 HTTP %s", resp.status_code)
        body = (resp.text or "")[:400]
        if resp.status_code >= 400:
            log.warning("[notify] 钉钉返回: %s", body)
            return
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if int(data.get("errcode", 0) or 0) != 0:
            log.warning("[notify] 钉钉业务失败: %s", body)
    except Exception as e:  # noqa: BLE001
        log.warning("[notify] 钉钉发送失败: %s", e)


def _type_label(backup_type: str) -> str:
    return {"full": "完整", "diff": "差异", "log": "日志"}.get((backup_type or "full").lower(), backup_type or "完整")


def build_job_card(
    *,
    overall: str,
    conn_name: str,
    host: str,
    port: int,
    when: str,
    backup_type: str,
    ok_names: list[str],
    fail_lines: list[str],
    remote_fail: list[str],
    size: int,
) -> dict[str, Any]:
    addr = f"{host}:{int(port)}" if host else "—"
    if overall == "success":
        title = "✅ SQL Server 备份成功"
        color = "green"
    elif overall == "partial":
        title = "⚠️ SQL Server 备份部分成功"
        color = "orange"
    else:
        title = "❌ SQL Server 备份失败"
        color = "red"
    result = f"成功 {len(ok_names)}，失败 {len(fail_lines)}"
    if remote_fail:
        result += f"，归档失败 {len(remote_fail)}"
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "fields": [
                _kv("数据名称", conn_name),
                _kv("数据地址", addr),
                _kv("类型", _type_label(backup_type)),
                _kv("时间", when),
                _kv("结果", result),
                _kv("合计大小", format_size(size)),
            ],
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**成功库**\n{_grey('、'.join(ok_names) if ok_names else '无')}",
            },
        },
    ]
    if fail_lines:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**失败**\n{_grey(chr(10).join(fail_lines))}",
                },
            }
        )
    if remote_fail:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**群晖归档失败**\n{_grey('、'.join(remote_fail))}",
                },
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": elements,
    }


def build_job_wecom(
    *,
    overall: str,
    conn_name: str,
    host: str,
    port: int,
    when: str,
    backup_type: str,
    ok_names: list[str],
    fail_lines: list[str],
    remote_fail: list[str],
    size: int,
) -> str:
    if overall == "success":
        status = '<font color="info">备份成功</font>'
    elif overall == "partial":
        status = '<font color="warning">备份部分成功</font>'
    else:
        status = '<font color="warning">备份失败</font>'
    result = f"成功 {len(ok_names)}，失败 {len(fail_lines)}"
    lines = [
        f"### {'✅' if overall == 'success' else '⚠️' if overall == 'partial' else '❌'} SQL Server {status}",
        f"> 数据名称：{_plain(conn_name)}",
        f"> 数据地址：{_plain(f'{host}:{int(port)}' if host else '—')}",
        f"> 类型：{_plain(_type_label(backup_type))}",
        f"> 时间：{_plain(when)}",
        f"> 结果：{_plain(result)}",
        f"> 合计大小：{format_size(size)}",
        f"> 成功库：{_plain('、'.join(ok_names) if ok_names else '无')}",
    ]
    if fail_lines:
        lines.append(f"> 失败：{_plain('；'.join(fail_lines))}")
    if remote_fail:
        lines.append(f"> 群晖归档失败：{_plain('、'.join(remote_fail))}")
    return "\n".join(lines)


def build_job_dingtalk(
    *,
    overall: str,
    conn_name: str,
    host: str,
    port: int,
    when: str,
    backup_type: str,
    ok_names: list[str],
    fail_lines: list[str],
    remote_fail: list[str],
    size: int,
) -> tuple[str, str]:
    title = {
        "success": "SQL Server 备份成功",
        "partial": "SQL Server 备份部分成功",
        "failed": "SQL Server 备份失败",
    }.get(overall, "SQL Server 备份")
    mark = "✅" if overall == "success" else "⚠️" if overall == "partial" else "❌"
    result = f"成功 {len(ok_names)}，失败 {len(fail_lines)}"
    lines = [
        f"### {mark} {title}",
        f"- 数据名称：{_plain(conn_name)}",
        f"- 数据地址：{_plain(f'{host}:{int(port)}' if host else '—')}",
        f"- 类型：{_plain(_type_label(backup_type))}",
        f"- 时间：{_plain(when)}",
        f"- 结果：{_plain(result)}",
        f"- 合计大小：{format_size(size)}",
        f"- 成功库：{_plain('、'.join(ok_names) if ok_names else '无')}",
    ]
    if fail_lines:
        lines.append(f"- 失败：{_plain('；'.join(fail_lines))}")
    if remote_fail:
        lines.append(f"- 群晖归档失败：{_plain('、'.join(remote_fail))}")
    return title, "\n".join(lines)


def notify_job_result(
    db: Session,
    *,
    conn_name: str,
    host: str,
    port: int,
    when: str,
    backup_type: str,
    recs: list[Any],
) -> None:
    """一次备份任务只发一条汇总，不再按库刷屏。"""
    cfg = get_notify(db)
    if not cfg.enabled:
        return
    ok_recs = [r for r in recs if getattr(r, "status", "") == "success"]
    fail_recs = [r for r in recs if getattr(r, "status", "") != "success"]
    remote_fail = [
        (getattr(r, "dbname", "") or "").strip()
        for r in ok_recs
        if (getattr(r, "remote_status", "") or "") == "failed"
    ]
    remote_fail = [n for n in remote_fail if n]
    if not recs or (fail_recs and not ok_recs):
        overall = "failed"
    elif fail_recs or remote_fail:
        overall = "partial"
    else:
        overall = "success"
    if overall == "success" and not cfg.notify_on_success:
        return
    if overall != "success" and not cfg.notify_on_fail:
        return
    channels = enabled_channels(cfg)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    ok_names = [(getattr(r, "dbname", "") or "").strip() or "?" for r in ok_recs]
    fail_lines = []
    for r in fail_recs:
        name = (getattr(r, "dbname", "") or "").strip() or "?"
        err = (getattr(r, "error_message", "") or "未知错误").strip()
        fail_lines.append(f"{name}：{err}")
    size = sum(int(getattr(r, "file_size", 0) or 0) for r in ok_recs)
    payload = {
        "overall": overall,
        "conn_name": conn_name,
        "host": host,
        "port": port,
        "when": when,
        "backup_type": backup_type,
        "ok_names": ok_names,
        "fail_lines": fail_lines,
        "remote_fail": remote_fail,
        "size": size,
    }
    log.info(
        "[notify] 发送任务汇总 overall=%s ok=%s fail=%s conn=%s",
        overall,
        len(ok_names),
        len(fail_lines),
        conn_name,
    )
    if "feishu" in channels:
        send_feishu(cfg.feishu_webhook, {"msg_type": "interactive", "card": build_job_card(**payload)})
    if "wecom" in channels:
        send_wecom(getattr(cfg, "wecom_webhook", "") or "", build_job_wecom(**payload))
    if "dingtalk" in channels:
        title, text = build_job_dingtalk(**payload)
        send_dingtalk(getattr(cfg, "dingtalk_webhook", "") or "", title, text)


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
    kind: str = "备份",
) -> None:
    cfg = get_notify(db)
    if not cfg.enabled:
        return
    if ok and not cfg.notify_on_success:
        return
    if not ok and not cfg.notify_on_fail:
        return
    channels = enabled_channels(cfg)
    feishu_hook = cfg.feishu_webhook
    wecom_hook = getattr(cfg, "wecom_webhook", "") or ""
    dingtalk_hook = getattr(cfg, "dingtalk_webhook", "") or ""
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    common = {
        "ok": ok,
        "conn_name": conn_name,
        "host": host,
        "port": port,
        "database": database,
        "when": when,
        "file_path": file_path,
        "size": size,
        "error": error,
        "kind": kind or "备份",
    }
    if "feishu" in channels:
        card = build_backup_card(**common)
        log.info("[notify] 发送飞书卡片 ok=%s db=%s conn=%s", ok, database, conn_name)
        send_feishu(feishu_hook, {"msg_type": "interactive", "card": card})
    if "wecom" in channels:
        content = build_wecom_markdown(**common)
        log.info("[notify] 发送企微消息 ok=%s db=%s conn=%s", ok, database, conn_name)
        send_wecom(wecom_hook, content)
    if "dingtalk" in channels:
        title, text = build_dingtalk_markdown(**common)
        log.info("[notify] 发送钉钉消息 ok=%s db=%s conn=%s", ok, database, conn_name)
        send_dingtalk(dingtalk_hook, title, text)
