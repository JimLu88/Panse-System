"""Bind factory delivery to Taobao child-order lines.

Revision ID: 0139
Revises: 0138
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa


revision = "0139"
down_revision = "0138"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    columns = _columns("order_details")
    additions = (
        ("sub_order_no", sa.Column("sub_order_no", sa.String(64), nullable=True)),
        ("line_status", sa.Column("line_status", sa.String(32), nullable=True)),
        ("refund_status", sa.Column("refund_status", sa.String(64), nullable=True)),
        ("refund_amount", sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True)),
        ("factory_no", sa.Column("factory_no", sa.Integer(), nullable=True)),
        (
            "factory_delivery_required",
            sa.Column(
                "factory_delivery_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        ),
        ("factory_delivery_state", sa.Column("factory_delivery_state", sa.String(32))),
        ("factory_delivery_key", sa.Column("factory_delivery_key", sa.String(160))),
        ("factory_delivery_error", sa.Column("factory_delivery_error", sa.Text())),
        (
            "factory_delivery_sent_at",
            sa.Column("factory_delivery_sent_at", sa.DateTime(timezone=True)),
        ),
        (
            "factory_delivery_message_id",
            sa.Column("factory_delivery_message_id", sa.String(128)),
        ),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("order_details", column)

    indexes = _indexes("order_details")
    if "ix_order_details_sub_order_no" not in indexes:
        op.create_index(
            "ix_order_details_sub_order_no", "order_details", ["sub_order_no"], unique=True
        )
    if "ix_order_details_line_status" not in indexes:
        op.create_index("ix_order_details_line_status", "order_details", ["line_status"])
    if "ix_order_details_factory_no" not in indexes:
        op.create_index(
            "ix_order_details_factory_no", "order_details", ["factory_no"], unique=True
        )
    if "ix_order_details_factory_delivery_required" not in indexes:
        op.create_index(
            "ix_order_details_factory_delivery_required",
            "order_details",
            ["factory_delivery_required"],
        )
    if "ix_order_details_factory_delivery_state" not in indexes:
        op.create_index(
            "ix_order_details_factory_delivery_state",
            "order_details",
            ["factory_delivery_state"],
        )


def downgrade() -> None:
    indexes = _indexes("order_details")
    for name in (
        "ix_order_details_factory_no",
        "ix_order_details_line_status",
        "ix_order_details_sub_order_no",
        "ix_order_details_factory_delivery_required",
        "ix_order_details_factory_delivery_state",
    ):
        if name in indexes:
            op.drop_index(name, table_name="order_details")
    columns = _columns("order_details")
    for name in (
        "factory_delivery_message_id", "factory_delivery_sent_at",
        "factory_delivery_error", "factory_delivery_key", "factory_delivery_state",
        "factory_delivery_required", "factory_no", "refund_amount",
        "refund_status", "line_status", "sub_order_no",
    ):
        if name in columns:
            op.drop_column("order_details", name)
