"""竞品最新价抓取 (尽力而为 + 优雅降级).

淘宝有反爬 (实测简单 GET 返回 403), 所以:
  - 尽力抓: 带浏览器 UA + 可选 cookie, 解析页面里的价格数字。
  - 抓不到就记 fetch_status=blocked/failed, 不抛错; 支持人工更新最新价。
价格为"叠平台券前"; 券后价由配置的通用券率换算 (调用方显示并披露减了多少)。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competitor import CompetitorPrice

_PRICE_RE = re.compile(r'"price"\s*[:：]\s*"?(\d+(?:\.\d+)?)"?')
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _try_fetch_price(url: str, cookie: Optional[str] = None) -> tuple[Optional[Decimal], str]:
    """返回 (price, status)。status: ok / blocked / failed / no_link。"""
    if not url:
        return None, "no_link"
    import httpx
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        r = httpx.get(url, headers=headers, timeout=8, follow_redirects=True)
    except Exception:
        return None, "failed"
    if r.status_code in (403, 401) or len(r.text) < 200:
        return None, "blocked"          # 反爬拦截
    m = _PRICE_RE.search(r.text)
    if not m:
        return None, "blocked"
    try:
        return Decimal(m.group(1)), "ok"
    except Exception:
        return None, "failed"


def refresh_one(db: Session, comp_id: int, *, cookie: Optional[str] = None) -> CompetitorPrice:
    c = db.get(CompetitorPrice, comp_id)
    if not c:
        raise ValueError("competitor not found")
    price, status = _try_fetch_price(c.link or "", cookie=cookie)
    if price is not None:
        c.latest_price = price
    c.fetch_status = status
    c.latest_fetched_at = datetime.now(timezone.utc)
    db.flush()
    return c


def set_manual_price(db: Session, comp_id: int, price: Decimal) -> CompetitorPrice:
    c = db.get(CompetitorPrice, comp_id)
    if not c:
        raise ValueError("competitor not found")
    c.latest_price = price
    c.fetch_status = "manual"
    c.latest_fetched_at = datetime.now(timezone.utc)
    db.flush()
    return c


def _parse_dt(s: Optional[str]) -> datetime:
    """ISO 字符串 → aware datetime; 缺失/非法回落当前 UTC。"""
    if not s:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def batch_update_prices(db: Session, items: list[dict]) -> dict:
    """批量回灌竞品最新价 (供外部采集服务一次推一批)。

    每条按 ``id`` 优先, 否则按 ``link`` 精确匹配命中我表里的行:
      {id?, link?, latest_price, fetch_status?, fetched_at?}
    命中即写 latest_price / fetch_status(默认 ok) / latest_fetched_at。
    返回 {updated, not_found:[key...], errors:[{key, error}...]}; 不抛错, 逐条容错。
    """
    updated = 0
    not_found: list = []
    errors: list = []
    link_index: Optional[dict] = None  # 按需预载 link→行, 避免逐条全表查

    for it in items:
        cid = it.get("id")
        link = (it.get("link") or "").strip()
        price = it.get("latest_price")
        key = cid if cid is not None else (link or None)
        if price is None:
            errors.append({"key": key, "error": "missing latest_price"})
            continue

        c = None
        if cid is not None:
            c = db.get(CompetitorPrice, cid)
        elif link:
            if link_index is None:
                link_index = {}
                for row in db.execute(select(CompetitorPrice)).scalars():
                    if row.link:
                        link_index.setdefault(row.link.strip(), row)
            c = link_index.get(link)
        else:
            errors.append({"key": None, "error": "missing id and link"})
            continue

        if c is None:
            not_found.append(key)
            continue
        try:
            c.latest_price = Decimal(str(price))
        except (ValueError, ArithmeticError):
            errors.append({"key": key, "error": "bad price"})
            continue
        c.fetch_status = it.get("fetch_status") or "ok"
        c.latest_fetched_at = _parse_dt(it.get("fetched_at"))
        updated += 1

    db.flush()
    return {"updated": updated, "not_found": not_found, "errors": errors}


def after_coupon(price: Optional[Decimal], coupon_rate: float) -> tuple[Optional[float], float]:
    """券后价 + 实际减额。price 为叠券前。"""
    if price is None:
        return None, 0.0
    p = float(price)
    cut = round(p * float(coupon_rate), 2)
    return round(p - cut, 2), cut
