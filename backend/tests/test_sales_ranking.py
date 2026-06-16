# -*- coding: utf-8 -*-
"""销售排行榜 #19/#25: 内部短名替淘宝长名 + P↔PPS前缀漂移合并去重 + 总销售额去退款。"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.models.product import Product
from app.services import sales_analytics as sa


def test_ranking_internal_name_pps_merge_and_refund(db_session):
    db_session.add(Product(code="PPS24210070901", name="榉木岩板餐桌"))
    # 同一款两笔: 一笔用规范码 PPS, 一笔用漂移码 P → 应合并成一行, 显示内部短名(非淘宝长标题)
    db_session.add(Order(platform="淘宝", order_no="O1", product_code="PPS24210070901",
                         product_name="畔色 岩板实木餐桌日式简约长方形榉木家用饭桌超长淘宝标题",
                         qty=1, order_date=date(2026, 6, 10), status="paid",
                         paid_amount=Decimal("3000")))
    db_session.add(Order(platform="淘宝", order_no="O2", product_code="P24210070901",
                         product_name="畔色 另一个长标题变体",
                         qty=1, order_date=date(2026, 6, 11), status="paid",
                         paid_amount=Decimal("3000"), refund_amount=Decimal("1000")))
    db_session.flush()

    r = sa.product_ranking(db_session, granularity="month", period="2026-06")
    assert r["refund_excluded"] is True

    # P/PPS 合并 → 仅一行
    assert len(r["ranking"]) == 1
    row = r["ranking"][0]
    # 内部短名, 非淘宝长标题
    assert row["product_name"] == "榉木岩板餐桌"
    assert row["product_code"] == "PPS24210070901"
    # 总销售额去退款: 3000 + (3000 - 1000) = 5000
    assert abs(row["revenue"] - 5000) < 0.01


def test_ranking_falls_back_to_taobao_when_no_internal(db_session):
    """产品档案查不到 → 回退淘宝名(不致崩, 仍按 product_code 聚合去重)。"""
    db_session.add(Order(platform="淘宝", order_no="O3", product_code="P99999999999",
                         product_name="某未建档产品长标题", qty=2,
                         order_date=date(2026, 6, 12), status="paid",
                         paid_amount=Decimal("1000")))
    db_session.flush()
    r = sa.product_ranking(db_session, granularity="month", period="2026-06")
    assert len(r["ranking"]) == 1
    assert r["ranking"][0]["product_name"] == "某未建档产品长标题"
