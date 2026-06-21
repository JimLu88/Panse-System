"""打包费手写账单 入库+配单+剔除+核算 测试 (用户 2026-06-21, C)。

覆盖: 单号匹配 / 客户名唯一匹配 / 「改客户」自动剔除不计入 / 关闭单不参与配单 /
当月应付=Σ未剔除 / 重复行去重。
"""
from datetime import date
from decimal import Decimal

from app.models.finance import PackingBill
from app.models.order import Order
from app.services import packing_bill_service as svc


def _order(db, no, name, *, status="signed"):
    db.add(Order(platform="淘宝", order_no=no, qty=1, status=status,
                 order_date=date(2026, 6, 1), paid_amount=Decimal("100"),
                 customer_name=name))
    db.flush()


def test_match_by_order_no(db_session):
    _order(db_session, "PB1", "张三")
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "张三", "order_no": "PB1", "packing_fee": 15},
    ], bill_month="2026-06")
    assert r["inserted"] == 1 and r["matched"] == 1
    assert r["payable_total"] == 15.0


def test_match_by_unique_name(db_session):
    _order(db_session, "PB2", "李四")
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "李四", "packing_fee": 20},
    ], bill_month="2026-06")
    assert r["matched"] == 1
    pb = db_session.query(PackingBill).filter_by(customer_name="李四").first()
    assert pb.match_method == "name_unique" and pb.matched_order_no == "PB2"


def test_excluded_row_not_in_payable(db_session):
    """「改客户」批注 → excluded, 不计入应付总额 (用户 C②)。"""
    _order(db_session, "PB3", "王五")
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "王五", "packing_fee": 18},
        {"customer_name": "赵六", "packing_fee": 18, "note": "改客户"},
    ], bill_month="2026-06")
    assert r["inserted"] == 2
    assert r["excluded"] == 1
    assert r["payable_total"] == 18.0          # 只算王五, 改客户那行剔除
    assert r["excluded_total"] == 18.0


def test_explicit_excluded_flag(db_session):
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "钱七", "packing_fee": 12, "excluded": True, "exclude_reason": "作废"},
    ], bill_month="2026-06")
    assert r["excluded"] == 1 and r["payable_total"] == 0.0


def test_cancelled_order_not_matched(db_session):
    """关闭单按铁律排除: 同名只在关闭单 → 不配单, 标 none。"""
    _order(db_session, "PB4", "孙八", status="cancelled")
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "孙八", "packing_fee": 22},
    ], bill_month="2026-06")
    assert r["matched"] == 0
    pb = db_session.query(PackingBill).filter_by(customer_name="孙八").first()
    assert pb.match_method == "none" and pb.matched_order_no is None


def test_dedup_same_row(db_session):
    _order(db_session, "PB5", "周九")
    rows = [{"customer_name": "周九", "packing_fee": 10, "row_date": "2026-06-05"}]
    svc.commit_packing_parsed(db_session, rows, bill_month="2026-06")
    r2 = svc.commit_packing_parsed(db_session, rows, bill_month="2026-06")
    assert r2["inserted"] == 0 and r2["skipped"] == 1
    assert r2["payable_total"] == 10.0          # 不翻倍


def test_total_mismatch_raises_exception(db_session):
    """本子合计与系统应付对不上 → 挂异常 (用户 2026-06-21)。"""
    from app.models.exception import DataException
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "甲", "packing_fee": 100},
    ], bill_month="2026-07", declared_total=150)   # 系统应付100, 本子150 → 差50
    assert r["total_mismatch"] == 50.0
    exc = db_session.query(DataException).filter_by(
        exception_type="packing_total_mismatch").first()
    assert exc is not None and exc.source_pk == "2026-07"


def test_total_match_no_exception(db_session):
    """本子合计与系统应付相符 → 不挂异常。"""
    from app.models.exception import DataException
    r = svc.commit_packing_parsed(db_session, [
        {"customer_name": "乙", "packing_fee": 100},
    ], bill_month="2026-07", declared_total=100)
    assert r["total_mismatch"] is None
    assert db_session.query(DataException).filter_by(
        exception_type="packing_total_mismatch").count() == 0
