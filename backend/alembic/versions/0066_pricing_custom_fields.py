"""pricing_custom_fields + pricing_custom_values — 定价表自定义列(EAV)。

用户可在定价表自建任意数值/文本列并改名, 按 SKU 填值。
幂等: 表已存在则跳过。

Revision ID: 0066
Revises: 0065
"""
import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "pricing_custom_fields" not in tables:
        op.create_table(
            "pricing_custom_fields",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("label", sa.String(64), nullable=False),
            sa.Column("value_kind", sa.String(8), nullable=False, server_default="number"),
            sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "pricing_custom_values" not in tables:
        op.create_table(
            "pricing_custom_values",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("sku_code", sa.String(32), nullable=False, index=True),
            sa.Column(
                "field_id", sa.Integer,
                sa.ForeignKey("pricing_custom_fields.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("num_value", sa.Numeric(16, 4), nullable=True),
            sa.Column("text_value", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("sku_code", "field_id", name="uq_pcv_sku_field"),
        )


def downgrade() -> None:
    op.drop_table("pricing_custom_values")
    op.drop_table("pricing_custom_fields")
