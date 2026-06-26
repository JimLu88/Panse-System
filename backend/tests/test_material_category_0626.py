# -*- coding: utf-8 -*-
"""配件分类 (方向1, 用户 2026-06-26): 批量归类 + 双面胶/螺丝入全产品 BOM。"""
from decimal import Decimal

from sqlalchemy import select

from app.models.bom import BomLine
from app.models.material import Material
from app.services import material_category_service as mcs


def _cats(db):
    return {m.code: m.category for m in db.execute(select(Material)).scalars().all()}


def test_auto_categorize_ac_by_keyword_and_prefix(db_session):
    db = db_session
    db.add_all([
        Material(code="AC-1", name="12mm黑色岩板"),
        Material(code="AC-2", name="洞石纹理饰面板"),
        Material(code="AC-3", name="电力轨道-Xpower-T25"),
        Material(code="AC-4", name="5mm超白玻璃"),
        Material(code="AC-5", name="灯带变压器"),
        Material(code="AC-6", name="榉木床铺板-1.2米"),
        Material(code="AC-7", name="玻璃床头柜橡胶垫"),   # 含"玻璃"但应归五金(橡胶垫优先)
        Material(code="WD-1", name="木作部分"),
        Material(code="MP-1", name="人工费"),
        Material(code="AC-9", name="某种未知奇怪料"),
    ])
    db.flush()
    res = mcs.auto_categorize(db, apply=True)
    c = _cats(db)
    assert c["AC-1"] == "岩板"
    assert c["AC-2"] == "洞石饰面板"
    assert c["AC-3"] == "电力轨道"
    assert c["AC-4"] == "玻璃"
    assert c["AC-5"] == "五金"
    assert c["AC-6"] == "床铺板"
    assert c["AC-7"] == "五金"        # 五金规则先于玻璃
    assert c["WD-1"] == "木作"
    assert c["MP-1"] == "人工"
    assert c["AC-9"] is None          # 未匹配 → 留空(未分类)
    assert res["uncategorized"] >= 1


def test_auto_categorize_only_empty_keeps_manual(db_session):
    db = db_session
    db.add(Material(code="AC-1", name="12mm黑色岩板", category="我手动设的"))
    db.flush()
    mcs.auto_categorize(db, apply=True, only_empty=True)
    assert _cats(db)["AC-1"] == "我手动设的"   # 不覆盖人工


def test_ensure_consumables_creates_and_adds_to_bom(db_session):
    db = db_session
    db.add(BomLine(product_code="P1", sku_code="S1", material_code="AC-X", qty_per_product=Decimal("1")))
    db.add(BomLine(product_code="P2", sku_code="S2", material_code="AC-Y", qty_per_product=Decimal("1")))
    db.flush()
    res = mcs.ensure_consumables_in_boms(db, apply=True)
    names = {m.name for m in db.execute(select(Material)).scalars().all()}
    assert "双面胶" in names and "螺丝" in names
    tape = db.execute(select(Material).where(Material.name == "双面胶")).scalar_one()
    assert tape.category == "五金" and float(tape.price) == 0.1 and tape.unit == "个"
    assert res["bom_anchors"] == 2 and res["bom_lines_added"] == 4   # 2 锚点 × 2 消耗料
    res2 = mcs.ensure_consumables_in_boms(db, apply=True)
    assert res2["bom_lines_added"] == 0   # 幂等


def test_consumables_dryrun_no_write(db_session):
    db = db_session
    db.add(BomLine(product_code="P1", sku_code="S1", material_code="AC-X", qty_per_product=Decimal("1")))
    db.flush()
    res = mcs.ensure_consumables_in_boms(db, apply=False)
    assert res["applied"] is False and res["bom_lines_added"] == 2   # 预览数, 未落库
    assert db.execute(select(Material).where(Material.name == "双面胶")).scalar_one_or_none() is None
