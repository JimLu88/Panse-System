"""营销活动全自动执行。

发现阶段只读锁定父活动、子阶段、秒级档期、官方力度和活动 ID；缺一项就停并飞书说明。
执行阶段按 计划预检 → 单品立减 → 当前活动导出差集 → 活动报名 → 自动核对 推进。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignCalendar, CampaignPlan

_ACTIONABLE = ("可报名", "报名中")
_TERMINAL = ("已结束", "报名截止", "已关闭", "已取消")
_EXACT_DT_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}:\d{2}")


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
    """执行未来 7 天内的自动计划；每个阶段只在上一步有终态成功后继续。"""
    from app.services import campaign_service

    if not enabled(db):
        return {"skipped": "campaign_auto_disabled"}
    now = datetime.now()
    horizon = now + timedelta(days=7)
    plans = db.execute(select(CampaignPlan).where(
        CampaignPlan.status.in_(("draft", "precheck", "discount_pushed")),
        CampaignPlan.start_at > now,
        CampaignPlan.start_at <= horizon,
    ).order_by(CampaignPlan.start_at)).scalars().all()
    processed = succeeded = failed = held = 0
    details: list[dict] = []
    for plan in plans:
        processed += 1
        # 每次执行前刷新 60 天动销登记；已登记但后来出单的仍待人工转正，不自动报名。
        grouping = campaign_service.group_by_sales(db)
        if plan.status == "draft":
            floor_refresh = campaign_service.refresh_floor_evidence_from_current_activity(db, plan)
            if not floor_refresh.get("ok"):
                failed += 1
                plan.status = "alarmed"
                db.commit()
                text = (
                    f"活动：{plan.name}\n失败步骤：价格线证据刷新\n"
                    f"原因：{floor_refresh.get('error') or '无法导出当前活动'}\n"
                    "系统已停止并等待用户决定；不会生成报名表、自动改价或自动重试。")
                notice = _notify_once(
                    db, f"floor_refresh_{plan.id}", "活动自动执行失败", text)
                details.append({
                    "plan_id": plan.id, "ok": False,
                    "step": "floor_evidence_refresh", "notification": notice,
                })
                continue
            checks = campaign_service.preflight(db, plan)
            critical = [c for c in checks if c.get("level") == "error"]
            if critical:
                failed += 1
                plan.status = "alarmed"
                db.commit()
                text = (
                    f"活动：{plan.name}\n失败步骤：ERP 预检\n"
                    f"原因：{json.dumps(critical, ensure_ascii=False, default=str)[:2200]}\n"
                    "系统已停止并标记为待人工决定；不会继续生成、上传、自动改价或自动重试。")
                notice = _notify_once(db, f"precheck_{plan.id}", "活动自动执行失败", text)
                details.append({"plan_id": plan.id, "ok": False,
                                "step": "precheck", "notification": notice})
                continue
            price_holds = campaign_service.price_hold_items(db, plan)
            if price_holds:
                hold_text = (
                    f"活动：{plan.name}\n"
                    f"本次有 {len(price_holds)} 个商品命中历史价格线，已从报名表和同期单品立减表整品暂缓；"
                    "其余商品继续自动报名。\n"
                    f"明细：{json.dumps(price_holds, ensure_ascii=False, default=str)[:2600]}\n"
                    f"当前暂按 {getattr(plan, 'price_protection_days', None) or 19} 天冷静期。"
                    "请提供本场价保说明链接；若要承担亏损提前报名，必须人工明确批准。"
                )
                _notify_once(
                    db, f"price_hold_{plan.id}", "活动商品因价保/历史价暂缓",
                    hold_text, level="warning")
                signup_rows, _ = campaign_service.build_signup_rows(db, plan)
                if not signup_rows:
                    held += 1
                    plan.status = "alarmed"
                    db.commit()
                    details.append({
                        "plan_id": plan.id, "ok": False, "step": "price_hold",
                        "held_items": len(price_holds), "waiting_for_manual_decision": True,
                    })
                    continue
            plan.status = "precheck"
            db.commit()

        if plan.status == "precheck":
            discount = campaign_service.push_discount(db, plan, phase="commit")
            if not discount.get("ok"):
                failed += 1
                plan.status = "alarmed"
                db.commit()
                text = (
                    f"活动：{plan.name}\n失败步骤：单品立减\n"
                    f"原因：{discount.get('error') or discount.get('validation') or '未知'}\n"
                    "系统已停止，未继续活动报名；不会自动改价或自动重试，等待用户决定。")
                notice = _notify_once(db, f"discount_{plan.id}", "活动自动执行失败", text)
                details.append({"plan_id": plan.id, "ok": False, "step": "discount",
                                "notification": notice})
                continue

        signup = campaign_service.push_signup(
            db, plan, execution_source="campaign_automation")
        if signup.get("ok"):
            succeeded += 1
        else:
            failed += 1
        details.append({
            "plan_id": plan.id,
            "ok": bool(signup.get("ok")),
            "step": "signup",
            "no_change": bool(signup.get("no_change")),
            "pending_items": len((signup.get("stats") or {}).get("pending_items") or []),
            "excluded_no_sales": len(
                (signup.get("stats") or {}).get("excluded_no_sales_items") or []),
            "promote_candidates": grouping.get("promote_candidates") or [],
            "error": signup.get("error"),
        })
    return {"processed": processed, "succeeded": succeeded, "failed": failed, "held": held,
            "details": details}
