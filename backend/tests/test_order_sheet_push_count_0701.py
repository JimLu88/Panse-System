"""待推飞书角标计数对齐 (2026-07-01): count_pending_push 只数『真能推的』,
排除订单已删/取消/退款 — 修复资料存档库「待推 49」推不掉却一直显示的遗留 bug。"""
from datetime import date
from decimal import Decimal

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.services import order_sheet_archive_service as svc


def _sheet(db, order_no, pushed=False):
    f = ImportedFile(
        kind="order_sheet", original_filename=f"下单图_{order_no}.html",
        stored_path="x", row_summary=({"pushed": True} if pushed else {}),
    )
    db.add(f)
    db.commit()
    return f


def _order(db, order_no, **kw):
    o = Order(
        order_no=order_no, platform="淘宝", status=kw.pop("status", "signed"),
        order_date=kw.pop("order_date", date(2026, 6, 25)),
        paid_amount=Decimal(str(kw.pop("paid", 1000))),
        refund_amount=Decimal(str(kw.pop("refund", 0))),
        factory_no=kw.pop("factory_no", 300),
        is_refill=False, is_historical=False, qty=1,
    )
    for k, v in kw.items():
        setattr(o, k, v)
    db.add(o)
    db.commit()
    return o


def test_count_excludes_missing_order(db_session):
    _sheet(db_session, "GHOST")                                   # 归档图在, 订单不存在
    assert svc.count_pending_push(db_session) == 0


def test_count_excludes_cancelled_and_refunded(db_session):
    _sheet(db_session, "C1"); _order(db_session, "C1", status="cancelled")
    _sheet(db_session, "R1"); _order(db_session, "R1", paid=1000, refund=1000)  # 全额退
    assert svc.count_pending_push(db_session) == 0


def test_count_counts_real_pending(db_session):
    _sheet(db_session, "OK1"); _order(db_session, "OK1", status="signed")
    assert svc.count_pending_push(db_session) == 1


def test_count_ignores_already_pushed(db_session):
    _sheet(db_session, "P1", pushed=True); _order(db_session, "P1", status="signed")
    assert svc.count_pending_push(db_session) == 0


def test_count_excludes_old_sheet_without_factory_no(db_session):
    # 6月无工厂编号的历史单: 推出去是红字"未能匹配"噪音 → 不算待推 (用户看到的「待推45」正是这种)
    _sheet(db_session, "OLD1")
    _order(db_session, "OLD1", status="signed", factory_no=None, order_date=date(2026, 6, 10))
    assert svc.count_pending_push(db_session) == 0


def test_count_includes_new_order_pending_number(db_session):
    # 6/19 起的新单暂无编号: 推送时会自动顺排编号 → 算可推
    _sheet(db_session, "NEW1")
    _order(db_session, "NEW1", status="signed", factory_no=None, order_date=date(2026, 6, 25))
    assert svc.count_pending_push(db_session) == 1


def test_stale_49_scenario_drops_to_real(db_session):
    # 模拟「待推 49」实为遗留: 40 张订单已删 + 5 张取消 + 4 张真待推 → 只应数 4
    for i in range(40):
        _sheet(db_session, f"DEL{i}")                             # 无对应订单
    for i in range(5):
        _sheet(db_session, f"CAN{i}"); _order(db_session, f"CAN{i}", status="cancelled")
    for i in range(4):
        _sheet(db_session, f"LIVE{i}"); _order(db_session, f"LIVE{i}", status="signed")
    assert svc.count_pending_push(db_session) == 4
