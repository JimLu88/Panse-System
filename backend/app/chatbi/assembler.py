# -*- coding: utf-8 -*-
"""ChatBI 半生成拼装器 (Plan4 v2 §4.4-2) —— LLM 只选指标, 代码确定性拼 SQL。

流程: LLM 输出受约束 JSON {metric, dimensions, filters, time, top_n, order} → 本模块
严格校验 (枚举成员/维度∈指标声明/op 白名单/值类型) → 从指标字典片段确定性拼 SQL →
仍过 sql_gate 六道闸。任何不合法 **直接拒 (raise), 绝不自动改写** (supersonic issue
#2022 教训: 校正器会把对的改错)。

安全: 过滤值只落入单引号字符串字面量 (PG standard_conforming_strings=on ⇒ 无反斜杠转义
⇒ 双写单引号即无法越狱) 或校验过的数字; 再叠 sql_gate 结构校验 + 只读角色 + statement_timeout。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.chatbi import metrics_dict
from app.chatbi.catalog import ALLOWED_VIEWS, VIEW_COLUMNS
from app.chatbi.sql_gate import GateResult, validate_readonly_select
from app.chatbi.time_parser import TimeRange

MAX_TOP = 1000

# 允许出现在 WHERE 的过滤字段 (维度类, 非度量)。
FILTERABLE_FIELDS = frozenset({
    "product_name", "product_code", "sku_code", "platform", "shop", "status",
})
# 过滤运算符 → SQL。contains 用 ILIKE '%..%'。
_OPS = {"eq": "=", "ne": "<>", "contains": "ILIKE", "gt": ">", "lt": "<", "gte": ">=", "lte": "<="}
_NUMERIC_OPS = {"gt", "lt", "gte", "lte"}


class AssemblerError(ValueError):
    """半生成 spec 不合法 (面向用户可读; 触发降级/拒答, 不自动改写)。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AssembledQuery:
    sql: str                       # 过闸门后的最终 SQL
    columns: list[dict]            # [{"name","kind"}] 供图表选择
    caliber_notes: list[str]       # 口径说明
    metric_key: str
    gate: GateResult


def _quote_literal(v: str) -> str:
    """PG 字符串字面量: 双写单引号 (standard_conforming_strings=on 下安全)。"""
    s = str(v)
    if len(s) > 200:
        raise AssemblerError("过滤值过长")
    return "'" + s.replace("'", "''") + "'"


def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError) as e:
        raise AssemblerError(f"数值过滤需要数字, 收到: {v!r}") from e
    return repr(int(f)) if f.is_integer() else repr(f)


def _build_filter(f: dict) -> str:
    field = str(f.get("field", "")).strip()
    op = str(f.get("op", "")).strip()
    val = f.get("value")
    if field not in FILTERABLE_FIELDS:
        raise AssemblerError(f"不允许按字段过滤: {field}")
    if op not in _OPS:
        raise AssemblerError(f"不支持的过滤运算符: {op}")
    if op == "contains":
        return f"{field} ILIKE {_quote_literal('%' + str(val) + '%')}"
    if op in _NUMERIC_OPS:
        return f"{field} {_OPS[op]} {_num(val)}"
    return f"{field} {_OPS[op]} {_quote_literal(val)}"


def assemble(spec: dict, time_range: TimeRange | None = None,
            max_rows: int = MAX_TOP) -> AssembledQuery:
    """把受约束 JSON spec 拼成安全 SQL。不合法即 raise AssemblerError (降级不改写)。"""
    if not isinstance(spec, dict):
        raise AssemblerError("spec 不是对象")
    metric_key = str(spec.get("metric", "")).strip()
    m = metrics_dict.get(metric_key)
    if m is None:
        raise AssemblerError(f"未知指标: {metric_key}")
    if m.service_only:
        raise AssemblerError(f"指标 {metric_key} 口径复杂, 只能走模板 (禁半生成)")

    # 维度: 必须 ⊆ 指标声明的 dims
    dims = spec.get("dimensions") or []
    if not isinstance(dims, list):
        raise AssemblerError("dimensions 必须是数组")
    for d in dims:
        if d not in m.dims:
            raise AssemblerError(f"指标 {metric_key} 不支持维度 {d}")

    # 过滤
    raw_filters = spec.get("filters") or []
    if not isinstance(raw_filters, list):
        raise AssemblerError("filters 必须是数组")
    view_cols = {c[0] for c in VIEW_COLUMNS[m.base_view]}
    user_filters = []
    for f in raw_filters:
        if not isinstance(f, dict):
            raise AssemblerError("filter 项必须是对象")
        if f.get("field") not in view_cols:
            raise AssemblerError(f"字段不在视图内: {f.get('field')}")
        user_filters.append(_build_filter(f))

    # top_n / order
    try:
        top_n = int(spec.get("top_n") or 100)
    except (TypeError, ValueError) as e:
        raise AssemblerError("top_n 必须是整数") from e
    top_n = max(1, min(top_n, max_rows))
    order = str(spec.get("order", "desc")).lower()
    if order not in ("asc", "desc"):
        raise AssemblerError("order 只能是 asc/desc")

    # ---- 拼 SELECT ----
    select_parts: list[str] = []
    columns: list[dict] = []
    for i, d in enumerate(dims, start=1):
        alias = f"d{i}"
        select_parts.append(f"{metrics_dict.dim_sql(d, m.time_field)} AS {alias}")
        columns.append({"name": alias, "label": metrics_dict.DIMENSIONS[d]["label"],
                        "kind": metrics_dict.DIMENSIONS[d]["kind"]})
    select_parts.append(f"{m.agg_sql} AS val")
    columns.append({"name": "val", "label": m.cn, "kind": "number"})

    where = list(m.builtin_filters) + user_filters
    if time_range is not None:
        where.append(f"{m.time_field} BETWEEN {_quote_literal(time_range.start.isoformat())} "
                     f"AND {_quote_literal(time_range.end.isoformat())}")
    where_sql = " AND ".join(where) if where else "TRUE"

    sql = f"SELECT {', '.join(select_parts)} FROM {m.base_view} WHERE {where_sql}"
    if dims:
        group_idx = ", ".join(str(i) for i in range(1, len(dims) + 1))
        sql += f" GROUP BY {group_idx} ORDER BY val {order.upper()} LIMIT {top_n}"
    else:
        sql += f" LIMIT {top_n}"

    # ---- 过只读六道闸 (结构 + 白名单 + LIMIT 钳制) ----
    gate = validate_readonly_select(sql, ALLOWED_VIEWS, max_rows=max_rows)

    caliber_notes = [m.caliber_ref]
    if "is_refill = TRUE" in m.builtin_filters:
        caliber_notes.append("⚠ 补单/刷单对账口径, 非经营数字")
    if time_range is not None:
        caliber_notes.append(f"时间范围: {time_range.label} ({time_range.start}~{time_range.end})")

    return AssembledQuery(
        sql=gate.safe_sql, columns=columns, caliber_notes=caliber_notes,
        metric_key=metric_key, gate=gate,
    )
