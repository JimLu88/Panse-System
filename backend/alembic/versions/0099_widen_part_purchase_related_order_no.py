"""part_purchases.related_order_no String(64) → Text (配件 epic 阶段2/3, 用户 2026-06-28)。

一笔零星配件采购(支付宝付款)可对应多个淘宝订单号(平台订单号列常多单 \n 分隔), 6 个 19 位单号
加分隔符已超 64 字符 → 改 Text 不限长, 供 aggregate_related_purchases 拆单按 BOM 占比分摊。
additive 扩容, 不丢数据。

Revision ID: 0099
Revises: 0098
"""
import sqlalchemy as sa
from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("part_purchases") as batch:
        batch.alter_column(
            "related_order_no",
            existing_type=sa.String(64),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("part_purchases") as batch:
        batch.alter_column(
            "related_order_no",
            existing_type=sa.Text(),
            type_=sa.String(64),
            existing_nullable=True,
        )
