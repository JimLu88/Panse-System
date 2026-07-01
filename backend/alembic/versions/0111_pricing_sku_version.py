"""工厂调价历史表 pricing_sku_version (有效期定价: 老单老价/新单新价, 历史利润不追溯改写)。

纯新增表, 不动既有数据; 无版本行时全系统行为 = 改造前 (回退 live pricing_sku)。

Revision ID: 0111
Revises: 0110
"""
import sqlalchemy as sa
from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None

_NUM = sa.Numeric(12, 2)


def upgrade() -> None:
    bind = op.get_bind()
    if "pricing_sku_version" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "pricing_sku_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_code", sa.String(32), nullable=False),
        sa.Column("product_code", sa.String(32), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("physical_cost", _NUM),
        sa.Column("factory_cost", _NUM),
        sa.Column("logistics_cost", _NUM),
        sa.Column("install_cost", _NUM),
        sa.Column("wood_cost", _NUM),
        sa.Column("external_parts_cost", _NUM),
        sa.Column("packaging_cost", _NUM),
        sa.Column("list_price", _NUM),
        sa.Column("daily_price", _NUM),
        sa.Column("small_promo", _NUM),
        sa.Column("mid_promo", _NUM),
        sa.Column("big_promo", _NUM),
        sa.Column("snapshot", sa.Text()),
        sa.Column("note", sa.String(255)),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pricing_sku_version_sku_code", "pricing_sku_version", ["sku_code"])
    op.create_index("ix_pricing_sku_version_product_code", "pricing_sku_version", ["product_code"])
    op.create_index("ix_pricing_sku_version_period_end", "pricing_sku_version", ["period_end"])
    op.create_index("ix_pricing_version_sku_period", "pricing_sku_version", ["sku_code", "period_end"])


def downgrade() -> None:
    bind = op.get_bind()
    if "pricing_sku_version" in sa.inspect(bind).get_table_names():
        op.drop_table("pricing_sku_version")
