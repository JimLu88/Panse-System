"""alipay_flows: 给 reconciliation_type 加索引 (对账规则全部按它过滤).

Revision ID: 0047
Revises: 0046
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_alipay_flows_recon_type", "alipay_flows", ["reconciliation_type"])


def downgrade():
    op.drop_index("ix_alipay_flows_recon_type", table_name="alipay_flows")
