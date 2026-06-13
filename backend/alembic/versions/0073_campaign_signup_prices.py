"""campaign_signup_prices 新表 (Plan F1 — 活动报名价存档, 与定价渠道价自动对照)。

幂等: 表已存在则跳过。

Revision ID: 0073
Revises: 0072
"""
import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "campaign_signup_prices" in insp.get_table_names():
        return
    op.create_table(
        "campaign_signup_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_code", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="taobao"),
        sa.Column("campaign_name", sa.String(length=128), nullable=True),
        sa.Column("signup_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="import"),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("sku_code", "channel", "campaign_name",
                            name="uq_campaign_signup_sku_channel_campaign"),
    )
    op.create_index("ix_campaign_signup_prices_sku_code", "campaign_signup_prices", ["sku_code"])


def downgrade() -> None:
    op.drop_index("ix_campaign_signup_prices_sku_code", table_name="campaign_signup_prices")
    op.drop_table("campaign_signup_prices")
