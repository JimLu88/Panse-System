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

# ── 定制单 BOM 模板: 同料多尺寸合并为一行 (用户 2026-06-27 洞洞板问题) ──────────
def test_custom_order_collapses_duplicate_material(db_session):
    """定制单 BOM 是模板(同一洞洞板堆 1155/955/755 三个尺寸)→ 同料只取一行(面积最大 1155×660)
    + 标 size_uncertain/alt_size_count; 不同料(隔板轨道)不受影响, 单尺寸不标记。"""
    db = db_session
    from app.models.bom import BomLine
    from app.models.material import Material
    db.add(Material(code="AC-DD", name="MDF洞洞板-茶色", unit="每平米", price=Decimal("50"), category="杂项"))
    db.add(Material(code="AC-RAIL", name="银色隔板轨道", unit="每米", price=Decimal("10"), category="杂项"))
    for sz in ["1155*660 打孔", "955*660 打孔", "755*660 打孔"]:
        db.add(BomLine(product_code="PPSCUST01", sku_code="SC1", material_code="AC-DD",
                       material_name="MDF洞洞板-茶色", qty_per_product=Decimal("1"), remark=sz))
    db.add(BomLine(product_code="PPSCUST01", sku_code="SC1", material_code="AC-RAIL",
                   material_name="银色隔板轨道", qty_per_product=Decimal("2"), remark=None))
    o = Order(platform="淘宝", order_no="CUST1", product_code="PCUST01", sku_code="SC1",
              qty=1, is_custom=True, order_date=date(2026, 6, 1), ship_date=date(2026, 6, 5),
              status="signed", paid_amount=Decimal("3000"))
    db.add(o)
    db.flush()
    mat_info, bom_by_pcsku, bom_by_pc = prs._load_bom_and_materials(db)
    cons = prs._order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc)
    mats = cons["杂项"]["materials"]
    dd = [m for m in mats if m["material_code"] == "AC-DD"]
    rail = [m for m in mats if m["material_code"] == "AC-RAIL"]
    assert len(dd) == 1                              # 洞洞板只 1 行(不再 3 行)
    assert dd[0]["size_uncertain"] is True and dd[0]["alt_size_count"] == 2
    assert dd[0]["size_note"] == "1155*660 打孔"     # 取面积最大
    assert len(rail) == 1 and "size_uncertain" not in rail[0]   # 单尺寸料不合并不标记


def test_noncustom_keeps_distinct_sizes(db_session):
    """非定制单: 同料不同尺寸照常各算一行(不合并)。"""
    db = db_session
    from app.models.bom import BomLine
    from app.models.material import Material
    db.add(Material(code="AC-DD", name="MDF洞洞板", unit="每平米", price=Decimal("50"), category="杂项"))
    for sz in ["1155*660", "955*660"]:
        db.add(BomLine(product_code="PPSNORM01", sku_code="SN1", material_code="AC-DD",
                       material_name="MDF洞洞板", qty_per_product=Decimal("1"), remark=sz))
    o = Order(platform="淘宝", order_no="NORM1", product_code="PNORM01", sku_code="SN1",
              qty=1, is_custom=False, order_date=date(2026, 6, 1), ship_date=date(2026, 6, 5),
              status="signed", paid_amount=Decimal("3000"))
    db.add(o)
    db.flush()
    mat_info, bom_by_pcsku, bom_by_pc = prs._load_bom_and_materials(db)
    cons = prs._order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc)
    assert len([m for m in cons["杂项"]["materials"] if m["material_code"] == "AC-DD"]) == 2


