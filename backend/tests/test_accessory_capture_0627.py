# -*- coding: utf-8 -*-
"""配件采购"备注识别"零星采购 (用户 2026-06-27): 备注写订单号/人名 → 自动归账。"""
from decimal import Decimal

from sqlalchemy import select

from app.models.order import Order, PartPurchase
from app.models.supplier import Supplier
from app.services import accessory_capture_service as acs


def test_link_order_from_remark(db_session):
    db = db_session
    db.add(Order(platform="淘宝", order_no="3306971283335045757", qty=1, status="signed",
                 paid_amount=Decimal("3000")))
    # 备注里写了真实订单号 → 应链上
    db.add(PartPurchase(purchase_no="P1", supplier="宋磊", qty=Decimal("1"), amount=Decimal("750"),
                        material_name="岩板 订单3306971283335045757 货款"))
    # 备注里是个不存在的号 → 不链
    db.add(PartPurchase(purchase_no="P2", supplier="宋磊", qty=Decimal("1"), amount=Decimal("100"),
                        material_name="岩板 9999999999999999999"))
    db.flush()

    prev = acs.link_orders_from_remark(db, apply=False)
    assert prev["linked"] == 1 and prev["applied"] is False
    res = acs.link_orders_from_remark(db, apply=True)
    assert res["linked"] == 1
    p1 = db.execute(select(PartPurchase).where(PartPurchase.purchase_no == "P1")).scalar_one()
    p2 = db.execute(select(PartPurchase).where(PartPurchase.purchase_no == "P2")).scalar_one()
    assert p1.related_order_no == "3306971283335045757"
    assert p2.related_order_no in (None, "")    # 假号不链


def test_relabel_supplier_from_remark(db_session):
    db = db_session
    db.add(Supplier(name="老孙木皮廠", supplier_type="veneer", is_active=True,
                    alipay_counterparty_keywords=["老孙木皮廠", "老孙"]))
    # 匿名付款码采购, 备注提到"老孙" → 改挂到 老孙木皮廠
    db.add(PartPurchase(purchase_no="P3", supplier="收钱码收款", qty=Decimal("1"),
                        amount=Decimal("640"), material_name="老孙 18mm贴皮采购"))
    # 正常对手方(非匿名)即使备注有关键字也不动
    db.add(PartPurchase(purchase_no="P4", supplier="泰盛隆", qty=Decimal("1"),
                        amount=Decimal("815"), material_name="老孙 贴皮"))
    db.flush()

    res = acs.relabel_supplier_from_remark(db, apply=True)
    assert res["relabeled"] == 1
    p3 = db.execute(select(PartPurchase).where(PartPurchase.purchase_no == "P3")).scalar_one()
    p4 = db.execute(select(PartPurchase).where(PartPurchase.purchase_no == "P4")).scalar_one()
    assert p3.supplier == "老孙木皮廠"      # 匿名 → 改挂
    assert p4.supplier == "泰盛隆"          # 正常对手方不动
