"""每日活动发现 (P4, spec: docs/活动生命周期系统_执行plan.md §四「每日活动发现」/§五调度)。

流程 (每天 18:40 抓单编排后, scheduler.campaign_daily_discovery):
1. web_agent_service.campaign_discover 抓千牛营销活动列表 (WA 只读任务);
2. 结果 upsert 进 CampaignCalendar (按 title+start_at 去重, 状态/结束时间就地刷新);
3. 距开始 <3 天且今天没提醒过的活动 → notify_service.broadcast_text 飞书提醒运营去报名,
   更新 last_notified_on 防重 (同活动一天只提醒一次);
4. WA 失败 → 飞书报错文案「活动发现抓取失败请手动查看」, 不静默。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

REMIND_DAYS_BEFORE = 3      # 距开始 <3 天提醒 (spec §四: 距开始<3天的活动推飞书)
_ACTIONABLE_STATUS_WORDS = ("可报名", "报名中")
_TERMINAL_STATUS_WORDS = ("已结束", "报名截止", "已关闭", "已取消")

# WA 抓页解析宽容: 日期可能是多种格式或 None, 解析不动的留 None (raw 原文已带回)
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d",
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
        if row is None and start is not None:
            # 日历卡片通常只有日期，活动详情才有秒级时间。若已经人工/详情页确认过
            # 同一天的精确档期，继续复用该行，避免次日又插入一个 00:00 的重复活动。
            day_start = datetime.combine(start.date(), time.min)
            day_end = day_start + timedelta(days=1)
            row = db.execute(select(CampaignCalendar).where(
                CampaignCalendar.title == title,
                CampaignCalendar.start_at >= day_start,
                CampaignCalendar.start_at < day_end)).scalars().first()
        if row is None:
            db.add(CampaignCalendar(title=title, start_at=start, end_at=end,
                                    status=status, source="discovery"))
            inserted += 1
        else:
            if (end is not None
                    and (row.end_at is None
                         or end.time() != time.min
                         or row.end_at.time() == time.min)):
                row.end_at = end
            if status:
                row.status = status
            updated += 1
    db.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def due_reminders(db: Session, today: Optional[date] = None) -> list:
    """返回今天应提醒的活动。

    只对有准确档期的阶段按开始前 3 天提醒；首页“可报名/报名中”入口卡片经常
    没有日期，不能把它们当成 8 场新活动逐条提醒。若本次抓取完全没有解析出
    任何可报名档期，run_daily_discovery 会另发一条合并诊断。已结束阶段不提醒。
    """
    from app.models.campaign import CampaignCalendar
    today = today or date.today()
    rows = db.execute(select(CampaignCalendar)).scalars().all()
    out = []
    for r in rows:
        status = str(r.status or "")
        if any(word in status for word in _TERMINAL_STATUS_WORDS):
            continue
        if r.start_at is None:
            continue
        days_left = (r.start_at.date() - today).days
        if 0 <= days_left <= REMIND_DAYS_BEFORE and r.last_notified_on != today:
            out.append(r)
    out.sort(key=lambda r: (r.start_at, r.title))
    return out


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "未知"


def explain_discovery_failure(error: str) -> str:
    """把 Web-Agent 诊断码翻成可行动的人话；保留原码方便继续排查。"""
    err = str(error or "未知原因")
    low = err.lower()
    if "no_campaigns_found" in low:
        return ("活动页已打开且登录有效，但程序没有识别到活动卡片；通常是千牛页面的"
                "状态文案或页面结构改版，并不等于平台真的没有活动")
    if "need_scan" in low or "login" in low:
        return "淘宝/千牛登录态已失效，需要重新扫码登录"
    if "token" in low:
        return "ERP 与本机 Web-Agent 的访问令牌不一致或未配置"
    if "timeout" in low:
        return "本机 Web-Agent 或千牛页面响应超时"
    if "connection" in low or "offline" in low:
        return "本机 Web-Agent 未在线或 8500 端口不可达"
    return "活动发现任务失败，需结合下方诊断码与最新截图检查"


def _latest_discovery_run_status_today(db: Session) -> Optional[str]:
    """Return the previous hourly discovery status for the local business day.

    The scheduler writes the current run only after this service returns, so
    the newest row is always the immediately preceding attempt.
    """
    from app.models.scheduled_job import ScheduledJobRun

    row = db.execute(
        select(ScheduledJobRun)
        .where(ScheduledJobRun.job_id == "campaign_daily_discovery")
        .order_by(ScheduledJobRun.id.desc())
        .limit(1)
    ).scalars().first()
    if row is None or row.started_at is None:
        return None
    started = row.started_at
    started_on = started.astimezone().date() if started.tzinfo else started.date()
    return row.status if started_on == date.today() else None


def run_daily_discovery(db: Session) -> dict:
    """调度入口: WA 活动发现 → 落日历 → <3天飞书提醒 (去重: 同活动一天一次)。"""
    from app.services import campaign_notification_service as notify_service, web_agent_service

    r = web_agent_service.campaign_discover(db)
    if not r.get("ok"):
        err = r.get("error") or r.get("message") or "未知原因"
        reason = explain_discovery_failure(err)
        # Hourly discovery is a refresh.  If the immediately preceding attempt
        # already succeeded today, retain that valid calendar and record this
        # transient failure without telling the operator that nothing was
        # captured.  A second consecutive failure (or the day's first failure)
        # still alerts normally.
        if _latest_discovery_run_status_today(db) == "ok":
            return {
                "ok": False,
                "error": err,
                "reason": reason,
                "notified_error": False,
                "notification_suppressed": "same_day_success_single_refresh_failure",
                "retrying_next_hour": True,
            }
        notify_service.broadcast_text(
            db,
            f"⚠️ 活动发现抓取失败。\n原因：{reason}。\n诊断码：{err}。\n"
            f"今天的千牛营销活动列表没抓到, 请手动到千牛「营销中心→活动报名」页查看近期可报活动, "
            f"以免错过报名窗口。",
            title="活动发现抓取失败", level="warn")
        return {"ok": False, "error": err, "reason": reason, "notified_error": True}

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

    # 首页“可报名/报名中”是入口卡片，本来就可能不带档期；
    # 真正日期在大促日历子卡片中，其状态可能是“售卖中”。
    # 只有存在可报名入口，且本次整批结果都无可解析日期，才报异常。
    discovered = r.get("campaigns") or []
    actionable = [c for c in discovered
                  if any(word in str(c.get("status") or "")
                         for word in _ACTIONABLE_STATUS_WORDS)]
    dated = [c for c in discovered if _parse_dt(c.get("start")) is not None]
    unresolved_warning = 0
    if actionable and not dated:
        from app.models.campaign import CampaignCalendar
        unresolved = db.execute(select(CampaignCalendar).where(
            CampaignCalendar.start_at.is_(None))).scalars().all()
        pending = [row for row in unresolved
                   if any(word in str(row.status or "") for word in _ACTIONABLE_STATUS_WORDS)
                   and row.last_notified_on != today]
        if pending:
            titles = "、".join(f"「{row.title}」" for row in pending[:8])
            if r.get("calendar_opened") is False:
                diagnosis = (
                    "活动入口可以读取，但大促日历页签未能打开"
                    f"（{r.get('calendar_error') or '未找到可用日历入口'}）"
                )
            else:
                diagnosis = "大促日历已打开，但当前页面日期格式没有被解析"
            notify_service.broadcast_text(
                db,
                f"已抓到可报名入口 {len(pending)} 个，但本次没有解析出任何售卖档期：{titles}。\n"
                f"诊断：{diagnosis}。这不代表没有活动；请人工看一次活动日历。",
                title="活动日期识别异常", level="warn")
            for row in pending:
                row.last_notified_on = today
            db.commit()
            unresolved_warning = 1
    # 近期可报名阶段进一步只读详情：只有父活动、子阶段、秒级档期、力度和活动 ID
    # 全部能锁定，才创建自动执行计划。安全门失败会飞书说明，并且不会猜值。
    from app.models.campaign import CampaignCalendar
    from app.services import campaign_automation_service
    discovered_by_key = {}
    for c in discovered:
        title = str(c.get("title") or "").strip()
        start = _parse_dt(c.get("start"))
        if title:
            discovered_by_key[(title, start.date() if start else None)] = c
    calendar_rows = db.execute(select(CampaignCalendar)).scalars().all()
    candidates = []
    for row in calendar_rows:
        c = discovered_by_key.get(
            (row.title, row.start_at.date() if row.start_at else None))
        if c is None:
            continue
        setattr(row, "_raw", str(c.get("raw") or ""))
        candidates.append(row)
    auto_plans = (
        campaign_automation_service.sync_upcoming_plans(db, candidates)
        if campaign_automation_service.enabled(db)
        else {"skipped": "campaign_auto_disabled"}
    )
    return {"ok": True, **stats, "reminded": reminded,
            "unresolved_warning": unresolved_warning, "auto_plans": auto_plans,
            "discovery_diagnostics": {
                "tabs": r.get("tabs"),
                "calendar_opened": r.get("calendar_opened"),
                "calendar_error": r.get("calendar_error"),
            }}
