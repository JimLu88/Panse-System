# -*- coding: utf-8 -*-
"""配件取值阶梯 (用户拍板 2026-07-13):

① SKU 行外配件有值(含显式0) → 直接用;
② SKU 行 NULL/无行 → 同产品按 |木作−账单锚| 最接近的规格行取外配件; 无锚 → id 首行(旧行为);
③ 15%×实付兜底暂不做。
案发: 定制曜黑柜(账单8800)的"定制尺寸"SKU行成本全空, 旧 limit(1) 撞到下柜行 → 配件460,
正确参照是整柜2.1 的 1598.49; 且旧代码把 NULL 当 0, 后补咨询行会让 est_parts 一重算就归零。
"""
from decimal import Decimal

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_cost_service as ocs


def _seed_product(db):
    """曜黑柜产品缩影: 下柜(460/4800) + 整柜(1598.49/8500) + 定制咨询行(全空)。"""
    db.add_all([
        PricingSku(sku_code="PPS2525013042011", product_code="PPS25250130420",
                   sku="曜黑柜-下柜-2.1米", external_parts_cost=Decimal("460"),
                   wood_cost=Decimal("4800"), physical_cost=Decimal("6000"),
                   list_price=Decimal("9000")),
        PricingSku(sku_code="PPS2525013042015", product_code="PPS25250130420",
                   sku="曜黑柜-整柜-2.1米", external_parts_cost=Decimal("1598.49"),
                   wood_cost=Decimal("8500"), physical_cost=Decimal("11500"),
                   list_price=Decimal("16000")),
        PricingSku(sku_code="PPS2525013042099", product_code="PPS25250130420",
                   sku="定制尺寸", list_price=Decimal("100")),   # 成本全空的咨询行
    ])
    db.commit()


def test_sku_row_with_value_used_directly(db_session):
    """①: SKU 行有外配件 → 直接用, 不进阶梯。"""
    _seed_product(db_session)
    o = Order(platform="淘宝", order_no="L1", sku_code="PPS2525013042015",
              product_code="PPS25250130420", qty=1)
    assert ocs._pricing_parts_for(db_session, o) == Decimal("1598.49")


def test_null_sku_row_falls_to_closest_wood(db_session):
    """②: 咨询行(NULL) + 木作账单8800 → 挑木作最接近的整柜行 → 1598.49 (不再撞下柜460)。"""
    _seed_product(db_session)
    o = Order(platform="淘宝", order_no="L2", sku_code="PPS2525013042099",
              product_code="PPS25250130420", qty=1, actual_cost=Decimal("8800"))
    assert ocs._pricing_parts_for(db_session, o) == Decimal("1598.49")


def test_no_anchor_keeps_first_row(db_session):
    """②无锚(无工厂账单): 保持旧行为取 id 首行(下柜460), 不瞎猜。"""
    _seed_product(db_session)
    o = Order(platform="淘宝", order_no="L3", sku_code="PPS2525013042099",
              product_code="PPS25250130420", qty=1)
    assert ocs._pricing_parts_for(db_session, o) == Decimal("460")


def test_explicit_zero_is_authoritative(db_session):
    """①: 显式 0 = 该款标准无外配件, 直接用 0, 不落阶梯。"""
    db_session.add(PricingSku(sku_code="Z1", product_code="ZP", sku="无配件款",
                              external_parts_cost=Decimal("0"), wood_cost=Decimal("1000"),
                              list_price=Decimal("2000")))
    db_session.commit()
    o = Order(platform="淘宝", order_no="L4", sku_code="Z1", product_code="ZP", qty=1)
    assert ocs._pricing_parts_for(db_session, o) == Decimal("0")


def test_recompute_writes_ladder_value(db_session):
    """端到端: 重算后 est_parts=阶梯值(整柜1598.49), v2实配件分支吃到正确配件。"""
    _seed_product(db_session)
    o = Order(platform="淘宝", order_no="L5", sku_code="PPS2525013042099",
              product_code="PPS25250130420", qty=1, is_custom=True,
              paid_amount=Decimal("15200"), actual_cost=Decimal("8800"), status="signed")
    db_session.add(o)
    db_session.commit()
    ocs.recompute_and_save(db_session, o)
    assert o.est_parts == Decimal("1598.49")
