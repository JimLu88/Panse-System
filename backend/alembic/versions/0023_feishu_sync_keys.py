"""feishu sync_key columns and daily_operations table

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- sync_key column additions ---

    op.add_column("bom_lines",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_bom_lines_sync_key", "bom_lines", ["sync_key"])

    op.add_column("brand_marketing",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_brand_marketing_sync_key", "brand_marketing", ["sync_key"])

    op.add_column("promotion_flows",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_promotion_flows_sync_key", "promotion_flows", ["sync_key"])

    op.add_column("outsourcing_expenses",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_outsourcing_expenses_sync_key", "outsourcing_expenses", ["sync_key"])

    op.add_column("wood_losses",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_wood_losses_sync_key", "wood_losses", ["sync_key"])

    op.add_column("factory_reconciliations",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_factory_reconciliations_sync_key", "factory_reconciliations", ["sync_key"])

    op.add_column("account_balances",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_account_balances_sync_key", "account_balances", ["sync_key"])

    op.add_column("product_inventory",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_product_inventory_sync_key", "product_inventory", ["sync_key"])
    op.create_unique_constraint(
        "uq_product_inventory_warehouse_product", "product_inventory", ["warehouse", "product_code"])

    op.add_column("part_inventory",
        sa.Column("sync_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_part_inventory_sync_key", "part_inventory", ["sync_key"])
    op.create_unique_constraint(
        "uq_part_inventory_warehouse_material", "part_inventory", ["warehouse", "material_code"])

    # --- new daily_operations table ---

    op.create_table(
        "daily_operations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sync_key", sa.String(128), nullable=True),
        sa.Column("record_date", sa.Date, nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("item", sa.String(255), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("qty", sa.Numeric(12, 4), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("remark", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sync_key", name="uq_daily_operations_sync_key"),
    )
    op.create_index("ix_daily_operations_record_date", "daily_operations", ["record_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_operations_record_date", "daily_operations")
    op.drop_table("daily_operations")

    op.drop_constraint("uq_part_inventory_warehouse_material", "part_inventory", type_="unique")
    op.drop_constraint("uq_part_inventory_sync_key", "part_inventory", type_="unique")
    op.drop_column("part_inventory", "sync_key")

    op.drop_constraint("uq_product_inventory_warehouse_product", "product_inventory", type_="unique")
    op.drop_constraint("uq_product_inventory_sync_key", "product_inventory", type_="unique")
    op.drop_column("product_inventory", "sync_key")

    op.drop_constraint("uq_account_balances_sync_key", "account_balances", type_="unique")
    op.drop_column("account_balances", "sync_key")

    op.drop_constraint("uq_factory_reconciliations_sync_key", "factory_reconciliations", type_="unique")
    op.drop_column("factory_reconciliations", "sync_key")

    op.drop_constraint("uq_wood_losses_sync_key", "wood_losses", type_="unique")
    op.drop_column("wood_losses", "sync_key")

    op.drop_constraint("uq_outsourcing_expenses_sync_key", "outsourcing_expenses", type_="unique")
    op.drop_column("outsourcing_expenses", "sync_key")

    op.drop_constraint("uq_promotion_flows_sync_key", "promotion_flows", type_="unique")
    op.drop_column("promotion_flows", "sync_key")

    op.drop_constraint("uq_brand_marketing_sync_key", "brand_marketing", type_="unique")
    op.drop_column("brand_marketing", "sync_key")

    op.drop_constraint("uq_bom_lines_sync_key", "bom_lines", type_="unique")
    op.drop_column("bom_lines", "sync_key")
