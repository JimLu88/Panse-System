# -*- coding: utf-8 -*-
"""配件「工厂月度对账」(用户 2026-06-26): 月度总额录入 + 历史平均/预估/实际三列 + 当月发货清单导出。

口径: 按发货日期 ship_date 圈当月; 实际=工厂返回月度总额; 历史平均=过去已对账月每单实际均值×本月单数。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.services import parts_recon_service as prs


def _dongshi_order(db, no, ship, est=Decimal("300"), product_code="PPSDS01", sku_code="SDS"):
    o = Order(platform="淘宝", order_no=no, product_code=product_code, sku_code=sku_code,
              sku="洞石餐边柜-1.2米", product_name="洞石餐边柜", qty=1,
              order_date=date(2026, 1, 1), ship_date=ship, status="signed",
              paid_amount=Decimal("5000"), est_parts=est)
    db.add(o)
    return o


def test_save_list_delete_monthly_recon(db_session):
    db = db_session
    r = prs.save_monthly_recon(db, material_key="dongshi", year_month="2026-02",
                               actual_total=Decimal("8600"), supplier="宋磊岩板")
    assert r["id"] and r["actual_total"] == 8600.0 and r["material_name"]
    rows = prs.list_monthly_recon(db, material_key="dongshi")
    assert len(rows) == 1 and rows[0]["year_month"] == "2026-02"
    r2 = prs.save_monthly_recon(db, material_key="dongshi", year_month="2026-02",
                                actual_total=Decimal("9000"), recon_id=r["id"])
    assert r2["actual_total"] == 9000.0                      # 更新而非新增
    assert len(prs.list_monthly_recon(db, material_key="dongshi")) == 1
    assert prs.delete_monthly_recon(db, r["id"]) is True
    assert prs.list_monthly_recon(db, material_key="dongshi") == []


def test_save_unknown_material_rejected(db_session):
    with pytest.raises(ValueError):
        prs.save_monthly_recon(db_session, material_key="nope", year_month="2026-02",
                               actual_total=Decimal("100"))


def test_recon_includes_factory_actual_and_variance(db_session):
    db = db_session
    _dongshi_order(db, "D1", date(2026, 2, 10), est=Decimal("300"))
    _dongshi_order(db, "D2", date(2026, 2, 20), est=Decimal("300"))
    db.flush()
    prs.save_monthly_recon(db, material_key="dongshi", year_month="2026-02", actual_total=Decimal("800"))
    ds = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "dongshi")
    p = {r["period"]: r for r in ds["periods"]}["2026-02"]
    assert p["standard_consume"] == 600.0       # 预估 Σest
    assert p["factory_actual"] == 800.0          # 实际(工厂月度)
    assert p["has_factory_actual"] is True
    assert p["variance"] == 200.0                # 实际 − 预估
    assert p["order_count"] == 2
    assert ds["total_factory_actual"] == 800.0


def test_historical_avg_rolls_from_past_reconciled_months(db_session):
    db = db_session
    _dongshi_order(db, "A1", date(2026, 2, 10))
    _dongshi_order(db, "A2", date(2026, 2, 20))      # 2月2单
    _dongshi_order(db, "B1", date(2026, 3, 5))
    _dongshi_order(db, "B2", date(2026, 3, 6))
    _dongshi_order(db, "B3", date(2026, 3, 7))       # 3月3单(未对账)
    db.flush()
    prs.save_monthly_recon(db, material_key="dongshi", year_month="2026-02", actual_total=Decimal("800"))
    ds = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "dongshi")
    p = {r["period"]: r for r in ds["periods"]}
    assert p["2026-02"]["historical_avg"] == 600.0       # 无更早历史 → 回退预估
    assert p["2026-03"]["historical_avg"] == 1200.0      # 2月每单400 × 3单
    assert p["2026-03"]["has_factory_actual"] is False


def test_export_all_shipped_orders_by_ship_date(db_session):
    db = db_session
    _dongshi_order(db, "S1", date(2026, 2, 10))
    _dongshi_order(db, "S2", date(2026, 3, 1))           # 不在2月
    db.flush()
    res = prs.export_shipped_orders(db, year_month="2026-02")
    assert res["material_key"] is None
    assert [o["order_no"] for o in res["orders"]] == ["S1"]   # 只列2月发货


def test_export_by_material_includes_bom_parts(db_session):
    db = db_session
    db.add(Material(code="AC-DS", name="洞石饰面板", price=Decimal("85"), unit="张"))
    db.add(BomLine(product_code="PPSDS01", sku_code="SDS", material_code="AC-DS",
                   material_name="洞石饰面板", qty_per_product=Decimal("2"), unit="张",
                   remark="背板 1180×680mm"))
    _dongshi_order(db, "M1", date(2026, 2, 10))
    db.flush()
    res = prs.export_shipped_orders(db, year_month="2026-02", material_key="dongshi")
    assert res["material_name"] and len(res["orders"]) == 1
    o = res["orders"][0]
    assert o["order_no"] == "M1" and o["sku"] == "洞石餐边柜-1.2米"
    parts = o["bom_parts"]
    assert len(parts) == 1 and parts[0]["part_name"] == "洞石饰面板"
    assert parts[0]["qty"] == 2.0
    assert parts[0]["category"] == "洞石饰面板"            # 分类(与岩板分开列)
    assert "1180" in (parts[0]["size_note"] or "")       # 预设尺寸列出, 方便工厂对照


def test_export_excludes_woodwork_and_dedups(db_session):
    """图一/图二修复: 木作(WD-)即使名字含"岩板"也排除; 模板堆叠的同料同尺寸去重; 岩板/饰面板分类。"""
    db = db_session
    # 物料名(Material.name)是清单显示名; 木作名里带"岩板"(产品名) → 名字会误命中, 必须按料号前缀排除(图一 bug)
    db.add(Material(code="WD-9", name="黑胡桃木岩板餐桌-木作部分", price=Decimal("0"), unit="套"))
    db.add(Material(code="AC-RB", name="洞石岩板", price=Decimal("90"), unit="块"))
    db.add(Material(code="AC-FM", name="洞石纹理饰面板", price=Decimal("60"), unit="块"))
    db.add(BomLine(product_code="PPSDUP1", sku_code="SDUP", material_code="WD-9",
                   material_name="木作", qty_per_product=Decimal("1")))
    # 同料同尺寸重复两行(模拟定制大杂烩模板堆叠) → 去重只留一行(图二 bug)
    db.add(BomLine(product_code="PPSDUP1", sku_code="SDUP", material_code="AC-RB",
                   material_name="x", qty_per_product=Decimal("1"), remark="1200*480"))
    db.add(BomLine(product_code="PPSDUP1", sku_code="SDUP", material_code="AC-RB",
                   material_name="x", qty_per_product=Decimal("1"), remark="1200*480"))
    db.add(BomLine(product_code="PPSDUP1", sku_code="SDUP", material_code="AC-FM",
                   material_name="x", qty_per_product=Decimal("1")))
    o = Order(platform="淘宝", order_no="DUP1", product_code="PPSDUP1", sku_code="SDUP",
              sku="岩板餐桌", product_name="岩板餐桌", qty=1, order_date=date(2026, 1, 1),
              ship_date=date(2026, 2, 10), status="signed", paid_amount=Decimal("4000"),
              est_parts=Decimal("200"))
    db.add(o)
    db.flush()
    res = prs.export_shipped_orders(db, year_month="2026-02", material_key="dongshi")
    parts = res["orders"][0]["bom_parts"]
    names = [p["part_name"] for p in parts]
    assert "黑胡桃木岩板餐桌-木作部分" not in names      # 木作按前缀排除(名字含"岩板"也不进)
    assert names.count("洞石岩板") == 1                  # 同料同尺寸去重(模板堆叠塌成一行)
    assert "洞石纹理饰面板" in names                      # 饰面板保留
    cats = [p["category"] for p in parts]
    assert cats == ["岩板", "洞石饰面板"]                 # 分开列: 岩板 在 洞石饰面板 前
