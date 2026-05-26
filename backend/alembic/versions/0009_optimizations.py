"""Phase optimizations (plan §12):
  - data_exceptions.escalation_count / last_escalated_at
  - ai_knowledge 表 (常见问题库)
  - 性能索引: orders / alipay_flows 复合索引

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 异常表加升级追踪字段 (§12.1)
    with op.batch_alter_table("data_exceptions") as batch:
        batch.add_column(sa.Column("escalation_count", sa.Integer, nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_escalated_at", sa.String(32)))

    # 2. AI 知识库 (§12.2)
    op.create_table(
        "ai_knowledge",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("exception_type", sa.String(64), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("solution_text", sa.Text, nullable=False),
        sa.Column("source_exception_id", sa.Integer),
        sa.Column("source_description", sa.Text),
        sa.Column("model", sa.String(64)),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("exception_type", "context_hash", name="uq_ai_knowledge_key"),
    )
    op.create_index("ix_ai_knowledge_exception_type", "ai_knowledge", ["exception_type"])

    # 3. 性能索引 (§12.3) — 订单按 (status, order_date) 常用查询
    op.create_index(
        "ix_orders_status_date", "orders", ["status", "order_date"],
    )
    # 支付宝按 (account, reconciliation_type, transaction_time) — 对账规则常扫
    op.create_index(
        "ix_alipay_flows_recon_acct_time",
        "alipay_flows",
        ["account", "reconciliation_type", "transaction_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_alipay_flows_recon_acct_time", table_name="alipay_flows")
    op.drop_index("ix_orders_status_date", table_name="orders")
    op.drop_index("ix_ai_knowledge_exception_type", table_name="ai_knowledge")
    op.drop_table("ai_knowledge")
    with op.batch_alter_table("data_exceptions") as batch:
        batch.drop_column("last_escalated_at")
        batch.drop_column("escalation_count")
