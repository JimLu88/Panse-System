# -*- coding: utf-8 -*-
"""全站统一模糊搜索 (用户需求 2026-06-12: "所有表格里面都可以这么搜索").

规则 (与产品总表一致):
  1. 空格分词: 每个词都必须命中 (词序不限) — AND;
  2. 词内字符间隙: 名称类列允许字与字之间插入其他字
     ("榉木餐桌" → %榉%木%餐%桌%, 可命中"榉木岩板餐桌");
  3. 编码/单号类列只做连续子串匹配 — 数字编号做间隙会过度命中
     (%1%2%3% 几乎匹配一切)。

用法:
    stmt = stmt.where(fuzzy_clause(q,
        like_cols=[Order.order_no, Order.customer_name],
        gap_cols=[Order.customer_name]))
like_cols = 连续子串匹配的列; gap_cols = 额外做间隙模糊的"名称类"列。
"""
from __future__ import annotations

import re

from sqlalchemy import and_, or_

_GAP_MAX_LEN = 12   # 词太长时间隙模式爆炸且无意义, 超过只做子串


def gap_pattern(term: str) -> str:
    return "%" + "%".join(term) + "%"


def fuzzy_clause(q: str, *, like_cols, gap_cols=()):
    """返回 SQLAlchemy 布尔子句; q 为空时返回 None (调用方跳过)。"""
    terms = [t for t in re.split(r"\s+", (q or "").strip()) if t]
    if not terms or not like_cols:
        return None
    per_term = []
    for t in terms:
        ors = [c.ilike(f"%{t}%") for c in like_cols]
        if 1 < len(t) <= _GAP_MAX_LEN:
            gp = gap_pattern(t)
            ors.extend(c.ilike(gp) for c in gap_cols)
        per_term.append(or_(*ors))
    return and_(*per_term)
