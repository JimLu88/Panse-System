"""营销活动价保方案3加强版。

- 每场活动默认19天，运营提供该活动价保说明链接后可手动修正；
- 链接缺失只提醒，不猜规则、不阻断其余价格安全门；
- 历史价格线冲突的商品按整品暂缓，其他商品可以继续；
- SKU身份轮换默认关闭，后续必须由用户重新拍板后显式开启。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignPlan

DEFAULT_PRICE_PROTECTION_DAYS = 19
ROTATION_ENABLED_KEY = "promo_sku_rotation_enabled"


def protection_days(plan) -> int:
    try:
        days = int(getattr(plan, "price_protection_days", None) or DEFAULT_PRICE_PROTECTION_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_PRICE_PROTECTION_DAYS
    return min(max(days, 1), 365)


def protection_until(plan):
    end_at = getattr(plan, "end_at", None)
    return end_at + timedelta(days=protection_days(plan)) if end_at else None


def rule_check(plan) -> dict:
    url = str(getattr(plan, "price_protection_rule_url", None) or "").strip()
    days = protection_days(plan)
    until = protection_until(plan)
    item = {
        "days": days,
        "rule_url": url or None,
        "confirmed_at": (
            getattr(plan, "price_protection_confirmed_at", None).isoformat(sep=" ")
            if getattr(plan, "price_protection_confirmed_at", None) else None
        ),
        "default_used": not bool(url),
        "cooldown_until": until.isoformat(sep=" ") if until else None,
    }
    return {
        "rule": "R14",
        "level": "pass" if url else "warn",
        "title": (
            f"价保规则已按活动说明确认：{days}天"
            if url else f"尚未提供本场价保说明链接：暂按默认{days}天，已进入运营提醒"
        ),
        "items": [item],
    }


def rotation_enabled(db: Session) -> bool:
    from app.services import settings_service

    raw = settings_service.get(db, ROTATION_ENABLED_KEY, env_fallback=False)
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def rotation_block_result() -> dict:
    return {
        "ok": False,
        "blocked_by": "price_protection_policy",
        "error": (
            "价保方案3加强版已启用：SKU身份轮换默认关闭。"
            "保持真实SKU和定制SKU身份不变；历史价格线冲突商品进入等待/人工决策。"
        ),
    }


def _notice_key(plan_id: int) -> str:
    return f"campaign_price_rule_notice_{plan_id}"


def notify_rule_link_needed(db: Session, plan, *, force: bool = False) -> dict:
    """缺链接时给运营发一次飞书；未送达不记去重，后续调度会重试。"""
    from app.services import campaign_notification_service as notify_service, settings_service

    if str(getattr(plan, "price_protection_rule_url", None) or "").strip():
        return {"needed": False, "sent": False, "reason": "rule_url_present"}
    days = protection_days(plan)
    text = (
        f"活动：{getattr(plan, 'name', '')}\n"
        f"千牛活动：{getattr(plan, 'qn_campaign_title', None) or getattr(plan, 'name', '')}\n"
        f"档期：{getattr(plan, 'start_at', None)} 至 {getattr(plan, 'end_at', None)}\n"
        f"当前临时价保冷静期：{days}天（系统可手动修改）\n"
        "请打开千牛本场活动的「价保说明/价保服务」页面，把页面链接发给运营负责人或 Codex。"
        "确认前系统不会猜价保期限，也不会启用SKU身份轮换。"
    )
    signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = _notice_key(int(getattr(plan, "id", 0) or 0))
    if not force and settings_service.get(db, key, env_fallback=False) == signature:
        return {"needed": True, "sent": False, "deduped": True}
    delivered = notify_service.broadcast_text(
        db, text, title="活动价保规则待确认", level="warning")
    sent = any(v is True for v in delivered.values())
    if sent:
        settings_service.set_value(
            db, key, signature, description="活动价保说明链接提醒去重签名")
        db.commit()
    return {"needed": True, "sent": sent, "delivery": delivered}


def remind_upcoming_missing_rules(db: Session, *, horizon_days: int = 14) -> dict:
    now = datetime.now()
    horizon = now + timedelta(days=horizon_days)
    plans = db.execute(select(CampaignPlan).where(
        CampaignPlan.start_at >= now,
        CampaignPlan.start_at <= horizon,
        CampaignPlan.price_protection_rule_url.is_(None),
    ).order_by(CampaignPlan.start_at)).scalars().all()
    sent = deduped = 0
    details = []
    for plan in plans:
        result = notify_rule_link_needed(db, plan)
        sent += int(bool(result.get("sent")))
        deduped += int(bool(result.get("deduped")))
        details.append({"plan_id": plan.id, **result})
    return {"scanned": len(plans), "sent": sent, "deduped": deduped, "details": details}
