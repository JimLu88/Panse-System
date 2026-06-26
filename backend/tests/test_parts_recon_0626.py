# -*- coding: utf-8 -*-
"""配件 epic P2 (用户 2026-06-26): est_parts 标准估值列 + 逐单采购汇总 + 大宗材料对账。

重点验证用户铁律: 大宗材料对账消费窗口按订单【发货日期 ship_date】圈定, 不用下单日期。
"""
from datetime import date
from decimal import Decimal

from app.models.order import Order, PartPurchase
from app.models.pricing import PricingSku
from app.services import order_cost_service as ocs
from app.services import parts_recon_service as prs


def _sku(db, sku_code="S1", parts=Decimal("240"), physical=Decimal("1000"),
         wood=Decimal("600"), product_code="PPS11111111111"):
    db.add(PricingSku(product_code=product_code, sku_code=sku_code, sku="基础",
                      physical_cost=physical, factory_cost=Decimal("800"),
                      wood_cost=wood, external_parts_cost=parts))


# ── est_parts 派生 ──────────────────────────────────────────────────────────
def test_recompute_sets_est_parts(db_session):
    """recompute_and_save 写 est_parts = 定价 external_parts_cost × 真实计价件数(qty=1)。"""
    db = db_session
    _sku(db)
    o = Order(platform="淘宝", order_no="E1", product_code="PPS11111111111", sku_code="S1",
              qty=1, order_date=date(2026, 6, 1), status="signed", paid_amount=Decimal("3000"))
    db.add(o)
    db.flush()
    ocs.recompute_and_save(db, o)
    assert o.est_parts == Decimal("240.00")


def test_est_parts_scales_with_real_qty(db_session):
    """真多件(qty=2 且件均实付≥单件成本) → est_parts ×2; 定制单 → ×1。"""
    db = db_session
    _sku(db)
    o = Order(platform="淘宝", order_no="E2", product_code="PPS11111111111", sku_code="S1",
              qty=2, order_date=date(2026, 6, 1), status="signed", paid_amount=Decimal("6000"))
    db.add(o)
    db.flush()
    ocs.recompute_and_save(db, o)
    assert o.est_parts == Decimal("480.00")   # 240 × 2

    c = Order(platform="淘宝", order_no="E3", product_code="PPS11111111111", sku_code="S1",
              qty=2, is_custom=True, order_date=date(2026, 6, 1), status="signed",
              paid_amount=Decimal("6000"))
    db.add(c)
    db.flush()
    ocs.recompute_and_save(db, c)
    assert c.est_parts == Decimal("240.00")   # 定制不放大件数


def test_backfill_est_parts_zero_and_none(db_session):
    """backfill: 非产品单(补单)→ 0; 无定价参照 → None(留空)。"""
    db = db_session
    _sku(db)
    matched = Order(platform="淘宝", order_no="B1", product_code="PPS11111111111", sku_code="S1",
                    qty=1, order_date=date(2026, 6, 1), status="signed", paid_amount=Decimal("3000"))
    refill = Order(platform="淘宝", order_no="B2", is_refill=True, qty=1,
                   order_date=date(2026, 6, 1), status="signed", paid_amount=Decimal("100"),
                   product_name="补拍链接")
    nopricing = Order(platform="淘宝", order_no="B3", product_code="ZZZNOPE", sku_code="ZZZ",
                      qty=1, order_date=date(2026, 6, 1), status="signed", paid_amount=Decimal("999"))
    db.add_all([matched, refill, nopricing])
    db.flush()
    res = ocs.backfill_est_parts(db)
    assert res["set"] >= 2
    assert matched.est_parts == Decimal("240.00")
    assert refill.est_parts == Decimal("0.00")     # 非产品 → 标准配件0
    assert nopricing.est_parts is None             # 无定价 → 留空


# ── A. 逐单采购汇总 → actual_parts ──────────────────────────────────────────
def test_aggregate_related_purchases_dryrun_then_apply(db_session):
    """dry-run 不落库(预览含 physical 变化); apply=True 才写 actual_parts。"""
    db = db_session
    _sku(db)
    o = Order(platform="淘宝", order_no="AGG1", product_code="PPS11111111111", sku_code="S1",
              qty=1, order_date=date(2026, 6, 1), status="signed", paid_amount=Decimal("3000"),
              actual_cost=Decimal("600"))
    db.add(o)
    db.add(PartPurchase(purchase_no="PUR1", material_name="玻璃", related_order_no="AGG1",
                        total_amount=Decimal("500"), purchase_date=date(2026, 6, 2)))
    db.add(PartPurchase(purchase_no="PUR2", material_name="服务费", related_order_no="AGG1",
                        total_amount=Decimal("99"), purchase_date=date(2026, 6, 2)))  # 非配件→排除
    db.flush()

    preview = prs.aggregate_related_purchases(db, apply=False)
    assert preview["applied"] is False
    assert preview["matched_orders"] == 1
    item = preview["items"][0]
    assert item["order_no"] == "AGG1" and item["new_actual_parts"] == 500.0
    db.refresh(o)
    assert o.actual_parts is None   # dry-run 未落库

    applied = prs.aggregate_related_purchases(db, apply=True)
    assert applied["applied"] is True and applied["applied_count"] == 1
    db.refresh(o)
    assert o.actual_parts == Decimal("500.00")   # 已落库, 排除了「服务费」99

# (大宗对账已改「分类+BOM 驱动」, 对账/导出测试见 test_parts_monthly_recon_0626.py)
