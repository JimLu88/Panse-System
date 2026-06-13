"""大表复合索引 + 飞书通知重试队列 + 对账每日快照 (用户审核项 14/17 + 对账建议 13)。

幂等: 已存在则跳过。

Revision ID: 0076
Revises: 0075
"""
import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

_INDEXES = [
    # (索引名, 表, 列列表) — 订单/流水/采购的高频查询路径
    ("ix_orders_date_status", "orders", ["order_date", "status"]),
    ("ix_orders_product_code", "orders", ["product_code"]),
    ("ix_alipay_flows_account_time", "alipay_flows", ["account", "transaction_time"]),
    ("ix_alipay_flows_recon_type", "alipay_flows", ["reconciliation_type"]),
    ("ix_part_purchases_date", "part_purchases", ["purchase_date"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    for name, table, cols in _INDEXES:
        if table not in tables:
            continue
        existing = {ix["name"] for ix in insp.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, cols)

    if "notify_retries" not in tables:
        op.create_table(
            "notify_retries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("title", sa.String(length=128), nullable=True),
            sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_notify_retries_pending", "notify_retries", ["sent_at", "next_at"])

    if "recon_snapshots" not in tables:
        op.create_table(
            "recon_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("snap_date", sa.Date(), nullable=False),
            sa.Column("rule", sa.String(length=64), nullable=False),
            sa.Column("ok_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_diff_abs", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_recon_snapshots_date_rule", "recon_snapshots",
                        ["snap_date", "rule"], unique=True)


def downgrade() -> None:
    for name, table, _ in _INDEXES:
        op.drop_index(name, table_name=table)
    op.drop_index("ix_recon_snapshots_date_rule", table_name="recon_snapshots")
    op.drop_table("recon_snapshots")
    op.drop_index("ix_notify_retries_pending", table_name="notify_retries")
    op.drop_table("notify_retries")
