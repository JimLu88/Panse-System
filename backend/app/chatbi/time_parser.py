# -*- coding: utf-8 -*-
"""ChatBI 自然语言时间解析 (Plan4 v2 §4.3)。

把问句里的时间词解析成 (start, end, granularity)。纯规则、零依赖、可全量单测。
口径约定 (写死, 与报表页对齐):
  - 本月 = 当月1日 .. 今天 (month-to-date; 用于"本月至今"类问法)
  - 上月 = 上月1日 .. 上月末 (整月)
  - 近N天 = 今天往前数 N 天 (含今天, 共 N 天)
  - 今年 = 本年1月1日 .. 今天; 去年/前年 = 整年
  - YYYY年M月 / YYYY-MM / M月 / 第N季度 / QN = 对应整段
大促窗口 (618/双11) 起止日期是口径常量, 由调用方通过 promo_windows 传入
(开工时找用户拍板); 未提供则对大促问法返回 None (交由上层拒答/澄清)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4}


@dataclass(frozen=True)
class TimeRange:
    start: date
    end: date
    granularity: str   # day / month / year
    label: str


def _month_first(y: int, m: int) -> date:
    return date(y, m, 1)


def _month_last(y: int, m: int) -> date:
    return date(y + 1, 1, 1) - timedelta(days=1) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _month_range(y: int, m: int, label: str) -> TimeRange:
    return TimeRange(_month_first(y, m), _month_last(y, m), "month", label)


def parse_time(text: str | None, today: date | None = None,
               promo_windows: dict | None = None) -> TimeRange | None:
    """解析时间词; 无可识别时间返回 None (由上层给默认区间, 如近30天)。"""
    today = today or date.today()
    t = (text or "").strip()
    if not t:
        return None

    # ---- 大促窗口 (口径常量, 需外部提供) ----
    if promo_windows:
        for key, aliases in (("618", ("618", "六一八", "年中大促", "年中")),
                             ("双11", ("双11", "双十一", "1111", "双11大促"))):
            if any(a in t for a in aliases) and key in promo_windows:
                w = promo_windows[key]
                return TimeRange(w["start"], w["end"], "day", w.get("label", key))

    # ---- YYYY年M月 / YYYY-M ----
    m = re.search(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*月?", t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return _month_range(y, mo, f"{y}年{mo}月")

    # ---- YYYY年 (整年) ----
    m = re.search(r"(20\d{2})\s*年(?!\s*\d)", t)
    if m:
        y = int(m.group(1))
        end = date(y, 12, 31) if y < today.year else today
        return TimeRange(date(y, 1, 1), end, "year", f"{y}年")

    # ---- 季度: 第N季度 / QN / N季度 ----
    m = re.search(r"[第]?\s*([1-4一二三四])\s*季度|[Qq]\s*([1-4])", t)
    if m:
        q = None
        g1, g2 = m.group(1), m.group(2)
        if g1:
            q = int(g1) if g1.isdigit() else _CN_DIGIT.get(g1)
        elif g2:
            q = int(g2)
        if q and 1 <= q <= 4:
            sm = (q - 1) * 3 + 1
            return TimeRange(_month_first(today.year, sm), _month_last(today.year, sm + 2),
                             "month", f"{today.year}年Q{q}")

    # ---- 近/最近/过去 N 天 ----
    m = re.search(r"(?:近|最近|过去)\s*(\d{1,4})\s*[天日]", t)
    if m:
        n = max(1, int(m.group(1)))
        return TimeRange(today - timedelta(days=n - 1), today, "day", f"近{n}天")

    # ---- 近/最近/过去 N 个月 ----
    m = re.search(r"(?:近|最近|过去)\s*(\d{1,2})\s*个?月", t)
    if m:
        n = max(1, int(m.group(1)))
        sy, sm = _shift_month(today.year, today.month, -(n - 1))
        return TimeRange(_month_first(sy, sm), today, "month", f"近{n}个月")

    # ---- 相对日 ----
    if any(k in t for k in ("今天", "今日", "当日", "本日")):
        return TimeRange(today, today, "day", "今天")
    if any(k in t for k in ("昨天", "昨日")):
        y = today - timedelta(days=1)
        return TimeRange(y, y, "day", "昨天")
    if "前天" in t:
        y = today - timedelta(days=2)
        return TimeRange(y, y, "day", "前天")

    # ---- 相对月 ----
    if any(k in t for k in ("上月", "上个月", "上一个月")):
        y, mo = _shift_month(today.year, today.month, -1)
        return _month_range(y, mo, "上月")
    if any(k in t for k in ("本月", "这个月", "当月", "本月份")):
        return TimeRange(_month_first(today.year, today.month), today, "month", "本月")

    # ---- 相对年 ----
    if any(k in t for k in ("今年", "本年", "本年度")):
        return TimeRange(date(today.year, 1, 1), today, "year", "今年")
    if any(k in t for k in ("去年", "上年")):
        y = today.year - 1
        return TimeRange(date(y, 1, 1), date(y, 12, 31), "year", "去年")
    if "前年" in t:
        y = today.year - 2
        return TimeRange(date(y, 1, 1), date(y, 12, 31), "year", "前年")

    # ---- 裸 M月 (无年份): 今年该月整月 ----
    m = re.search(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)", t)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            return _month_range(today.year, mo, f"{today.year}年{mo}月")

    return None
