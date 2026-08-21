from __future__ import annotations

from fastapi import APIRouter

from app.deps import AdminUser, CurrentUser, DbSess
from app.schemas import NotifyIn, NotifyOut
from app.services.notify import get_notify

router = APIRouter(prefix="/api/notify", tags=["notify"])


@router.get("")
def get_cfg(_user: CurrentUser, db: DbSess) -> dict:
    row = get_notify(db)
    return {"ok": True, "item": NotifyOut.model_validate(row).model_dump()}


@router.put("")
def put_cfg(_admin: AdminUser, body: NotifyIn, db: DbSess) -> dict:
    row = get_notify(db)
    row.feishu_webhook = (body.feishu_webhook or "").strip()
    row.wecom_webhook = (body.wecom_webhook or "").strip()
    row.notify_channel = body.notify_channel
    row.enabled = bool(body.enabled)
    row.notify_on_success = bool(body.notify_on_success)
    row.notify_on_fail = bool(body.notify_on_fail)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": NotifyOut.model_validate(row).model_dump()}
