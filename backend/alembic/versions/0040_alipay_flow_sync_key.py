"""给 alipay_flows 加飞书同步配对键 sync_key

飞书同步原来用 transaction_no 当配对键, 同号配对流水(在线支付+分账)会被压成一行、
同步时丢一条。改用 account+流水号+交易类型+金额 拼的 sync_key (与唯一约束一致),
配对流水两端都能配上。本迁移加列 + 索引 + 回填存量数据 (与 models._alipay_sync_key 同口径)。

Revision ID: 0040
Revises: 0039
"""
from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alipay_flows", sa.Column("sync_key", sa.String(length=160), nullable=True))
    op.create_index("ix_alipay_flows_sync_key", "alipay_flows", ["sync_key"])
    # 回填: 'alipay:' + account:流水号:交易类型:金额 (None→空串, 金额按原始文本)
    op.execute(
        """
        UPDATE alipay_flows
        SET sync_key = 'alipay:'
            || coalesce(account, '') || ':'
            || coalesce(transaction_no, '') || ':'
            || coalesce(transaction_type, '') || ':'
            || coalesce(amount::text, '')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_alipay_flows_sync_key", table_name="alipay_flows")
    op.drop_column("alipay_flows", "sync_key")
