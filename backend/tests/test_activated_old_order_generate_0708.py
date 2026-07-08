"""已激活的老远期单(order_date < AUTO_SINCE 但备注开始制作)必须被补生成候选纳入 (2026-07-08)。
根因: generate_pending 原按 order_date>=2026-06-06 切, 远期单早下、激活时已老 → 被切掉永不进工厂。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.order import Order
from app.services import order_sheet_archive_service as osa


def test_old_activated_order_matches_filter(db_session):
    # 老单(5-21, 早于 AUTO_SINCE 6-6), 备注"开始制作" → 应命中激活粗筛
    db_session.add(Order(order_no="OLD-ACT", platform="淘宝", order_date=date(2026, 5, 21),
                         status="paid", seller_memo="开始制作", is_refill=False))
    # 老单但无激活词 → 不应命中(仍按日期线切掉, 不误扫历史)
    db_session.add(Order(order_no="OLD-PLAIN", platform="淘宝", order_date=date(2026, 5, 21),
                         status="paid", seller_memo="随便写点", is_refill=False))
    db_session.flush()
    hit = set(db_session.execute(
        select(Order.order_no).where(osa._activated_memo_filter())
    ).scalars().all())
    assert "OLD-ACT" in hit
    assert "OLD-PLAIN" not in hit


def test_negated_activate_word_not_matched_by_flags(db_session):
    # "先不开始制作" 含激活词但有否定前缀 → order_flags 精确判定不算激活 (循环内二次把关)
    from app.services import order_flags as of
    o = Order(order_no="OLD-NEG", platform="淘宝", order_date=date(2026, 5, 21),
              status="paid", seller_memo="先不开始制作", is_refill=False)
    assert of.is_activated(o) is False


def test_is_refunded_ignores_no_refund_status():
    """'没有申请退款' 含"退款"二字但实际没退, 不能判成退款作废 (2026-07-08 修子串误判)。"""
    from decimal import Decimal
    o = Order(order_no="NR-1", platform="淘宝", status="paid", refund_status="没有申请退款",
              refund_amount=Decimal("0"), paid_amount=Decimal("2703.16"))
    assert osa._is_refunded(o) is False          # 正常无退款单 → 不作废, 能进工厂
    o2 = Order(order_no="NR-2", platform="淘宝", status="paid", refund_status="退款成功",
               refund_amount=Decimal("0"), paid_amount=Decimal("100"))
    assert osa._is_refunded(o2) is True          # 真退款 → 仍作废
    o3 = Order(order_no="NR-3", platform="淘宝", status="paid", refund_status="退款关闭",
               refund_amount=Decimal("0"), paid_amount=Decimal("100"))
    assert osa._is_refunded(o3) is False         # 退款关闭(撤销) → 不作废
