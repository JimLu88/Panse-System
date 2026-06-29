"""after_sales.platform_order_no String(64) → Text (修 09:40 自动匹配截断, 用户 2026-06-29)。

alipay_flow_router_service 用支付宝流水的 related_order_no(自配件 epic 起改存淘宝平台单号, 多单时
为多个 19 位单号拼接, 已超 64 字符)自动建售后(AfterSales), 写入 varchar(64) 报
StringDataRightTruncation → daily_0940_alipay_match 每天失败。改 Text 不限长, 与
part_purchases.related_order_no(0099) 同口径。additive 扩容, 不丢数据, 保留 index。

Revision ID: 0100
Revises: 0099
"""
import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("after_sales") as batch:
        batch.alter_column(
            "platform_order_no",
            existing_type=sa.String(64),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("after_sales") as batch:
        batch.alter_column(
            "platform_order_no",
            existing_type=sa.Text(),
            type_=sa.String(64),
            existing_nullable=False,
        )
