# -*- coding: utf-8 -*-
"""配件 epic 阶段2/3/4 (用户 2026-06-28): 平台订单号归账 + 多单 BOM 占比分摊 +
逐类覆盖 actual_parts + 供应商级零星/月结(月结导出扣零星 + 红字 + 双算自检)。
"""
from datetime import date
from decimal import Decimal

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order, PartPurchase
from app.models.supplier import Supplier
from app.services import alipay_flow_router_service as router
from app.services import parts_recon_service as prs
from app.models.finance import AlipayFlow


def _mat(db, code, name, price, category):
    db.add(Material(code=code, name=name, price=Decimal(str(price)), category=category, unit="块"))


def _bom(db, pc, sc, mcode, mname, qty=1, remark=None):
    db.add(BomLine(product_code=pc, sku_code=sc, material_code=mcode, material_name=mname,
                   qty_per_product=Decimal(str(qty)), remark=remark))


def _order(db, no, pc, sc, ship=date(2026, 2, 10), qty=1):
    o = Order(platform="淘宝", order_no=no, product_code=pc, sku_code=sc, sku="x",
              product_name="柜", qty=qty, order_date=date(2026, 1, 1), ship_date=ship,
              status="signed", paid_amount=Decimal("5000"))
    db.add(o)
    return o


# ── 阶段2: 采购挂淘宝平台订单号(非支付宝商户号) ──────────────────────────────
def test_router_uses_platform_order_no(db_session):
    db = db_session
    db.add(AlipayFlow(account="爱群号", transaction_no="F1", transaction_type="支出",
                      counterparty="张三", amount=Decimal("-200"),
                      related_order_no="2026MERCHANT00000001",      # 支付宝商户号(对不上订单)
                      platform_order_no="4502255436167008824",      # 淘宝平台订单号
                      remark="岩板采购", reconciliation_status="open"))
    db.flush()
    n = router.create_purchases_from_unclassified(db)
    assert n == 1
    p = db.query(PartPurchase).one()
    assert p.related_order_no == "4502255436167008824"   # 用平台订单号, 非商户号


