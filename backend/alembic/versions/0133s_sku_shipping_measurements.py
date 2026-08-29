"""Add logistics package measurements and SKU shipping dimensions.

Revision ID: 0133s
Revises: 0133
Create Date: 2026-08-01
"""
from alembic import op
import sqlalchemy as sa


revision = "0133s"
down_revision = "0133"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    logistics_columns = _columns("logistics_bills")
    for name, column_type in (
        ("actual_weight_kg", sa.Numeric(10, 3)),
        ("volume_m3", sa.Numeric(10, 4)),
        ("package_count", sa.Integer()),
    ):
        if name not in logistics_columns:
            op.add_column("logistics_bills", sa.Column(name, column_type, nullable=True))

    sku_columns = _columns("pricing_sku")
    for name, column_type in (
        ("product_weight_kg", sa.Numeric(10, 3)),
        ("packaged_weight_kg", sa.Numeric(10, 3)),
        ("product_volume_m3", sa.Numeric(10, 4)),
        ("packaged_volume_m3", sa.Numeric(10, 4)),
        ("packaged_weight_source", sa.String(16)),
        ("packaged_volume_source", sa.String(16)),
        ("shipping_measure_source_tracking_no", sa.String(128)),
        ("shipping_measure_source_date", sa.Date()),
        ("shipping_measure_sample_count", sa.Integer()),
    ):
        if name not in sku_columns:
            op.add_column("pricing_sku", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name in (
        "shipping_measure_sample_count", "shipping_measure_source_date",
        "shipping_measure_source_tracking_no", "packaged_volume_source",
        "packaged_weight_source", "packaged_volume_m3", "product_volume_m3",
        "packaged_weight_kg", "product_weight_kg",
    ):
        if name in _columns("pricing_sku"):
            op.drop_column("pricing_sku", name)
    for name in ("package_count", "volume_m3", "actual_weight_kg"):
        if name in _columns("logistics_bills"):
            op.drop_column("logistics_bills", name)
