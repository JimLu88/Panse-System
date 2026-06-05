"""alipay_flows: 给 reconciliation_type 加索引 (对账规则全部按它过滤).

幂等: 索引已存在则跳过 (避免重复 CREATE INDEX 报错)。

Revision ID: 0047
Revises: 0046
"""
from alembic import op
from sqlalchemy import inspect

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    existing = {ix["name"] for ix in inspect(op.get_bind()).get_indexes("alipay_flows")}
    if "ix_alipay_flows_recon_type" not in existing:
        op.create_index("ix_alipay_flows_recon_type", "alipay_flows", ["reconciliation_type"])


def downgrade():
    op.drop_index("ix_alipay_flows_recon_type", table_name="alipay_flows")
