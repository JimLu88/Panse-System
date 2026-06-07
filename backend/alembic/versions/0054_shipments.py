"""中央物流追踪表 shipments + 售后退货单号 after_sales.return_tracking_no

Revision ID: 0054
Revises: 0053
Create Date: 2026-06-07

幂等: 表/列已存在时跳过 (容器启动反复 upgrade head 不报错)。
"""
from alembic import op
import sqlalchemy as sa

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "shipments" not in insp.get_table_names():
        op.create_table(
            "shipments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("entity_type", sa.String(32), nullable=False, index=True),
            sa.Column("entity_id", sa.Integer(), nullable=False, index=True),
            sa.Column("tracking_no", sa.String(128), nullable=False, index=True),
            sa.Column("carrier_code", sa.String(64), nullable=True),
            sa.Column("carrier_name", sa.String(64), nullable=True),
            sa.Column("provider", sa.String(16), nullable=True),
            sa.Column("state", sa.String(16), nullable=True),
            sa.Column("mapped_status", sa.String(32), nullable=True),
            sa.Column("is_signed", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("last_status", sa.String(255), nullable=True),
            sa.Column("events", sa.JSON(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="true", index=True),
            sa.Column("last_error", sa.String(255), nullable=True),
            sa.Column("queried_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                      onupdate=sa.func.now(), nullable=False),
            sa.UniqueConstraint("entity_type", "entity_id", "tracking_no", name="uq_shipment_entity_no"),
        )
        op.create_index("ix_shipments_active_entity", "shipments", ["active", "entity_type"])

    as_cols = [c["name"] for c in insp.get_columns("after_sales")]
    if "return_tracking_no" not in as_cols:
        with op.batch_alter_table("after_sales") as batch:
            batch.add_column(sa.Column("return_tracking_no", sa.String(128), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    as_cols = [c["name"] for c in insp.get_columns("after_sales")]
    if "return_tracking_no" in as_cols:
        with op.batch_alter_table("after_sales") as batch:
            batch.drop_column("return_tracking_no")

    if "shipments" in insp.get_table_names():
        op.drop_index("ix_shipments_active_entity", table_name="shipments")
        op.drop_table("shipments")
