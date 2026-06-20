"""order_details 加行级商品列 (qty/amount/source) — 一单多产品按行汇总成本, 杜绝塌单漏算。

source='import' = 导入的商品行(一单多宝贝); source='bom'/auto = 原有 BOM 物料分解行。

Revision ID: 0084
Revises: 0083
"""
import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("order_details")}
    if "qty" not in cols:
        op.add_column("order_details", sa.Column("qty", sa.Integer(), nullable=True))
    if "amount" not in cols:
        op.add_column("order_details", sa.Column("amount", sa.Numeric(12, 2), nullable=True))
    if "source" not in cols:
        op.add_column("order_details", sa.Column("source", sa.String(16), nullable=True))
        try:
            op.create_index("ix_order_details_source", "order_details", ["source"])
        except Exception:  # noqa: BLE001
            pass


def downgrade() -> None:
    try:
        op.drop_index("ix_order_details_source", table_name="order_details")
    except Exception:  # noqa: BLE001
        pass
    for c in ("qty", "amount", "source"):
        try:
            op.drop_column("order_details", c)
        except Exception:  # noqa: BLE001
            pass
