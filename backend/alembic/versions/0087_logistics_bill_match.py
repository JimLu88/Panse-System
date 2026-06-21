"""logistics_bills 加收货人/目的地/匹配结果/行类型 — 物流费账单按人名(+省)匹配订单。

德邦逐单行: recipient_name(收货人) + destination(目的地) 用来配淘宝订单, 结果写
match_method/match_note, 命中写回 order_no; 配不到 match_method='none' (前端显示「未能自动匹配」)。
row_type 区分 line(逐单) / summary(月结汇总), 让汇总行从逐单表里分出来挪到表底, 并核对
"月结总额 vs 各单相加"。

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("logistics_bills")}
    if "recipient_name" not in cols:
        op.add_column("logistics_bills", sa.Column("recipient_name", sa.String(length=64), nullable=True))
        op.create_index("ix_logistics_bills_recipient_name", "logistics_bills", ["recipient_name"])
    if "destination" not in cols:
        op.add_column("logistics_bills", sa.Column("destination", sa.String(length=128), nullable=True))
    if "match_method" not in cols:
        op.add_column("logistics_bills", sa.Column("match_method", sa.String(length=32), nullable=True))
    if "match_note" not in cols:
        op.add_column("logistics_bills", sa.Column("match_note", sa.Text(), nullable=True))
    if "row_type" not in cols:
        op.add_column("logistics_bills", sa.Column("row_type", sa.String(length=16),
                                                   nullable=False, server_default="line"))


def downgrade() -> None:
    for col, has_idx in (("recipient_name", True), ("destination", False),
                         ("match_method", False), ("match_note", False), ("row_type", False)):
        try:
            if has_idx:
                op.drop_index(f"ix_logistics_bills_{col}", table_name="logistics_bills")
            op.drop_column("logistics_bills", col)
        except Exception:  # noqa: BLE001
            pass
