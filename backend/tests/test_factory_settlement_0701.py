"""木作工厂月结销账 (用户 2026-07-01): 月度欠款台账 + 声明驱动销账/撤销 + 别名 + 备注解析。"""
from datetime import date
from decimal import Decimal

from app.models.factory_settlement import DEFAULT_WOOD_SUPPLIER as SUP
from app.models.order import FactoryOrder
from app.services import factory_settlement_service as fss


def _fo(db, no, *, month=None, order_date=None, bill="1000", status="unpaid", factory=SUP):
    fo = FactoryOrder(factory_order_no=no, factory_name=factory,
                      factory_bill_amount=Decimal(str(bill)), payment_status=status,
                      settlement_month=month, order_date=order_date)
    db.add(fo)
    db.flush()
    return fo


def test_breakdown_groups_by_month(db_session):
    db = db_session
    _fo(db, "A1", order_date=date(2026, 5, 3), bill="38490")
    _fo(db, "A2", order_date=date(2026, 1, 3), bill="28410")
    bd = fss.month_breakdown(db)
    by = {m["month"]: m for m in bd["months"]}
    assert by["2026-05"]["unpaid"] == Decimal("38490.00")
    assert by["2026-01"]["unpaid"] == Decimal("28410.00")
    assert bd["total_unpaid"] == Decimal("66900.00")


def test_settle_and_reverse(db_session):
    db = db_session
    _fo(db, "M1", order_date=date(2026, 5, 3), bill="20000")
    _fo(db, "M2", order_date=date(2026, 5, 9), bill="18490")
    _fo(db, "X1", order_date=date(2026, 4, 1), bill="5000")   # 4月, 不该被5月销账动
    r = fss.settle_month(db, month="2026-05", trigger="manual")
    assert r["flipped"] == 2
    pid = r["payment_id"]
    by = {m["month"]: m for m in fss.month_breakdown(db)["months"]}
    assert by["2026-05"]["status"] == "paid" and by["2026-05"]["unpaid"] == Decimal("0.00")
    assert by["2026-04"]["unpaid"] == Decimal("5000.00")      # 4月不动
    assert fss.settle_month(db, month="2026-05")["flipped"] == 0   # 幂等
    rv = fss.reverse_settlement(db, pid)
    assert rv["reverted"] == 2
    by2 = {m["month"]: m for m in fss.month_breakdown(db)["months"]}
    assert by2["2026-05"]["unpaid"] == Decimal("38490.00")    # 撤销后恢复未付


def test_settlement_month_overrides_order_date(db_session):
    """工厂账单说5月(settlement_month) 覆盖 4月下单(order_date)。"""
    db = db_session
    _fo(db, "S1", order_date=date(2026, 4, 28), month="2026-05", bill="3000")
    by = {m["month"]: m for m in fss.month_breakdown(db)["months"]}
    assert by.get("2026-05", {}).get("unpaid") == Decimal("3000.00")
    assert "2026-04" not in by


def test_alias_match_masked(db_session):
    db = db_session
    fss.seed_default_aliases(db)
    assert fss.match_supplier(db, "**男") == SUP       # 打码 → 伟男
    assert fss.match_supplier(db, "程卫燕") == SUP
    assert fss.match_supplier(db, "无关路人甲") is None


def test_parse_remark_negative_first():
    assert fss.parse_settlement_remark("5月货款还没付清", year=2026)["action"] == "unsettle"
    r = fss.parse_settlement_remark("5月已付清", year=2026)
    assert r["action"] == "settle" and r["months"] == ["2026-05"]
    r2 = fss.parse_settlement_remark("四月已结清", year=2026)
    assert r2["action"] == "settle" and r2["months"] == ["2026-04"]
    assert fss.parse_settlement_remark("货款", year=2026)["action"] is None


def test_parse_remark_expanded_keywords():
    """扩充关键词(用户 2026-07-02): '货款…结算/已付款' 也销账; 杂费不销; 守住旧行为。"""
    # 真实备注 '挚乐1月货款2025结算' → 结算 → 销 1 月
    r = fss.parse_settlement_remark("挚乐1月货款2025结算", year=2026)
    assert r["action"] == "settle" and "2026-01" in r["months"]
    assert fss.parse_settlement_remark("3月货款结算", year=2026)["action"] == "settle"
    assert fss.parse_settlement_remark("5月货款已结算", year=2026)["action"] == "settle"
    assert fss.parse_settlement_remark("4月货款已付款", year=2026)["action"] == "settle"
    # 杂费: 打包费/运费/配件采购/玻璃费/样品定金 → 不销货款账(fee)
    for fee in ("挚乐2月打包费", "挚乐运费", "挚乐轨道灯带配件采购12-2月",
                "2025年玻璃费用-挚乐", "榉木材料运费+叉车", "25年床样品货款定金"):
        assert fss.parse_settlement_remark(fee, year=2026)["action"] != "settle"
    # 否定优先(新增否定词'没结算'也拦得住)
    assert fss.parse_settlement_remark("5月货款还没结算", year=2026)["action"] == "unsettle"
    # 单独'货款'仍不销(可能只是部分付款)
    assert fss.parse_settlement_remark("江西货款", year=2026)["action"] is None


