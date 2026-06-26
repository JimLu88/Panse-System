# -*- coding: utf-8 -*-
"""供应商评分 6 维全自动 (用户 2026-06-27): 价格竞争力/对账一致性/按时率/采购规模 都从现有数据算。
(test_phase8_tier1.py 已有"出分/排名"基础测试; 本文件锁住各新维度的具体算法。)"""
from datetime import date
from decimal import Decimal

from app.models.order import Order, PartPurchase
from app.models.supplier import Supplier
from app.services import supplier_score_service as sss


def _purch(db, no, supplier, mcode, qty, price, day=10, related=None):
    db.add(PartPurchase(
        purchase_no=no, supplier=supplier, material_code=mcode, material_name=mcode,
        qty=Decimal(qty), unit_price=Decimal(price), amount=Decimal(qty) * Decimal(price),
        total_amount=Decimal(qty) * Decimal(price), purchase_date=date(2026, 5, day),
        related_order_no=related))


def test_six_dim_scoring_auto(db_session):
    db = db_session
    db.add_all([
        Supplier(name="甲五金", supplier_type="hardware", is_active=True),
        Supplier(name="乙五金", supplier_type="hardware", is_active=True),
    ])
    # 同料 AC-X: 甲 ¥8(便宜) / 乙 ¥12(贵); 全体均价 10 → 甲 ratio0.8→竞争力1.0, 乙 ratio1.2→0.8
    _purch(db, "P1", "甲五金", "AC-X", "10", "8", day=10, related="O1")
    _purch(db, "P2", "乙五金", "AC-X", "10", "12", day=10)
    # 甲的采购关联订单 O1(5/20发货), 采购 5/10 在发货前 → 按时 + 可追溯(对账一致)
    db.add(Order(platform="淘宝", order_no="O1", qty=1, status="signed",
                 order_date=date(2026, 5, 1), ship_date=date(2026, 5, 20),
                 paid_amount=Decimal("3000")))
    db.flush()

    rows = sss.compute_for_month(db, 2026, 5)
    by_name = {db.get(Supplier, r.supplier_id).name: r for r in rows}
    a, b = by_name["甲五金"], by_name["乙五金"]

    # 价格竞争力: 甲便宜 → 1.0, 乙贵 → 0.8
    assert a.detail_json["price_competitiveness"]["score"] == 1.0
    assert abs(b.detail_json["price_competitiveness"]["score"] - 0.8) < 1e-6
    # 甲: 按时率 1.0(5/10≤5/20) + 对账一致 1.0(可追溯 O1)
    assert a.detail_json["on_time"]["rate"] == 1.0
    assert a.detail_json["recon_consistency"]["matched_rate"] == 1.0
    # 乙: 无关联订单 → 按时无可评估 + 对账 0(不可追溯)
    assert b.detail_json["on_time"]["rate"] is None
    assert b.detail_json["recon_consistency"]["matched_rate"] == 0.0
    # 综合: 甲 > 乙 + 排名甲第一
    assert a.score > b.score and a.rank == 1
    # 采购规模/依赖度: 占比 > 0
    assert "scale" in a.detail_json and a.detail_json["scale"]["share_pct"] > 0


def test_single_source_flag(db_session):
    db = db_session
    db.add(Supplier(name="洞石厂", supplier_type="finish_panel", is_active=True))
    _purch(db, "P9", "洞石厂", "AC-DONG", "5", "100", day=12)   # 只此一家供 AC-DONG
    db.flush()
    rows = sss.compute_for_month(db, 2026, 5)
    r = rows[0]
    assert "AC-DONG" in r.detail_json["scale"]["single_source_materials"]
    assert r.detail_json["scale"]["single_source_count"] == 1
