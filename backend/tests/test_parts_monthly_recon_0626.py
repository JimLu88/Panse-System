# -*- coding: utf-8 -*-
"""配件对账(方向1, 分类+BOM驱动) + 工厂月度对账 + 当月发货清单导出 (用户 2026-06-26)。

对账不再靠关键词: 由 Material.category + BOM 驱动。预估 = Σ(发货成交单 BOM 里该分类外采配件
price×qty), 按发货日期 ship_date 分月; 实际 = 工厂月度对账总额(PartsMonthlyRecon, key=分类)。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.services import parts_recon_service as prs


def _mat(db, code, name, price, category, unit="块"):
    db.add(Material(code=code, name=name, price=Decimal(str(price)), category=category, unit=unit))


def _bom(db, mcode, mname, qty=1, remark=None, pc="PPSDS01", sc="SDS"):
    db.add(BomLine(product_code=pc, sku_code=sc, material_code=mcode, material_name=mname,
                   qty_per_product=Decimal(str(qty)), remark=remark))


def _order(db, no, ship, pc="PPSDS01", sc="SDS", sku="洞石餐边柜-1.2米", qty=1, est=Decimal("300")):
    o = Order(platform="淘宝", order_no=no, product_code=pc, sku_code=sc, sku=sku,
              product_name="洞石餐边柜", qty=qty, order_date=date(2026, 1, 1), ship_date=ship,
              status="signed", paid_amount=Decimal("5000"), est_parts=est)
    db.add(o)
    return o


def _seed_yanban(db):
    """岩板分类: 产品 PPSDS01 的 BOM 有 AC-RB(岩板¥90) + WD木作(应排除)。"""
    _mat(db, "AC-RB", "洞石岩板", 90, "岩板")
    _mat(db, "WD-1", "木作部分", 0, "木作")
    _bom(db, "AC-RB", "洞石岩板", 1, "1200*480")
    _bom(db, "WD-1", "木作", 1)


# ── 工厂月度对账 CRUD (material_key 现在是分类) ──────────────────────────────
def test_save_list_delete_monthly_recon(db_session):
    db = db_session
    r = prs.save_monthly_recon(db, material_key="岩板", year_month="2026-02",
                               actual_total=Decimal("8600"), supplier="宋磊岩板")
    assert r["id"] and r["actual_total"] == 8600.0 and r["material_name"] == "岩板"
    rows = prs.list_monthly_recon(db, material_key="岩板")
    assert len(rows) == 1 and rows[0]["year_month"] == "2026-02"
    r2 = prs.save_monthly_recon(db, material_key="岩板", year_month="2026-02",
                                actual_total=Decimal("9000"), recon_id=r["id"])
    assert r2["actual_total"] == 9000.0
    assert len(prs.list_monthly_recon(db, material_key="岩板")) == 1
    assert prs.delete_monthly_recon(db, r["id"]) is True
    assert prs.list_monthly_recon(db, material_key="岩板") == []


def test_save_empty_category_rejected(db_session):
    with pytest.raises(ValueError):
        prs.save_monthly_recon(db_session, material_key="   ", year_month="2026-02",
                               actual_total=Decimal("100"))


# ── 对账: BOM 驱动、按分类、工厂实际/差异 ───────────────────────────────────
def test_recon_category_driven_standard_and_variance(db_session):
    db = db_session
    _seed_yanban(db)
    _order(db, "D1", date(2026, 2, 10))
    _order(db, "D2", date(2026, 2, 20))
    db.flush()
    prs.save_monthly_recon(db, material_key="岩板", year_month="2026-02", actual_total=Decimal("220"))
    rc = prs.bulk_material_recon(db)
    assert rc["category_driven"] is True
    yan = next(m for m in rc["materials"] if m["key"] == "岩板")
    p = {r["period"]: r for r in yan["periods"]}["2026-02"]
    assert p["standard_consume"] == 180.0      # 2 单 × (1×90)
    assert p["factory_actual"] == 220.0
    assert p["variance"] == 40.0               # 220 − 180
    assert p["order_count"] == 2
    assert all(m["key"] != "木作" for m in rc["materials"])   # 木作不进对账


def test_recon_uses_ship_date_not_order_date(db_session):
    db = db_session
    _seed_yanban(db)
    _order(db, "S1", date(2026, 2, 15))   # 下单 1 月、发货 2 月
    db.flush()
    yan = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "岩板")
    p = {r["period"]: r for r in yan["periods"]}
    assert "2026-02" in p and p["2026-02"]["standard_consume"] == 90.0
    assert "2026-01" not in p


def test_recon_excludes_unshipped_and_refill(db_session):
    db = db_session
    _seed_yanban(db)
    db.add(Order(platform="淘宝", order_no="U1", product_code="PPSDS01", sku_code="SDS", sku="x",
                 qty=1, ship_date=None, status="signed", paid_amount=Decimal("4000")))
    db.add(Order(platform="淘宝", order_no="U2", is_refill=True, product_code="PPSDS01", sku_code="SDS",
                 sku="x", qty=1, ship_date=date(2026, 2, 10), status="signed", paid_amount=Decimal("4000")))
    db.flush()
    yan = [m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "岩板"]
    assert (not yan) or yan[0]["total_standard"] == 0.0   # 未发货/补单都不消耗


def test_historical_avg_rolls(db_session):
    db = db_session
    _seed_yanban(db)
    _order(db, "A1", date(2026, 2, 10))
    _order(db, "A2", date(2026, 2, 20))      # 2 月 2 单 → 预估 180
    _order(db, "B1", date(2026, 3, 5))
    _order(db, "B2", date(2026, 3, 6))
    _order(db, "B3", date(2026, 3, 7))       # 3 月 3 单
    db.flush()
    prs.save_monthly_recon(db, material_key="岩板", year_month="2026-02", actual_total=Decimal("200"))
    yan = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "岩板")
    p = {r["period"]: r for r in yan["periods"]}
    assert p["2026-02"]["historical_avg"] == 180.0   # 无更早 → 回退预估
    assert p["2026-03"]["historical_avg"] == 300.0   # 2 月每单 100 × 3 单


# ── 导出 ────────────────────────────────────────────────────────────────────
def test_export_all_shipped_by_ship_date(db_session):
    db = db_session
    _seed_yanban(db)
    _order(db, "S1", date(2026, 2, 10))
    _order(db, "S2", date(2026, 3, 1))
    db.flush()
    res = prs.export_shipped_orders(db, year_month="2026-02")
    assert res["material_key"] is None
    assert [o["order_no"] for o in res["orders"]] == ["S1"]   # 只 2 月发货


def test_export_by_category_bom_detail_dedup_and_woodwork(db_session):
    db = db_session
    _mat(db, "AC-RB", "洞石岩板", 90, "岩板")
    _mat(db, "AC-FM", "洞石纹理饰面板", 60, "洞石饰面板")
    _mat(db, "WD-1", "黑胡桃木岩板餐桌-木作部分", 0, "木作")   # 名字带"岩板"但按料号前缀排
    _bom(db, "AC-RB", "洞石岩板", 1, "1200*480")
    _bom(db, "AC-RB", "洞石岩板", 1, "1200 *480")   # 空格不一致 → 去重只留一行
    _bom(db, "AC-FM", "洞石纹理饰面板", 1)
    _bom(db, "WD-1", "木作", 1)
    _order(db, "M1", date(2026, 2, 10))
    db.flush()
    res = prs.export_shipped_orders(db, year_month="2026-02", material_key="岩板")
    names = [p["part_name"] for p in res["orders"][0]["bom_parts"]]
    assert names == ["洞石岩板"]          # 只岩板分类; 去重 1 行; 木作排除; 饰面板属另一分类
    assert res["orders"][0]["bom_parts"][0]["category"] == "岩板"
    res2 = prs.export_shipped_orders(db, year_month="2026-02", material_key="洞石饰面板")
    assert [p["part_name"] for p in res2["orders"][0]["bom_parts"]] == ["洞石纹理饰面板"]
