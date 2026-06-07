"""配件返厂/退货单 part_returns (方案C — 坏件财务闭环).

记录坏件处置的钱: 退款应收 / 维修费 / 报废损失, 可关联支付宝流水与原采购单做供应商对账。
幂等 (表已存在则跳过), Postgres 与 SQLite 通用。

Revision ID: 0056
Revises: 0055
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    return table in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("part_returns"):
        return
    op.create_table(
        "part_returns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_code", sa.String(64), nullable=False, index=True),
        sa.Column("material_name", sa.String(255)),
        sa.Column("warehouse", sa.String(64), nullable=False, server_default="default"),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("amount_kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("reason", sa.String(255)),
        sa.Column("supplier", sa.String(128)),
        sa.Column("related_purchase_no", sa.String(32)),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("tracking_no", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False, server_default="open", index=True),
        sa.Column("actor", sa.String(64)),
        sa.Column("processed_at", sa.Date()),
        sa.Column("remark", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    if _has_table("part_returns"):
        op.drop_table("part_returns")