def test_custom_collapses_family_across_material_codes(db_session):
    """定制单: 同一物理件的尺寸变体是【不同料号】(电力轨道 AC-0162/63/64/65)→ 按族(名字去尺寸)
    只取一行(面积最大); 不同子型号(U80 vs T25)分开不误并。(2026-07-05 修多绑)"""
    db = db_session
    from app.models.bom import BomLine
    from app.models.material import Material
    u80 = [("AC-T162", "电力轨道-Xpower-U80-黑色-2.05-2插座", "60", "2050*80"),
           ("AC-T163", "电力轨道-Xpower-U80-黑色-1.75-2插座", "55", "1750*80"),
           ("AC-T164", "电力轨道-Xpower-U80-黑色-1.45-2插座", "50", "1450*80"),
           ("AC-T165", "电力轨道-Xpower-U80-黑色-1.15-2插座", "45", "1150*80")]
    t25 = ("AC-T166", "电力轨道-Xpower-T25-黑色-2.0-2插座", "58", "2000*80")
    for code, name, price, _sz in u80 + [t25]:
        db.add(Material(code=code, name=name, unit="根", price=Decimal(price), category="电力轨道"))
    for code, name, _price, sz in u80:
        db.add(BomLine(product_code="PPSTRK01", sku_code="ST1", material_code=code,
                       material_name=name, qty_per_product=Decimal("1"), remark=sz))
    db.add(BomLine(product_code="PPSTRK01", sku_code="ST1", material_code=t25[0],
                   material_name=t25[1], qty_per_product=Decimal("1"), remark=t25[3]))
    o = Order(platform="淘宝", order_no="TRK1", product_code="PTRK01", sku_code="ST1",
              qty=1, is_custom=True, order_date=date(2026, 6, 1), ship_date=date(2026, 6, 5),
              status="signed", paid_amount=Decimal("5000"))
    db.add(o)
    db.flush()
    mat_info, bom_by_pcsku, bom_by_pc = prs._load_bom_and_materials(db)
    cons = prs._order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc)
    trk = cons["电力轨道"]["materials"]
    assert len(trk) == 2                                       # U80 四码合1 + T25 一条 = 2 (不再 5)
    u80_row = [m for m in trk if "U80" in m["part_name"]]
    assert len(u80_row) == 1 and u80_row[0]["size_uncertain"] is True
    assert u80_row[0]["material_code"] == "AC-T162"            # 取面积最大(2.05)
    assert u80_row[0]["alt_size_count"] == 3                   # 合并了其它 3 个尺寸
    t25_row = [m for m in trk if "T25" in m["part_name"]]
    assert len(t25_row) == 1                                   # T25 单独, 没被误并入 U80


def test_noncustom_template_fallback_collapses_size_variants(db_session):
    """非定制单但该 SKU 无精确 BOM → 落【产品级模板】(含全部尺寸变体、还重复列多遍)→ 一单只用一种
    尺寸 → 按族合一行(面积最大)+标 size_uncertain。修万象餐边柜电力轨道一单虚列4行。(2026-07-05)"""
    db = db_session
    from app.models.bom import BomLine
    from app.models.material import Material
    variants = [("AC-T162", "电力轨道-Xpower-U80-黑色-2.05-2插座", "655", "2.1米版"),
                ("AC-T163", "电力轨道-Xpower-U80-黑色-1.75-2插座", "577", "1.8米版"),
                ("AC-T164", "电力轨道-Xpower-U80-黑色-1.55-2插座", "534", "1.6米版"),
                ("AC-T165", "电力轨道-Xpower-U80-黑色-1.15-2插座", "421", "1.2米版")]
    for code, name, price, _sz in variants:
        db.add(Material(code=code, name=name, unit="根", price=Decimal(price), category="电力轨道"))
    # 产品级模板 BOM: 4 个尺寸变体各重复列 2 遍 = 8 行(脏 BOM), sku_code=None(非精确)
    for code, name, _p, sz in variants * 2:
        db.add(BomLine(product_code="PPSROT01", sku_code=None, material_code=code,
                       material_name=name, qty_per_product=Decimal("1"), remark=sz))
    o = Order(platform="淘宝", order_no="ROT1", product_code="PPSROT01", sku_code="SKU_NO_BOM",
              sku="旋转餐边柜1.5米", qty=1, is_custom=False, order_date=date(2026, 6, 1),
              ship_date=date(2026, 6, 5), status="signed", paid_amount=Decimal("5000"))
    db.add(o)
    db.flush()
    mat_info, by_pcsku, by_pc = prs._load_bom_and_materials(db)
    cons = prs._order_category_consumption(o, mat_info, by_pcsku, by_pc)
    trk = cons["电力轨道"]["materials"]
    assert len(trk) == 1                              # 4变体×2重复 → 合成 1 行(不再 4/8 行)
    assert trk[0]["material_code"] == "AC-T162"       # 面积最大(2.05/2.1米版)
    assert trk[0]["size_uncertain"] is True
    assert trk[0]["alt_size_count"] == 3              # 合并其它 3 个尺寸(重复行已先去重)


# (大宗对账已改「分类+BOM 驱动」, 对账/导出测试见 test_parts_monthly_recon_0626.py)
