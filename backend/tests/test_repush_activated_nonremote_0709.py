"""repush_activated 现覆盖普通单(非远期): 备注读到"开始制作"就重推一次, 幂等 (用户 2026-07-09)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.services import order_sheet_archive_service as osa


def _sheet(no, **rs):
    return ImportedFile(kind="order_sheet", stored_path=f"/x/{no}.jpg",
                        original_filename=f"2026-06-16_{no}.jpg", source="auto",
                        row_summary={"pushed": True, **rs})


def _order(no, memo, fno):
    return Order(order_no=no, platform="淘宝", status="paid", seller_memo=memo,
                 is_remote_ship=False, factory_no=fno, order_date=date(2026, 6, 16),
                 paid_amount=Decimal("100"), refund_amount=Decimal("0"), is_refill=False)


def _fno(db, no):
    o = db.execute(select(Order).where(Order.order_no == no)).scalar_one()
    return o.factory_no


def test_nonremote_activated_gets_repushed(db_session):
    db_session.add(_order("RA-1", "开始制作", 999))
    db_session.add(_sheet("RA-1"))                  # 推过、非激活态
    db_session.flush()
    r = osa.repush_activated(db_session)
    assert "RA-1" in r["reset_for_new_no"]           # 非远期单也命中(原来会被跳过)
    assert _fno(db_session, "RA-1") is None           # 清号 → 待顺排新号重推


def test_already_activated_marker_is_idempotent(db_session):
    db_session.add(_order("RA-2", "开始制作", 290))
    db_session.add(_sheet("RA-2", activated=True))    # 已按激活态推过
    db_session.flush()
    r = osa.repush_activated(db_session)
    assert "RA-2" not in r["reset_for_new_no"]         # 幂等: 不再重推
    assert _fno(db_session, "RA-2") == 290


def test_not_activated_untouched(db_session):
    db_session.add(_order("RA-3", "等通知装修好再发", 291))   # 远期词, 未激活
    db_session.add(_sheet("RA-3"))
    db_session.flush()
    r = osa.repush_activated(db_session)
    assert "RA-3" not in r["reset_for_new_no"]         # 没激活 → 不动
    assert _fno(db_session, "RA-3") == 291
