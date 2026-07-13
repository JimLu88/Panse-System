"""活动档期日历 (2026-07-13 用户需求)。★2026-07-13 改精确到秒: 淘宝活动起止是精确到时分秒的,
档期也精确到秒对应; 单品立减自动结束 = 下一档期 start 的【前一秒】(不是前一天 23:59:59)。

存 system_settings 键 `activity_periods` = JSON 列表 [{name, tier, start, end}]:
  - tier ∈ mid(中促10%) / big(88VIP大促12%) / big618(618双11 15%) / super_reduce(超级立减长期);
  - start/end = 'YYYY-MM-DD HH:MM:SS'; 超级立减长期可无 end(常年在线)。只给日期无时间的补 00:00:00。

★自动结束: 单品立减默认结束 = 【下一档期 start 前一秒】, 让两波大促之间的空档由单品立减兜住、下一波
  一开(精确那一秒)就自动让位(用户口径, 对齐淘宝精确时刻)。无下一档 → next=None, 前端提示"无下次活动"。
"""
from __future__ import annotations

import datetime
import json
from typing import Optional

from sqlalchemy.orm import Session

_SETTING_KEY = "activity_periods"
_VALID_TIERS = ("mid", "big", "big618", "super_reduce")
_TIER_LABEL = {
    "mid": "中促 10%",
    "big": "88VIP大促 12%",
    "big618": "618/双11 15%",
    "super_reduce": "超级立减长期",
}
_FMT = "%Y-%m-%d %H:%M:%S"
# 容错解析顺序: 全时间 / 到分 / 只日期(补0点)
_PARSE_FMTS = (_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d")


def tier_label(tier: str) -> str:
    return _TIER_LABEL.get(tier, tier)


def _to_dt(v) -> Optional[datetime.datetime]:
    """任意 → datetime(精确到秒) 或 None (容错空/错格式; 'T'/日期-only 都吃)。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime.datetime):
        return v
    s = str(v).strip().replace("T", " ")
    for fmt in _PARSE_FMTS:
        try:
            return datetime.datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _norm_dt(v) -> Optional[str]:
    """任意 → 'YYYY-MM-DD HH:MM:SS' 或 None。只有日期(无时间)补 00:00:00。"""
    dt = _to_dt(v)
    return dt.strftime(_FMT) if dt else None


def _clean(items) -> list[dict]:
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        tier = it.get("tier") if it.get("tier") in _VALID_TIERS else "big"
        start = _norm_dt(it.get("start"))
        end = _norm_dt(it.get("end"))
        if not name or not start:
            continue
        out.append({"name": name, "tier": tier, "start": start, "end": end})
    out.sort(key=lambda x: x["start"])   # 'YYYY-MM-DD HH:MM:SS' 字典序=时间序
    return out


def get_calendar(db: Session) -> list[dict]:
    """读活动档期日历 (已清洗+按 start 升序, 精确到秒)。"""
    from app.services import settings_service
    raw = settings_service.get(db, _SETTING_KEY, env_fallback=False)
    try:
        items = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        items = []
    return _clean(items)


def set_calendar(db: Session, periods) -> list[dict]:
    """整表覆盖存档期日历 (清洗+排序后写 system_settings)。返回落库后的清单。"""
    from app.services import settings_service
    clean = _clean(periods)
    settings_service.set_value(
        db, _SETTING_KEY, json.dumps(clean, ensure_ascii=False),
        description="活动档期日历(报名/单品立减选档期+自动结束, 精确到秒)")
    db.commit()
    return clean


def next_period_after(db: Session, ref, *, exclude_name: Optional[str] = None) -> Optional[dict]:
    """ref 之后、start 最早的一档 (排除自身)。用于单品立减自动结束。"""
    ref_dt = _to_dt(ref)
    if ref_dt is None:
        return None
    cand = [
        p for p in get_calendar(db)
        if p["start"] and (_to_dt(p["start"]) or datetime.datetime.min) > ref_dt
        and p.get("name") != exclude_name
    ]
    return cand[0] if cand else None


def auto_end_for(db: Session, start, *, this_name: Optional[str] = None) -> dict:
    """单品立减自动结束 = 下一档期 start 的【前一秒】。无下一档 → end=None(前端提示无下次活动)。"""
    start_dt = _to_dt(start)
    if start_dt is None:
        return {"end": None, "end_dt": None, "next": None, "reason": "开始时间无效"}
    nxt = next_period_after(db, start_dt, exclude_name=this_name)
    if not nxt:
        return {"end": None, "end_dt": None, "next": None, "reason": "无下次活动"}
    ns = _to_dt(nxt["start"])
    end_dt = ns - datetime.timedelta(seconds=1)
    s = end_dt.strftime(_FMT)
    return {
        "end": s, "end_dt": s,
        "next": {"name": nxt["name"], "tier": nxt["tier"],
                 "tier_label": tier_label(nxt["tier"]), "start": nxt["start"]},
    }


def status(db: Session) -> dict:
    """当前生效 / 即将到来 的档期 (前端顶部提示, 精确到秒判断)。"""
    now = datetime.datetime.now()
    active, upcoming = [], []
    for p in get_calendar(db):
        s = _to_dt(p["start"])
        e = _to_dt(p["end"]) if p.get("end") else None
        if s is None:
            continue
        item = {**p, "tier_label": tier_label(p["tier"])}
        if s <= now and (e is None or now <= e):
            active.append(item)
        elif s > now:
            upcoming.append({**item, "days_to_start": (s - now).days})
    return {"today": now.strftime(_FMT), "active": active, "upcoming": upcoming[:5]}
