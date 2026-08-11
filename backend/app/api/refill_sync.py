"""评价系统补单跟踪 -> ERP 补单主表。

这里只接收评价系统已经确认的补单订单号。写入 ``refill_records`` 后，ERP 现有
``rederive_refill_flags`` 定时任务仍以同一张主表为准，不会在次日把标识撤掉。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.finance import RefillRecord
from app.models.order import Order
from app.services import order_cost_service

router = APIRouter(prefix="/api/refill-sync", tags=["refill-sync"])

REVIEW_SYNC_REMARK = "评价系统补单跟踪自动同步"
REVIEW_REFILL_COMMISSION = Decimal("15.00")


class ReviewOrderTrackIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=64)
    product_name: Optional[str] = Field(default=None, max_length=255)
    placed_date: Optional[date] = None
    commission: Decimal = Field(default=REVIEW_REFILL_COMMISSION, gt=0, le=Decimal("9999.99"))


class ReviewOrderTrackBatchIn(BaseModel):
    items: list[ReviewOrderTrackIn] = Field(min_length=1, max_length=500)


def sync_review_order_tracks(db: Session, items: list[ReviewOrderTrackIn]) -> dict:
    """幂等写入补单主表，并立即给已存在的 ERP 订单打补单标识。"""
    normalized: dict[str, ReviewOrderTrackIn] = {}
    for item in items:
        no = item.order_no.strip()
        if no:
            normalized[no] = item.model_copy(update={"order_no": no})

    order_nos = list(normalized)
    existing_rows = db.scalars(
        select(RefillRecord)
        .where(RefillRecord.order_no.in_(order_nos))
        .order_by(RefillRecord.id)
    ).all()
    existing = {row.order_no.strip(): row for row in existing_rows if row.order_no}
    orders = {
        row.order_no.strip(): row
        for row in db.scalars(select(Order).where(Order.order_no.in_(order_nos))).all()
        if row.order_no
    }

    created = filled = commission_filled = flagged = already_marked = 0
    missing_orders: list[str] = []
    for no, item in normalized.items():
        order = orders.get(no)
        record = existing.get(no)
        if record is None:
            record = RefillRecord(
                order_no=no,
                buyer_nick=order.customer_name if order else None,
                refill_date=item.placed_date or (order.order_date if order else None) or date.today(),
                product_code=order.product_code if order else None,
                product_name=item.product_name or (order.product_name if order else None),
                sku=order.sku if order else None,
                order_amount=order.paid_amount if order else None,
                qty=(order.qty or 1) if order else 1,
                commission=item.commission,
                remark=REVIEW_SYNC_REMARK,
                sync_key=f"review-order-track:{no}",
            )
            db.add(record)
            existing[no] = record
            created += 1
        else:
            # 已有人工财务记录不覆盖，只补空白的基础识别字段。
            before = (record.refill_date, record.product_name, record.product_code, record.sku,
                      record.commission)
            if record.refill_date is None:
                record.refill_date = item.placed_date or (order.order_date if order else None)
            if not record.product_name:
                record.product_name = item.product_name or (order.product_name if order else None)
            if not record.product_code and order:
                record.product_code = order.product_code
            if not record.sku and order:
                record.sku = order.sku
            # 只修复评价程序自动同步且漏佣金的历史记录；人工财务记录即使佣金为 0 也不覆盖。
            if (record.remark or "").strip() == REVIEW_SYNC_REMARK and not record.commission:
                record.commission = item.commission
                commission_filled += 1
            after = (record.refill_date, record.product_name, record.product_code, record.sku,
                     record.commission)
            if after != before:
                filled += 1

        if order is None:
            # 仍保留 refill_record；淘宝订单稍后导入时，现有 apply_refill_flags 会自动命中。
            missing_orders.append(no)
        elif order.is_refill:
            already_marked += 1
        else:
            order.is_refill = True
            order_cost_service.recompute_and_save(db, order)
            flagged += 1

    db.commit()
    return {
        "received": len(items),
        "unique": len(normalized),
        "created": created,
        "filled": filled,
        "commission_filled": commission_filled,
        "flagged": flagged,
        "already_marked": already_marked,
        "missing_orders": missing_orders,
    }


@router.post("/review-order-tracks")
def sync_review_order_tracks_api(
    payload: ReviewOrderTrackBatchIn,
    db: Session = Depends(get_db),
):
    return sync_review_order_tracks(db, payload.items)
