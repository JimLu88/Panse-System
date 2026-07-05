"""评价资产台账 review_assets (Plan1 v2): 带图好评资产 + 折叠倒计时提醒。

淘宝评价约180天后退出商品页首屏默认排序(俗称"折叠/沉底", 非删除)。花钱刷的带图好评
是有寿命(约180天)的资产, 靠人工记时间常漏。本表记每条评价时间线, 由定时任务
daily_0900_review_asset_remind 扫: 折叠倒计时(多级30·14·7)、待评价超时、产品活跃覆盖。

口径: 补单=刷单(source=refill, 对齐 orders.is_refill); 本表只管评价资产,
不产生任何经营/财务数字。预留 rating/review_text 供方向#4(舆情/回评)共用同表。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 评价资产状态机
REVIEW_STATUS = (
    "pending_review",  # 待评价 (刷单已发货, 尚未去评价; review_date 空)
    "reviewed",        # 已评价 (活跃带图好评)
    "folding_soon",    # 临近折叠 (job 自动打, 距 fold_due_date ≤ 30 天)
    "folded",          # 已折叠 (job 自动打, 过 fold_due_date 未释放)
    "released",        # 已释放 (人工: 已安排新刷单节点补新评价) — 终态
    "abandoned",       # 放弃 — 终态
)
# 终态: job 不再改动
REVIEW_TERMINAL = ("released", "abandoned")
# 活跃(可展示)状态: 计入产品覆盖
REVIEW_ACTIVE = ("reviewed", "folding_soon")


class ReviewAsset(Base, TimestampMixin):
    __tablename__ = "review_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), index=True)  # 能关联就关联
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # 平台订单号
    shop: Mapped[Optional[str]] = mapped_column(String(32), index=True)  # 店铺, 从订单带出
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    sku_name: Mapped[Optional[str]] = mapped_column(String(128))  # 规格 (from-order 取 orders.sku_code)

    review_date: Mapped[Optional[date]] = mapped_column(Date, index=True)  # 评价日期; pending 时为空, 评完补录
    image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 评价图张数
    rating: Mapped[Optional[int]] = mapped_column(SmallInteger)  # 星级 1-5 (方向#4 预留; 补单一般 5)
    review_text: Mapped[Optional[str]] = mapped_column(Text)  # 评价内容 (方向#4 预留, 可空)

    # 折叠预期日 = review_date + settings.review_fold_days (默认180); review_date 空则空
    fold_due_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), default="refill", nullable=False)  # refill 补单 / natural 自然单

    screenshot_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("imported_files.id"))  # P1 截图归档

    # 提醒防刷屏: 记已推送日期与已到达的最高级别
    last_notified_date: Mapped[Optional[date]] = mapped_column(Date)
    last_notified_level: Mapped[Optional[str]] = mapped_column(String(16))  # info/warn/error
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # 状态流转留痕
    remark: Mapped[Optional[str]] = mapped_column(Text)
