"""支付宝流水 related_order_no 加宽到 255

源数据里 关联订单号 可能是多个订单号用 | 拼接 (实测最长 73 字符),
原 String(64) 会触发 StringDataRightTruncation。

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alipay_flows") as batch_op:
        batch_op.alter_column(
            "related_order_no",
            existing_type=sa.String(64),
            type_=sa.String(255),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("alipay_flows") as batch_op:
        batch_op.alter_column(
            "related_order_no",
            existing_type=sa.String(255),
            type_=sa.String(64),
            existing_nullable=True,
        )
