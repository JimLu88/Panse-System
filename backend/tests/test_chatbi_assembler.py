# -*- coding: utf-8 -*-
"""ChatBI 半生成拼装器单测: 校验 + 确定性拼 SQL + 注入中和。"""
from datetime import date

import pytest

from app.chatbi.assembler import AssemblerError, assemble
from app.chatbi.time_parser import TimeRange


def test_valid_spec_assembles_and_passes_gate():
    spec = {"metric": "net_revenue", "dimensions": ["product"],
            "filters": [{"field": "product_name", "op": "contains", "value": "餐边柜"}],
            "top_n": 10, "order": "desc"}
    q = assemble(spec)
    assert "chatbi_v_orders" in q.gate.tables
    assert q.sql.upper().count("SELECT") >= 1
    assert "ILIKE" in q.sql.upper()
    # 内置口径过滤必须在 (排补单 + 真实成交)
    assert "is_refill" in q.sql.lower()
    assert "is_settled_sale" in q.sql.lower()
    kinds = {c["name"]: c["kind"] for c in q.columns}
    assert kinds["val"] == "number" and kinds["d1"] == "category"


def test_time_range_applied():
    spec = {"metric": "net_revenue", "dimensions": ["month"]}
    tr = TimeRange(date(2026, 6, 1), date(2026, 6, 30), "month", "上月")
    q = assemble(spec, time_range=tr)
    assert "BETWEEN" in q.sql.upper()
    assert "2026-06-01" in q.sql and "2026-06-30" in q.sql
    assert any("上月" in n for n in q.caliber_notes)


def test_service_only_metric_rejected():
    with pytest.raises(AssemblerError):
        assemble({"metric": "net_profit", "dimensions": ["product"]})
    with pytest.raises(AssemblerError):
        assemble({"metric": "gross_margin_rate"})


def test_unknown_metric_rejected():
    with pytest.raises(AssemblerError):
        assemble({"metric": "definitely_not_a_metric"})


def test_dimension_outside_declared_rejected():
    # refund_rate 未声明 product 维度 → 拒
    with pytest.raises(AssemblerError):
        assemble({"metric": "refund_rate", "dimensions": ["product"]})


def test_disallowed_filter_field_rejected():
    with pytest.raises(AssemblerError):
        assemble({"metric": "net_revenue",
                  "filters": [{"field": "paid_amount", "op": "eq", "value": 1}]})


def test_numeric_op_requires_number():
    with pytest.raises(AssemblerError):
        assemble({"metric": "net_revenue",
                  "filters": [{"field": "product_name", "op": "gt", "value": "abc"}]})


def test_injection_value_is_neutralized():
    # 恶意过滤值必须被中和成单一字符串字面量, 不产生第二条语句; 仍过闸门。
    evil = "x'; DROP TABLE chatbi_v_orders; --"
    spec = {"metric": "net_revenue", "dimensions": ["product"],
            "filters": [{"field": "product_name", "op": "eq", "value": evil}]}
    q = assemble(spec)                      # 不抛 = 已中和 (单引号双写)
    assert "''" in q.sql                    # 单引号被双写
    # 过闸门 = 只解析出一条 SELECT (注入未生效)
    assert q.gate.limited_to <= 1000


def test_refill_metric_carries_warning():
    q = assemble({"metric": "refill_gmv", "dimensions": ["month"]})
    assert any("对账口径" in n or "补单" in n for n in q.caliber_notes)


def test_scalar_kpi_no_dimension():
    q = assemble({"metric": "net_revenue", "dimensions": []})
    assert "GROUP BY" not in q.sql.upper()
    assert len(q.columns) == 1 and q.columns[0]["name"] == "val"
