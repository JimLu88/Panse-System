# -*- coding: utf-8 -*-
"""ChatBI 基础层单测: time_parser / charts / metrics_dict (纯函数, 无需 DB)。"""
from datetime import date

import pytest

from app.chatbi import charts, metrics_dict
from app.chatbi.catalog import ALLOWED_VIEWS, VIEW_COLUMNS
from app.chatbi.sql_gate import validate_readonly_select
from app.chatbi.time_parser import parse_time

TODAY = date(2026, 7, 5)


# ------------------------------- time_parser ------------------------------- #

@pytest.mark.parametrize("text,start,end,gran", [
    ("上月净利润",         date(2026, 6, 1),  date(2026, 6, 30), "month"),
    ("本月营收",           date(2026, 7, 1),  date(2026, 7, 5),  "month"),
    ("近30天销量",         date(2026, 6, 6),  date(2026, 7, 5),  "day"),
    ("最近7天",            date(2026, 6, 29), date(2026, 7, 5),  "day"),
    ("2026年4月退款",      date(2026, 4, 1),  date(2026, 4, 30), "month"),
    ("2026-04 的数据",     date(2026, 4, 1),  date(2026, 4, 30), "month"),
    ("今年到现在",         date(2026, 1, 1),  date(2026, 7, 5),  "year"),
    ("去年全年",           date(2025, 1, 1),  date(2025, 12, 31), "year"),
    ("第二季度",           date(2026, 4, 1),  date(2026, 6, 30), "month"),
    ("Q2",                date(2026, 4, 1),  date(2026, 6, 30), "month"),
    ("今天成交",           date(2026, 7, 5),  date(2026, 7, 5),  "day"),
    ("昨天",              date(2026, 7, 4),  date(2026, 7, 4),  "day"),
    ("5月销量",            date(2026, 5, 1),  date(2026, 5, 31), "month"),
])
def test_parse_time_cases(text, start, end, gran):
    r = parse_time(text, today=TODAY)
    assert r is not None, text
    assert (r.start, r.end, r.granularity) == (start, end, gran)


def test_parse_time_none_when_no_time_word():
    assert parse_time("各产品毛利率排行", today=TODAY) is None
    assert parse_time("", today=TODAY) is None


def test_parse_time_promo_window():
    pw = {"618": {"start": date(2026, 6, 1), "end": date(2026, 6, 20), "label": "618大促"}}
    r = parse_time("618期间销量", today=TODAY, promo_windows=pw)
    assert r is not None and r.start == date(2026, 6, 1) and r.end == date(2026, 6, 20)


def test_parse_time_promo_none_without_windows():
    # 未提供窗口常量 → 大促问法不猜, 返回 None (交上层拒答/澄清)
    assert parse_time("618卖了多少", today=TODAY) is None


# ------------------------------- charts ------------------------------- #

def test_chart_kpi_single_value():
    c = charts.pick_chart([{"name": "net", "kind": "number"}], row_count=1)
    assert c["type"] == "kpi"


def test_chart_line_for_time_series():
    c = charts.pick_chart(
        [{"name": "month", "kind": "time"}, {"name": "rev", "kind": "number"}], question="营收趋势")
    assert c["type"] == "line" and c["x"] == "month"


def test_chart_multiseries_line():
    c = charts.pick_chart([
        {"name": "month", "kind": "time"},
        {"name": "rev", "kind": "number"},
        {"name": "platform", "kind": "category"}])
    assert c["type"] == "line" and c.get("series") == "platform"


def test_chart_bar_for_ranking():
    c = charts.pick_chart(
        [{"name": "product", "kind": "category"}, {"name": "rev", "kind": "number"}],
        question="产品销售额排行")
    assert c["type"] == "bar" and c["orient"] == "horizontal"


def test_chart_pie_for_share_intent():
    c = charts.pick_chart(
        [{"name": "platform", "kind": "category"}, {"name": "rev", "kind": "number"}],
        row_count=4, question="各平台营收占比")
    assert c["type"] == "pie"


def test_chart_scatter_two_numbers():
    c = charts.pick_chart(
        [{"name": "spend", "kind": "number"}, {"name": "rev", "kind": "number"}])
    assert c["type"] == "scatter"


def test_chart_table_fallback():
    c = charts.pick_chart([{"name": "a", "kind": "category"}, {"name": "b", "kind": "category"}])
    assert c["type"] == "table"


# ------------------------------- metrics_dict ------------------------------- #

def test_every_metric_has_required_metadata():
    for key, m in metrics_dict.METRICS.items():
        assert m.cn, key
        assert m.caliber_ref, key
        assert m.verify_ref, key
        assert m.base_view in ALLOWED_VIEWS, key
        col_names = {c[0] for c in VIEW_COLUMNS[m.base_view]}
        assert m.time_field in col_names, f"{key}: time_field {m.time_field} 不在视图列"


def test_service_only_metrics_have_hint_no_sql():
    for key in metrics_dict.service_metric_keys():
        m = metrics_dict.METRICS[key]
        assert m.agg_sql is None, key
        assert m.service_hint, key


def test_sql_metrics_assemble_and_pass_gate():
    """每个可 SQL 直算指标: 用其 agg + 内置过滤 + 一个维度拼查询, 必须解析成功且过只读闸门。"""
    for key in metrics_dict.sql_metric_keys():
        m = metrics_dict.METRICS[key]
        assert m.agg_sql, key
        assert m.dims, key
        for d in m.dims:
            assert d in metrics_dict.DIMENSIONS, f"{key}: 维度 {d} 未定义"
        dim = m.dims[0]
        dim_expr = metrics_dict.dim_sql(dim, m.time_field)
        where = " AND ".join(m.builtin_filters) or "TRUE"
        sql = (f"SELECT {dim_expr} AS dim, {m.agg_sql} AS val "
               f"FROM {m.base_view} WHERE {where} GROUP BY 1 ORDER BY 2 DESC")
        # 必须通过只读六道闸 (解析 + 仅SELECT + 表白名单 + LIMIT 注入)
        r = validate_readonly_select(sql, ALLOWED_VIEWS)
        assert m.base_view in r.tables, key


def test_refill_metrics_isolated():
    # 补单指标必须用对账口径 (is_refill=TRUE), 且不与经营指标混用
    for key in ("refill_count", "refill_gmv"):
        m = metrics_dict.METRICS[key]
        assert "is_refill = TRUE" in m.builtin_filters
        assert "对账口径" in m.caliber_ref