def test_route_alipay_keyword_autosettle(db_session):
    """P2: 货款出账(别名识别)→纠正归类 factory_payment; 备注「5月已付清」自动销账; 否定不销; 幂等。"""
    from datetime import datetime, timezone

    from sqlalchemy import select
    from app.models.finance import AlipayFlow

    db = db_session
    fss.seed_default_aliases(db)
    _fo(db, "K1", order_date=date(2026, 5, 3), bill="20000")
    _fo(db, "K2", order_date=date(2026, 5, 9), bill="18490")
    _fo(db, "K3", order_date=date(2026, 4, 1), bill="5000")
    db.add(AlipayFlow(account="支付宝-企业账号", transaction_no="TXN-MAY", amount=Decimal("-38490"),
                      counterparty="**男", remark="5月货款已付清", reconciliation_type="customer_payment",
                      transaction_time=datetime(2026, 6, 1, tzinfo=timezone.utc)))
    db.add(AlipayFlow(account="支付宝-企业账号", transaction_no="TXN-APR", amount=Decimal("-5000"),
                      counterparty="**男", remark="4月货款还没付清", reconciliation_type="customer_payment",
                      transaction_time=datetime(2026, 6, 1, tzinfo=timezone.utc)))
    db.flush()

    r = fss.route_alipay_settlements(db)
    assert r["flipped"] == 2 and "2026-05" in r["settled_months"]
    flows = {f.transaction_no: f for f in db.execute(select(AlipayFlow)).scalars().all()}
    assert flows["TXN-MAY"].reconciliation_type == "factory_payment"   # 纠正货款误归类
    assert flows["TXN-APR"].reconciliation_type == "factory_payment"   # 否定单也纠正归类(但不销账)
    by = {m["month"]: m for m in fss.month_breakdown(db)["months"]}
    assert by["2026-05"]["unpaid"] == Decimal("0.00")                  # 5月已销
    assert by["2026-04"]["unpaid"] == Decimal("5000.00")              # 4月否定→未销
    assert fss.route_alipay_settlements(db)["flipped"] == 0           # 幂等


def test_p3_exception_open_and_selfheal(db_session):
    """P3: 未付清月开异常; 销账后 recheck 自动销账(自愈)。"""
    from sqlalchemy import select
    from app.models.exception import DataException
    from app.services import exception_recheck_service, scanner_service

    db = db_session
    _fo(db, "E1", order_date=date(2026, 5, 3), bill="38490")
    db.flush()
    res = scanner_service.run_scanner(db, "factory_bill_unpaid")
    assert res.written == 1
    exc = db.execute(
        select(DataException).where(DataException.exception_type == "factory_bill_unpaid")
    ).scalar_one()
    assert exc.status == "open" and "2026-05" in exc.source_pk
    assert exception_recheck_service.recheck(db, exc) is not None     # 未销 → 仍开
    fss.settle_month(db, month="2026-05")
    assert exception_recheck_service.recheck(db, exc) is None         # 已销 → 自愈销账


def test_product_search_filters(db_session):
    """q 模糊搜索(用户 2026-07-03): 台账/逐单明细只含 产品名/SKU/产品编码 匹配的工厂单。"""
    db = db_session
    db.add_all([
        FactoryOrder(factory_order_no="S1", factory_name=SUP, factory_bill_amount=Decimal("1000"),
                     payment_status="unpaid", order_date=date(2026, 5, 3),
                     product_name="榉木岩板餐桌", sku="榉木-1.8米", product_code="PPS24210070901"),
        FactoryOrder(factory_order_no="S2", factory_name=SUP, factory_bill_amount=Decimal("2000"),
                     payment_status="unpaid", order_date=date(2026, 5, 4),
                     product_name="樱桃木窄柜", sku="樱桃-窄柜", product_code="PPS99"),
    ])
    db.flush()
    assert fss.month_breakdown(db, q="岩板")["total_billed"] == Decimal("1000.00")      # 产品名
    assert fss.month_breakdown(db, q="窄柜")["total_billed"] == Decimal("2000.00")       # SKU
    assert fss.month_breakdown(db, q="PPS24210070901")["total_billed"] == Decimal("1000.00")  # 编码
    assert fss.month_breakdown(db)["total_billed"] == Decimal("3000.00")                 # 空q=全部
    detail = fss.settlement_detail_rows(db, q="岩板")
    assert len(detail) == 1 and detail[0]["factory_order_no"] == "S1"
    assert detail[0]["product_code"] == "PPS24210070901"
    assert len(fss.settlement_detail_rows(db)) == 2                                       # 空q=全部


def test_p4_missing_orders(db_session):
    """P4: 已发货未被任何工厂账单覆盖=漏单; 剔除已覆盖/样块/未来月; 按发货月累计。"""
    from app.models.order import FactoryOrder, Order

    db = db_session
    db.add_all([
        Order(platform="淘宝", order_no="MO1", status="shipped", paid_amount=Decimal("999"),
              ship_date=date(2026, 5, 10), product_name="餐桌"),               # 漏单
        Order(platform="淘宝", order_no="MO2", status="shipped", paid_amount=Decimal("888"),
              ship_date=date(2026, 5, 11), product_name="书桌"),               # 有账单 → 不漏
        Order(platform="淘宝", order_no="MO3", status="shipped", paid_amount=Decimal("16"),
              ship_date=date(2026, 5, 12), product_name="榉木样块"),           # 样块 → 排除
        Order(platform="淘宝", order_no="MO4", status="shipped", paid_amount=Decimal("500"),
              ship_date=date(2026, 6, 1), product_name="柜子"),                # 6月发货 → up_to=5月不计
    ])
    db.add(FactoryOrder(factory_order_no="FBMO2", factory_name=SUP, platform_order_no="MO2",
                        factory_bill_amount=Decimal("600"), payment_status="unpaid"))
    db.flush()

    r = fss.missing_orders(db, up_to_month="2026-05")
    assert {x["order_no"] for x in r["orders"]} == {"MO1"}
    assert r["count"] == 1
    assert fss.missing_orders_xlsx_bytes(db, up_to_month="2026-05")[:2] == b"PK"   # xlsx=zip
