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


def after_coupon(price: Optional[Decimal], coupon_rate: float) -> tuple[Optional[float], float]:
    """券后价 + 实际减额。price 为叠券前。"""
    if price is None:
        return None, 0.0
    p = float(price)
    cut = round(p * float(coupon_rate), 2)
    return round(p - cut, 2), cut
