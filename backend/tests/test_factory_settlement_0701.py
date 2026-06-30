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
