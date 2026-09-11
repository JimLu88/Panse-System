"""营销活动全自动执行。

发现阶段只读锁定父活动、子阶段、秒级档期、官方力度和活动 ID；缺一项就停并通知。
新版执行规则见 docs/campaign-continuous-flow-20260911.json；旧预检执行链已退役。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignCalendar, CampaignPlan

_ACTIONABLE = ("可报名", "报名中")
_TERMINAL = ("已结束", "报名截止", "已关闭", "已取消")
_EXACT_DT_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}:\d{2}")
AUTO_EXECUTION_HORIZON_DAYS = 14


def enabled(db: Session) -> bool:
    from app.services import settings_service
    raw = settings_service.get(db, "campaign_auto_enabled", env_fallback=False)
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _normal_dt(text: str) -> Optional[datetime]:
    text = re.sub(r"[-/.]", "-", text.strip())
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _parent_title(calendar: CampaignCalendar) -> str:
    raw_lines = [x.strip() for x in str(getattr(calendar, "_raw", "") or "").splitlines()
                 if x.strip()]
    for line in raw_lines:
        if "平台大促" in line or ("淘宝" in line and "大促" in line):
            return line[:255]
    return calendar.title


def _campaign_type(parent: str, phase: str) -> str:
    text = f"{parent} {phase}"
    if "超级立减" in text:
        return "super_reduce"
    if "618" in text:
        return "big618"
    if "双11" in text or "11.11" in text:
        return "big11"
    if "38" in text:
        return "big38"
    if "88" in text:
        return "big88"
    return "big_other"


def _notify_once(db: Session, key_suffix: str, title: str, text: str,
                 *, level: str = "error") -> dict:
    from app.services import campaign_notification_service as notify_service, settings_service
    signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = f"campaign_auto_notice_{key_suffix}"[:120]
    if settings_service.get(db, key, env_fallback=False) == signature:
        return {"deduped": True}
    result = notify_service.broadcast_text(db, text, title=title, level=level)
    if any(v is True for v in result.values()):
        settings_service.set_value(db, key, signature,
                                   description="营销活动自动化通知去重签名")
        db.commit()
    return result


def _extract_exact_window(body: str, calendar: CampaignCalendar) -> tuple[
        Optional[datetime], Optional[datetime]]:
    values = [dt for dt in (_normal_dt(x) for x in _EXACT_DT_RE.findall(body)) if dt]
    if calendar.start_at:
        values = [dt for dt in values if dt.date() >= calendar.start_at.date()]
    start = next(
        (dt for dt in values
         if not calendar.start_at or dt.date() == calendar.start_at.date()),
        None)
    end = next(
        (dt for dt in values
         if calendar.end_at and dt.date() == calendar.end_at.date() and dt != start),
        None)
    if start and end and start < end:
        return start, end
    return None, None


def sync_upcoming_plans(db: Session, calendars: list[CampaignCalendar]) -> dict:
    """把发现到的近期可报名阶段变成可执行计划；详情安全门不完整则不建计划。"""
    from app.services import campaign_service, web_agent_service

    created = existing = blocked = 0
    details: list[dict] = []
    today = datetime.now().date()
    for calendar in calendars:
        status = str(calendar.status or "")
        if any(word in status for word in _TERMINAL):
            continue
        if not any(word in status for word in _ACTIONABLE):
            continue
        if calendar.start_at is None:
            continue
        days_left = (calendar.start_at.date() - today).days
        if not (0 <= days_left <= 14):
            continue
        plan = db.execute(select(CampaignPlan).where(
            CampaignPlan.name == calendar.title,
            CampaignPlan.start_at >= datetime.combine(calendar.start_at.date(), datetime.min.time()),
            CampaignPlan.start_at < datetime.combine(
                calendar.start_at.date() + timedelta(days=1), datetime.min.time()),
        )).scalars().first()
        if plan is not None:
            existing += 1
            continue

        # CampaignCalendar 不持久化 raw，发现调用方临时挂在实例上供这里提取父标题。
        parent = _parent_title(calendar)
        inspected = web_agent_service.campaign_inspect_detail(db, parent)
        if not inspected.get("ok"):
            blocked += 1
            reason = inspected.get("error") or "活动详情读取失败"
            _notify_once(
                db, f"inspect_{calendar.id}", "活动自动计划未创建",
                f"阶段：{calendar.title}\n父活动：{parent}\n原因：{reason}\n"
                "系统没有秒级档期和活动 ID，已停止，不会猜时间或盲目报名。",
            )
            details.append({"calendar_id": calendar.id, "ok": False, "error": reason})
            continue
        body = str(inspected.get("body_text") or "")
        start, end = _extract_exact_window(body, calendar)
        ctype = _campaign_type(parent, calendar.title)
        expected_rate = campaign_service.TIER_LEVERAGE[
            campaign_service.CAMPAIGN_TYPES[ctype][1]]
        rate_text = f"{int(expected_rate * 100)}%"
        url = str(inspected.get("url") or "")
        cid = re.search(r"(?:[?&])campaignId=(\d+)", url)
        uid = re.search(r"(?:[?&])unitedActivityId=(\d+)", url)
        missing = []
        if not start or not end:
            missing.append("秒级档期")
        if rate_text not in body:
            missing.append(f"官方力度{rate_text}")
        if not cid:
            missing.append("campaignId")
        if not uid:
            missing.append("unitedActivityId")
        if calendar.title not in body:
            missing.append("子阶段名称")
        if missing:
            blocked += 1
            reason = "、".join(missing)
            _notify_once(
                db, f"guard_{calendar.id}", "活动自动计划安全门失败",
                f"阶段：{calendar.title}\n父活动：{parent}\n缺少/不匹配：{reason}\n"
                "系统已停止，不会猜活动、猜力度或猜档期。",
            )
            details.append({"calendar_id": calendar.id, "ok": False, "error": reason})
            continue
        tier = campaign_service.CAMPAIGN_TYPES[ctype][1]
        plan = CampaignPlan(
            name=calendar.title,
            workflow_key=(
                f"campaign:auto:{cid.group(1)}:{uid.group(1)}:"
                f"{start.strftime('%Y%m%d%H%M%S')}"
            ),
            campaign_type=ctype,
            tier=tier,
            start_at=start,
            end_at=end,
            qn_campaign_title=parent,
            status="draft",
            remark=(f"auto-discovery; campaignId={cid.group(1)}; "
                    f"unitedActivityId={uid.group(1)}; official_rate={rate_text}"),
        )
        db.add(plan)
        db.commit()
        created += 1
        details.append({"calendar_id": calendar.id, "plan_id": plan.id, "ok": True})
    return {"created": created, "existing": existing, "blocked": blocked, "details": details}


def run_auto_execute(db: Session) -> dict:
    """New scheduled entry. Never fall back to the retired preflight pipeline.

    Existing separately claimed business batches retain their original receipt
    paths. New automation waits for the verified continuous Web-Agent binding.
    """
    from app.services import campaign_continuous_runtime

    if not enabled(db):
        return {"skipped": "campaign_auto_disabled"}
    result = campaign_continuous_runtime.readiness(db)
    reason = result.get("error") or "continuous_execution_binding_not_installed"
    notice = _notify_once(
        db, "continuous_transport", "活动自动报名接入待完成",
        "新版连续报名尚未通过真实 Web-Agent 接入验收。"
        "旧预检报名链已停用，不会回退或重复提交。\n"
        f"原因：{reason}\n需要程序维护完成接入，不是要求逐个商品人工报名。",
    )
    return {"processed": 0, "failed": 0, "blocked": 1,
            "step": "continuous_transport", "error": reason,
            "legacy_fallback": False, "platform_write": False,
            "notification": notice, "transport": result}
