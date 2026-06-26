# -*- coding: utf-8 -*-
"""配件采购 Excel/CSV 导入 + 导入后自动匹配 (用户 2026-06-27: 一次导入自动匹配料号/分类/订单号)。"""
from decimal import Decimal

from sqlalchemy import select

from app.models.material import Material
from app.models.order import Order, PartPurchase
from app.services import accessory_capture_service as acs
from app.services import purchase_table_import


def test_import_purchases_with_auto_match(db_session):
    db = db_session
    db.add(Material(code="AC-RB", name="洞石岩板", price=Decimal("90"), category="岩板", unit="块"))
    db.add(Order(platform="淘宝", order_no="3306971283335045757", qty=1, status="signed",
                 paid_amount=Decimal("3000")))
    db.flush()
    csv = (
        "日期,供应商,配件名称,数量,金额,备注\n"
        "2026-05-10,宋磊,洞石岩板,2,750,订单3306971283335045757 货款\n"
    ).encode("utf-8")
    res = purchase_table_import.import_purchases_table_core(db, csv, "配件采购.csv")
    assert res["inserted"] == 1
    p = db.execute(select(PartPurchase).where(PartPurchase.supplier == "宋磊")).scalar_one()
    assert p.material_code == "AC-RB"                      # 名称子串 → 自动配料号(→分类岩板)
    assert p.related_order_no == "3306971283335045757"     # 备注订单号 → 自动链
    assert res["auto_match"]["material_matched"] >= 1 and res["auto_match"]["order_linked"] >= 1


def test_material_match_category_fallback(db_session):
    """物料名对不上具体料, 但按类别关键词命中分类 → 取该分类代表料号(让它进零星对账按分类)。"""
    db = db_session
    db.add(Material(code="AC-RB1", name="12mm黑色岩板", price=Decimal("90"), category="岩板"))
    # 采购名 "岩板货款" 对不上 "12mm黑色岩板" 全名, 但"岩板"关键词命中分类 岩板
    db.add(PartPurchase(purchase_no="QP1", supplier="宋磊", material_name="岩板货款",
                        qty=Decimal("1"), amount=Decimal("750")))
    db.flush()
    res = acs.match_material_code(db, apply=True)
    assert res["matched"] == 1
    p = db.execute(select(PartPurchase).where(PartPurchase.purchase_no == "QP1")).scalar_one()
    assert p.material_code == "AC-RB1"   # 类别(岩板)代表料号