# ── 阶段3: 多单按 BOM 面积占比分摊 ───────────────────────────────────────────
def test_multi_order_area_proportion(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _bom(db, "PPSA", "SA", "AC-RB", "岩板", 1, "2000*1000")   # 面积 2,000,000
    _bom(db, "PPSB", "SB", "AC-RB", "岩板", 1, "1000*1000")   # 面积 1,000,000
    o2 = _order(db, "O2", "PPSA", "SA")
    o3 = _order(db, "O3", "PPSB", "SB")
    db.add(PartPurchase(purchase_no="MP1", supplier="山东张", material_code="AC-RB",
                        material_name="岩板", amount=Decimal("900"),
                        related_order_no="O2\nO3", purchase_date=date(2026, 2, 12)))
    db.flush()
    res = prs.aggregate_related_purchases(db, apply=True)
    db.refresh(o2); db.refresh(o3)
    assert o2.actual_parts == Decimal("600.00")   # 900 × 2/3 (面积占比)
    assert o3.actual_parts == Decimal("300.00")   # 900 × 1/3
    assert res["applied_count"] == 2


# ── 阶段3: 逐类覆盖 — 只覆盖对应零星配件预估, 其它类保留预估 ─────────────────
def test_category_aware_actual_parts(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _mat(db, "AC-DD", "洞洞板", 50, "杂项")
    _bom(db, "PPSX", "SX", "AC-RB", "岩板", 1, "1000*500")
    _bom(db, "PPSX", "SX", "AC-DD", "洞洞板", 2, "200*200")   # 杂项预估 = 2×50 = 100
    o1 = _order(db, "O1", "PPSX", "SX")
    db.add(PartPurchase(purchase_no="SP1", supplier="山东张", material_code="AC-RB",
                        material_name="岩板", amount=Decimal("200"),
                        related_order_no="O1", purchase_date=date(2026, 2, 12)))
    db.flush()
    prs.aggregate_related_purchases(db, apply=True)
    db.refresh(o1)
    # 岩板预估90→真实200(覆盖), 杂项保留预估100 → 300
    assert o1.actual_parts == Decimal("300.00")


# ── 阶段4: 月结类里零星采购从月结预估扣除 + 双算自检 ──────────────────────────
def test_sporadic_excluded_from_monthly_std(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")     # 岩板=月结类
    _bom(db, "PPSY", "SY", "AC-RB", "岩板", 1, "1000*1000")
    _order(db, "Y1", "PPSY", "SY")            # 走零星(支付宝现付)
    _order(db, "Y2", "PPSY", "SY")            # 正常月结
    db.add(PartPurchase(purchase_no="YP1", supplier="山东张", material_code="AC-RB",
                        material_name="岩板", amount=Decimal("150"),
                        related_order_no="Y1", purchase_date=date(2026, 2, 12)))
    db.flush()
    yan = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "岩板")
    p = {r["period"]: r for r in yan["periods"]}["2026-02"]
    assert p["standard_consume"] == 90.0       # 只 Y2 计入月结预估(Y1 零星已扣)
    assert p["order_count"] == 1               # Y1 不计月结单数
    assert p["sporadic_excluded"] == 150.0     # Y1 零星金额单列
    overlap = prs.detect_sporadic_monthly_overlap(db)
    assert any(x["order_no"] == "Y1" and x["category"] == "岩板" for x in overlap)


# ── 阶段3: 月结导出逐单红字提示零星 ──────────────────────────────────────────
def test_export_marks_sporadic_red_note(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _bom(db, "PPSZ", "SZ", "AC-RB", "岩板", 1, "1000*1000")
    _order(db, "Z1", "PPSZ", "SZ")
    db.add(PartPurchase(purchase_no="ZP1", supplier="山东张", material_code="AC-RB",
                        material_name="岩板", amount=Decimal("150"), alipay_flow_no="FLOWX",
                        related_order_no="Z1", purchase_date=date(2026, 2, 12)))
    db.flush()
    res = prs.export_shipped_orders(db, year_month="2026-02", material_key="岩板")
    row = next(o for o in res["orders"] if o["order_no"] == "Z1")
    assert row["sporadic"] is True
    assert "零星" in row["sporadic_note"] and "FLOWX" in row["sporadic_note"]


# ── 阶段4: 月结供应商的采购不算"零星覆盖"(不扣、不标红) ───────────────────────
def test_monthly_supplier_not_treated_as_sporadic(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _bom(db, "PPSM", "SM", "AC-RB", "岩板", 1, "1000*1000")
    _order(db, "M1", "PPSM", "SM")
    db.add(Supplier(name="岩板厂", supplier_type="other", payment_terms="月结"))
    db.add(PartPurchase(purchase_no="MMP1", supplier="岩板厂", material_code="AC-RB",
                        material_name="岩板", amount=Decimal("150"),
                        related_order_no="M1", purchase_date=date(2026, 2, 12)))
    db.flush()
    # 月结供应商 → 不计入零星覆盖 → 月结预估照常含 M1, 无红字
    yan = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "岩板")
    p = {r["period"]: r for r in yan["periods"]}["2026-02"]
    assert p["standard_consume"] == 90.0 and p["sporadic_excluded"] == 0.0
    res = prs.export_shipped_orders(db, year_month="2026-02", material_key="岩板")
    assert res["orders"][0]["sporadic"] is False
    assert prs.detect_sporadic_monthly_overlap(db) == []


# ── 用户口径 2026-06-29: 自购木材(MW)当零星记入; 纯工厂木作(WD)/人工(MP)仍排除 ──
def test_self_purchased_wood_recorded_factory_service_excluded(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _mat(db, "MW-OAK", "榉木大板", 0, "木材")     # 自购木材 → 记入(标 not_in_bom)
    _mat(db, "WD-LAB", "木作加工", 0, "木作")     # 纯工厂木作 → 排除
    _bom(db, "PPSR", "SR", "AC-RB", "岩板", 1, "1000*1000")
    o = _order(db, "R1", "PPSR", "SR")
    o2 = _order(db, "R2", "PPSR", "SR")
    db.add(PartPurchase(purchase_no="WD1", supplier="木材厂", material_code="MW-OAK",
                        material_name="榉木大板", amount=Decimal("800"),
                        related_order_no="R1", purchase_date=date(2026, 2, 12)))
    db.add(PartPurchase(purchase_no="LAB1", supplier="某工厂", material_code="WD-LAB",
                        material_name="木作加工", amount=Decimal("500"),
                        related_order_no="R2", purchase_date=date(2026, 2, 12)))
    db.flush()
    r = prs.aggregate_related_purchases(db, apply=True)
    db.refresh(o); db.refresh(o2)
    # 自购榉木记入产品成本: R1 = 岩板est90 + 木材real800 = 890, 木材不在BOM→标记待核
    assert o.actual_parts == Decimal("890.00")
    item = next(i for i in r["items"] if i["order_no"] == "R1")
    assert item["not_in_bom_categories"] == 1
    # 纯工厂木作(WD)被排除: R2 不记 actual_parts
    assert o2.actual_parts is None


# ── 复核 must-fix 2: 料号命中 BOM → 真实覆盖对应预估(不叠加双算) ──────────────
def test_material_code_anchors_to_bom_no_double(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _bom(db, "PPSN", "SN", "AC-RB", "岩板", 1, "1000*1000")
    o = _order(db, "N1", "PPSN", "SN")
    db.add(PartPurchase(purchase_no="NP1", supplier="山东张", material_code="AC-RB",
                        material_name="岩板", amount=Decimal("200"),
                        related_order_no="N1", purchase_date=date(2026, 2, 12)))
    db.flush()
    r = prs.aggregate_related_purchases(db, apply=True)
    db.refresh(o)
    assert o.actual_parts == Decimal("200.00")          # 覆盖 est90, 不是 290(双算)
    assert r["flagged_not_in_bom_orders"] == 0          # 料号命中 BOM, 不标待核对


def test_purchase_category_not_in_bom_flagged(db_session):
    """采购的料不在该单 BOM → 作额外项加入 + 标 not_in_bom 待人工核对(不静默)。"""
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _mat(db, "AC-FM", "洞石饰面板", 60, "洞石饰面板")
    _bom(db, "PPSF", "SF", "AC-RB", "岩板", 1, "1000*1000")   # BOM 只有岩板
    o = _order(db, "F1", "PPSF", "SF")
    db.add(PartPurchase(purchase_no="FP1", supplier="山东张", material_code="AC-FM",
                        material_name="洞石饰面板", amount=Decimal("200"),
                        related_order_no="F1", purchase_date=date(2026, 2, 12)))
    db.flush()
    r = prs.aggregate_related_purchases(db, apply=True)
    item = next(i for i in r["items"] if i["order_no"] == "F1")
    assert item["not_in_bom_categories"] == 1
    assert r["flagged_not_in_bom_orders"] == 1
    fm = next(c for c in item["categories"] if c["category"] == "洞石饰面板")
    assert fm["not_in_bom"] is True


# ── 用户 2026-06-29 抽查纠错: 取消单/定金片段单不覆盖成本; 以 est_parts 为基线不虚高 ──────
def test_aggregate_skips_cancelled_and_capped_orders(db_session):
    db = db_session
    _mat(db, "AC-RB", "岩板", 90, "岩板")
    _bom(db, "PPSK", "SK", "AC-RB", "岩板", 1, "1000*1000")
    c = _order(db, "K1", "PPSK", "SK"); c.status = "cancelled"          # 取消单
    fr = _order(db, "K2", "PPSK", "SK")                                 # 定金/片段单(实付<<理论成本→封顶)
    fr.paid_amount = Decimal("2000"); fr.theoretical_cost = Decimal("10000")
    ok = _order(db, "K3", "PPSK", "SK")                                 # 正常成交单(未封顶)→ 应覆盖
    for no in ("K1", "K2", "K3"):
        db.add(PartPurchase(purchase_no="KP_" + no, supplier="山东张", material_code="AC-RB",
                            material_name="岩板", amount=Decimal("300"),
                            related_order_no=no, purchase_date=date(2026, 2, 12)))
    db.flush()
    r = prs.aggregate_related_purchases(db, apply=True)
    db.refresh(c); db.refresh(fr); db.refresh(ok)
    assert c.actual_parts is None and fr.actual_parts is None   # 取消单/片段单不覆盖
    assert ok.actual_parts == Decimal("300.00")                # 正常单覆盖(base=BOM90, real300覆盖→300)
    assert r["skipped_orders"] == 2


def test_est_parts_base_no_balloon(db_session):
    """覆盖以 est_parts 为基线: 真实采购的料不在BOM(额外项)→ est_parts + real, 不用 BOM 全类汇总致虚高。"""
    db = db_session
    _mat(db, "AC-FM", "洞石饰面板", 60, "洞石饰面板")
    _bom(db, "PPSE", "SE", "AC-FM", "洞石饰面板", 1, "1000*1000")
    o = _order(db, "E1", "PPSE", "SE")
    o.est_parts = Decimal("500")          # 该单原配件预估(定价口径)
    db.add(PartPurchase(purchase_no="EP1", supplier="山东张", material_code="AC-X未知",
                        material_name="未知件", amount=Decimal("200"),
                        related_order_no="E1", purchase_date=date(2026, 2, 12)))
    db.flush()
    prs.aggregate_related_purchases(db, apply=True)
    db.refresh(o)
    assert o.actual_parts == Decimal("700.00")   # est_parts 500 + 不在BOM的真实 200 = 700(非 BOM 汇总)


# ── 复核 must-fix 3: 月结部分覆盖按金额净额扣除(不丢整类) ─────────────────────
def test_monthly_partial_cover_nets_not_whole(db_session):
    db = db_session
    _mat(db, "AC-A", "岩板铰件", 100, "岩板")
    _mat(db, "AC-B", "岩板拉手", 80, "岩板")
    _bom(db, "PPSP", "SP", "AC-A", "岩板铰件", 1, "100*100")   # 岩板 est = 100 + 80 = 180
    _bom(db, "PPSP", "SP", "AC-B", "岩板拉手", 1, "100*100")
    _order(db, "P1", "PPSP", "SP")
    db.add(PartPurchase(purchase_no="PP1", supplier="山东张", material_code="AC-A",
                        material_name="岩板铰件", amount=Decimal("80"),   # 只现付了其中一部分
                        related_order_no="P1", purchase_date=date(2026, 2, 12)))
    db.flush()
    yan = next(m for m in prs.bulk_material_recon(db)["materials"] if m["key"] == "岩板")
    p = {r["period"]: r for r in yan["periods"]}["2026-02"]
    assert p["standard_consume"] == 100.0      # 180 − 80 现付 = 100 剩余仍进月结(不丢整 180)
    assert p["sporadic_excluded"] == 80.0
    assert p["order_count"] == 1               # 仍有月结剩余 → 该单计入
