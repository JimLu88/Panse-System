"""活动档期日历 (2026-07-13 用户需求: 报名/单品立减要选具体档期, 单品立减自动结束=下一档期开始前一刻)。

存 system_settings 键 `activity_periods` = JSON 列表 [{name, tier, start, end}]:
  - tier ∈ mid(中促10%) / big(88VIP大促12%) / big618(618双11 15%) / super_reduce(超级立减长期);
  - start/end = ISO 'YYYY-MM-DD'; 超级立减长期可无 end(常年在线)。

★自动结束: 单品立减默认结束 = 【下一档期 start 的前一天 23:59:59】, 让两波大促之间的空档由单品立减
  兜住、下一波一开就自动让位(用户口径)。无下一档 → 返回 next=None, 前端提示"无下次活动"。

与成品库存页 promo_periods(备货提前期用) 分开存: 那个是备货触发, 这个是报名档期(带力度), 关注点不同、
避免互相影响。首次为空时可从 promo_periods 播种(seed_from_promo_periods)。
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


def tier_label(tier: str) -> str:
    return _TIER_LABEL.get(tier, tier)


def _norm_date(v) -> Optional[str]:
    """任意 → ISO 'YYYY-MM-DD' 或 None (容错空/错格式)。"""
    if not v:
        return None
    try:
        return datetime.date.fromisoformat(str(v)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _to_date(v) -> datetime.date:
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


def _clean(items) -> list[dict]:
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        tier = it.get("tier") if it.get("tier") in _VALID_TIERS else "big"
        start = _norm_date(it.get("start"))
        end = _norm_date(it.get("end"))
        if not name or not start:
            continue
        out.append({"name": name, "tier": tier, "start": start, "end": end})
    out.sort(key=lambda x: x["start"])
    return out


def get_calendar(db: Session) -> list[dict]:
    """读活动档期日历 (已清洗+按 start 升序)。"""
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
        description="活动档期日历(报名/单品立减选档期+自动结束)")
    db.commit()
    return clean


def next_period_after(db: Session, ref, *, exclude_name: Optional[str] = None) -> Optional[dict]:
    """ref 之后、start 最早的一档 (排除自身)。用于单品立减自动结束。"""
    ref_d = _to_date(ref)
    cand = [
        p for p in get_calendar(db)
        if p["start"] and _to_date(p["start"]) > ref_d and p.get("name") != exclude_name
    ]
    return cand[0] if cand else None


def auto_end_for(db: Session, start, *, this_name: Optional[str] = None) -> dict:
    """单品立减自动结束 = 下一档期 start 的前一天 23:59:59。无下一档 → end=None(前端提示无下次活动)。"""
    start_d = _to_date(start)
    nxt = next_period_after(db, start_d, exclude_name=this_name)
    if not nxt:
        return {"end": None, "end_dt": None, "next": None, "reason": "无下次活动"}
    ns = _to_date(nxt["start"])
    end_d = ns - datetime.timedelta(days=1)
    end_dt = datetime.datetime.combine(end_d, datetime.time(23, 59, 59))
    return {
        "end": end_d.isoformat(),
        "end_dt": end_dt.isoformat(sep=" "),
        "next": {"name": nxt["name"], "tier": nxt["tier"],
                 "tier_label": tier_label(nxt["tier"]), "start": nxt["start"]},
    }


def status(db: Session) -> dict:
    """当前生效 / 即将到来 的档期 (前端顶部提示)。"""
    today = datetime.date.today()
    active, upcoming = [], []
    for p in get_calendar(db):
        s = _to_date(p["start"])
        e = _to_date(p["end"]) if p.get("end") else None
        item = {**p, "tier_label": tier_label(p["tier"])}
        if s <= today and (e is None or today <= e):
            active.append(item)
        elif s > today:
            upcoming.append({**item, "days_to_start": (s - today).days})
    return {"today": today.isoformat(), "active": active, "upcoming": upcoming[:5]}
