"""每日活动发现 (P4, spec: docs/活动生命周期系统_执行plan.md §四「每日活动发现」/§五调度)。

流程 (每天 18:40 抓单编排后, scheduler.campaign_daily_discovery):
1. web_agent_service.campaign_discover 抓千牛营销活动列表 (WA 只读任务);
2. 结果 upsert 进 CampaignCalendar (按 title+start_at 去重, 状态/结束时间就地刷新);
3. 距开始 <3 天且今天没提醒过的活动 → notify_service.broadcast_text 飞书提醒运营去报名,
   更新 last_notified_on 防重 (同活动一天只提醒一次);
4. WA 失败 → 飞书报错文案「活动发现抓取失败请手动查看」, 不静默。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

REMIND_DAYS_BEFORE = 3      # 距开始 <3 天提醒 (spec §四: 距开始<3天的活动推飞书)

# WA 抓页解析宽容: 日期可能是多种格式或 None, 解析不动的留 None (raw 原文已带回)
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d",
)


def _parse_dt(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ai_rescue(db: Session, c: dict, title: str,
               start: Optional[datetime], end: Optional[datetime]):
    """规则解析失手 (没标题 / 日期解析不出) 且带 raw 原文 → 活动系统 AI (设置页可配
    DeepSeek/千问) 抽严格 JSON {title,start,end} 兜底; 未配置 / 抽取失败 → 原值原样返回
    (零行为变化)。规则解析成功时不调 AI (省 token)。"""
    raw = str(c.get("raw") or "").strip()
    if not raw or (title and start is not None):
        return title, start, end
    try:
        from app.services import campaign_ai_service
        got = campaign_ai_service.extract_campaign_fields(db, raw)
    except Exception:   # noqa: BLE001 — 兜底路径绝不能拖垮发现主流程
        return title, start, end
    if not got:
        return title, start, end
    title = title or str(got.get("title") or "").strip()   # 规则标题优先, AI 只补空
    if start is None:
        start = _parse_dt(got.get("start"))
    if end is None:
        end = _parse_dt(got.get("end"))
    return title, start, end


def upsert_calendar(db: Session, campaigns: list[dict]) -> dict:
    """WA 发现结果 → CampaignCalendar, 按 (title, start_at) 去重 upsert。
    已存在的只刷新 end_at/status (活动状态会从「预热」变「报名中」), 不重复建行。"""
    from app.models.campaign import CampaignCalendar
    inserted = updated = skipped = 0
    for c in campaigns or []:
        title = str(c.get("title") or "").strip()
        start = _parse_dt(c.get("start"))
        end = _parse_dt(c.get("end"))
        # 规则解析不出 → 可选 AI 兜底 (未配置时此行为空操作, 与旧行为逐位一致)
        title, start, end = _ai_rescue(db, c, title, start, end)
        if not title:
            skipped += 1                       # 没标题的行没法去重/提醒, 丢弃 (raw 已在 WA 侧留档)
            continue
        status = str(c.get("status") or "").strip() or None
        row = db.execute(select(CampaignCalendar).where(
            CampaignCalendar.title == title,
            CampaignCalendar.start_at == start)).scalars().first()
        if row is None:
            db.add(CampaignCalendar(title=title, start_at=start, end_at=end,
                                    status=status, source="discovery"))
            inserted += 1
        else:
            if end is not None:
                row.end_at = end
            if status:
                row.status = status
            updated += 1
    db.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def due_reminders(db: Session, today: Optional[date] = None) -> list:
    """距开始 0~<{REMIND_DAYS_BEFORE} 天、今天还没提醒过的日历项 (已开始的不再提醒)。"""
    from app.models.campaign import CampaignCalendar
    today = today or date.today()
    rows = db.execute(select(CampaignCalendar).where(
        CampaignCalendar.start_at.isnot(None))).scalars().all()
    out = []
    for r in rows:
        days_left = (r.start_at.date() - today).days
        if 0 <= days_left < REMIND_DAYS_BEFORE and r.last_notified_on != today:
            out.append(r)
    out.sort(key=lambda r: r.start_at)
    return out


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "未知"


def run_daily_discovery(db: Session) -> dict:
    """调度入口: WA 活动发现 → 落日历 → <3天飞书提醒 (去重: 同活动一天一次)。"""
    from app.services import notify_service, web_agent_service

    r = web_agent_service.campaign_discover(db)
    if not r.get("ok"):
        err = r.get("error") or r.get("message") or "未知原因"
        notify_service.broadcast_text(
            db,
            f"⚠️ 活动发现抓取失败（{err}）。\n"
            f"今天的千牛营销活动列表没抓到, 请手动到千牛「营销中心→活动报名」页查看近期可报活动, "
            f"以免错过报名窗口。",
            title="活动发现抓取失败", level="warn")
        return {"ok": False, "error": err, "notified_error": True}

    stats = upsert_calendar(db, r.get("campaigns") or [])
    today = date.today()
    due = due_reminders(db, today)
    reminded = 0
    if due:
        lines = [f"📅 千牛活动开抢在即（{len(due)} 场）, 请去报名:"]
        for c in due:
            days_left = (c.start_at.date() - today).days
            when = "今天就开始" if days_left == 0 else f"还有 {days_left} 天开始"
            lines.append(f"- 「{c.title}」{when}; 档期 {_fmt(c.start_at)} ~ {_fmt(c.end_at)}"
                         + (f"; 千牛状态: {c.status}" if c.status else ""))
        lines.append("报名走系统「定价→活动自动填写→生命周期向导」: 报名价=日常价, 每场只变单品立减。")
        notify_service.broadcast_text(db, "\n".join(lines),
                                      title="活动报名提醒", level="warn")
        for c in due:
            c.last_notified_on = today       # 防重: 同活动一天只提醒一次
        db.commit()
        reminded = len(due)
    return {"ok": True, **stats, "reminded": reminded}
