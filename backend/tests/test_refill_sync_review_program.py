from datetime import date

from sqlalchemy import func, select

from app.api.refill_sync import ReviewOrderTrackIn, sync_review_order_tracks
from app.models.finance import RefillRecord
from app.models.order import Order


def test_review_order_track_sync_is_persistent_and_idempotent(db_session):
    order = Order(
        platform="淘宝",
        order_no="3310000000000000001",
        order_date=date(2026, 7, 21),
        customer_name="测试买家",
        product_code="PPS001",
        product_name="测试餐桌",
        sku="原木色",
        qty=1,
        paid_amount=100,
        status="paid",
        is_refill=False,
    )
    db_session.add(order)
    db_session.commit()

    item = ReviewOrderTrackIn(
        order_no=" 3310000000000000001 ",
        product_name="评价系统产品名",
        placed_date=date(2026, 7, 20),
    )
    first = sync_review_order_tracks(db_session, [item])
    assert first["created"] == 1
    assert first["flagged"] == 1
    assert first["missing_orders"] == []
    db_session.refresh(order)
    assert order.is_refill is True

    record = db_session.scalar(select(RefillRecord).where(RefillRecord.order_no == order.order_no))
    assert record is not None
    assert record.refill_date == date(2026, 7, 20)
    assert record.product_name == "评价系统产品名"
    # RefillRecord 的既有事件钩子会把 sync_key 统一改成财务侧稳定业务键。
    assert record.sync_key and record.sync_key.startswith(f"refill:{order.order_no}:")

    second = sync_review_order_tracks(db_session, [item])
    assert second["created"] == 0
    assert second["flagged"] == 0
    assert second["already_marked"] == 1
    assert db_session.scalar(select(func.count()).select_from(RefillRecord)) == 1


def test_review_order_track_sync_keeps_record_until_order_arrives(db_session):
    result = sync_review_order_tracks(
        db_session,
        [ReviewOrderTrackIn(order_no="3310000000000000002", placed_date=date(2026, 7, 21))],
    )
    assert result["created"] == 1
    assert result["flagged"] == 0
    assert result["missing_orders"] == ["3310000000000000002"]
    assert db_session.scalar(select(func.count()).select_from(RefillRecord)) == 1
