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
    assert "1180" in (parts[0]["size_note"] or "")       # 预设尺寸列出, 方便工厂对照
