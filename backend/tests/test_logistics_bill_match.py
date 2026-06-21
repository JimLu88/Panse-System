"""物流费账单逐单行 → 淘宝订单 自动配对测试 (用户 2026-06-21)。

覆盖: 运单号命中 / 姓名+省市命中 / 同名异地→未匹配 / 关闭单不参与 / summary行不配 / 多候选。
"""
from datetime import date
from decimal import Decimal

from app.models.finance import LogisticsBill
from app.models.order import Order
from app.services import logistics_bill_match


def _order(db, no, name, addr, *, status="signed", track=None):
    db.add(Order(
        platform="淘宝", order_no=no, qty=1, status=status,
        order_date=date(2026, 6, 1), paid_amount=Decimal("100"),
        customer_name=name, customer_address=addr, tracking_no=track,
    ))
    db.flush()


def _bill(db, **kw):
    kw.setdefault("freight_amount", Decimal("30"))
    kw.setdefault("row_type", "line")
    b = LogisticsBill(bill_date=date(2026, 6, 10), carrier="德邦", **kw)
    db.add(b)
    db.flush()
    return b


def test_match_by_tracking_no(db_session):
    """运单号全等 → 最可靠, 直接命中."""
    _order(db_session, "O1", "张三", "广东省深圳市南山区", track="DB123")
    b = _bill(db_session, tracking_no="DB123", recipient_name="张三", destination="广东省深圳市")
    r = logistics_bill_match.match_logistics_bills(db_session)
    assert r["matched"] == 1
    assert b.order_no == "O1"
    assert b.match_method == "track"


def test_match_by_name_and_province(db_session):
    """无运单号命中, 但收货人同名 + 目的地省市在订单地址里 → name_prov."""
    _order(db_session, "O2", "李四", "浙江省杭州市西湖区文一路")
    b = _bill(db_session, tracking_no="ZZZ", recipient_name="李四", destination="浙江省杭州市")
    r = logistics_bill_match.match_logistics_bills(db_session)
    assert r["matched"] == 1
    assert b.order_no == "O2"
    assert b.match_method == "name_prov"


def test_same_name_other_province_is_unmatched_when_ambiguous(db_session):
    """同名但有多个候选且目的地省市对不上 → multi 或 none, 不乱配."""
    _order(db_session, "O3a", "王五", "北京市朝阳区")
    _order(db_session, "O3b", "王五", "四川省成都市")
    b = _bill(db_session, tracking_no="NO", recipient_name="王五", destination="广东省广州市")
    logistics_bill_match.match_logistics_bills(db_session)
    assert b.order_no is None
    assert b.match_method in ("multi", "none")


def test_cancelled_order_not_matched(db_session):
    """关闭单按铁律排除: 收货人只在关闭单里 → 不命中, 标 none."""
    _order(db_session, "O4", "赵六", "江苏省南京市", status="cancelled", track="DBX")
    b = _bill(db_session, tracking_no="DBX", recipient_name="赵六", destination="江苏省南京市")
    r = logistics_bill_match.match_logistics_bills(db_session)
    assert b.order_no is None
    assert b.match_method == "none"
    assert r["none"] == 1


def test_summary_row_not_matched(db_session):
    """月结汇总行 (row_type='summary') 不参与配对."""
    _order(db_session, "O5", "钱七", "湖南省长沙市", track="DBS")
    s = LogisticsBill(bill_date=date(2026, 6, 30), carrier="德邦", row_type="summary",
                      freight_amount=Decimal("14540"), tracking_no=None)
    db_session.add(s)
    db_session.flush()
    logistics_bill_match.match_logistics_bills(db_session)
    assert s.match_method is None
    assert s.order_no is None


def test_manual_match_not_overwritten(db_session):
    """人工指定过的不重算 (only_unmatched 跳过 manual)."""
    _order(db_session, "O6", "孙八", "山东省青岛市", track="DB6")
    b = _bill(db_session, tracking_no="DB6", recipient_name="孙八",
              destination="山东省青岛市", order_no="MANUAL", match_method="manual")
    logistics_bill_match.match_logistics_bills(db_session)
    assert b.order_no == "MANUAL"
    assert b.match_method == "manual"
