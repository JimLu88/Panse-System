"""alipay_flows.transaction_no: VARCHAR(64) → VARCHAR(128)

支付宝主力号等账户的退款类流水号可超 64 字符 (最长约 70 位),
导致整表导入失败。同步扩展 sync_key 以容纳更长的拼接串。

Revision ID: 0043
Revises: 0042
"""
from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("alipay_flows", "transaction_no",
                    type_=sa.String(128), existing_nullable=False)
    op.alter_column("alipay_flows", "sync_key",
                    type_=sa.String(255), existing_nullable=True)


def downgrade():
    op.alter_column("alipay_flows", "transaction_no",
                    type_=sa.String(64), existing_nullable=False)
    op.alter_column("alipay_flows", "sync_key",
                    type_=sa.String(160), existing_nullable=True)
