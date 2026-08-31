"""Add controlled campaign SKU slot pools.

Revision ID: 0146
Revises: 0145
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = "0146"
down_revision = "0145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_sku_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("taobao_item_id", sa.String(64), nullable=False),
        sa.Column("taobao_sku_id", sa.String(64), nullable=False, unique=True),
        sa.Column("physical_slot_code", sa.String(96), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="active"),
        sa.Column("attribute_sha256", sa.String(64), nullable=False),
        sa.Column("baseline_daily_price", sa.Numeric(12, 2)),
        sa.Column("custom_min_final_price", sa.Numeric(12, 2)),
        sa.Column("floor_evidence", sa.JSON()),
        sa.Column("cooling_until", sa.DateTime(timezone=True)),
        sa.Column("last_workflow_key", sa.String(128)),
        sa.Column("active_from", sa.DateTime(timezone=True)),
        sa.Column("active_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sku_code", "physical_slot_code",
                            name="uq_campaign_sku_slot_logical_physical"),
    )
    for name, cols in (
        ("ix_campaign_sku_slots_sku_code", ["sku_code"]),
        ("ix_campaign_sku_slots_item_id", ["taobao_item_id"]),
        ("ix_campaign_sku_slots_state", ["state"]),
    ):
        op.create_index(name, "campaign_sku_slots", cols)
    op.create_table(
        "campaign_sku_slot_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workflow_key", sa.String(128), nullable=False),
        sa.Column("taobao_item_id", sa.String(64), nullable=False),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("source_slot_id", sa.Integer()),
        sa.Column("target_slot_id", sa.Integer()),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("write_claimed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_id", sa.String(64), unique=True),
        sa.Column("result_summary", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workflow_key", "sku_code",
                            name="uq_campaign_sku_slot_attempt_workflow_sku"),
    )
    op.create_index("ix_campaign_sku_slot_attempt_workflow", "campaign_sku_slot_attempts", ["workflow_key"])
    op.create_index("ix_campaign_sku_slot_attempt_item", "campaign_sku_slot_attempts", ["taobao_item_id"])
    op.create_index("ix_campaign_sku_slot_attempt_sku", "campaign_sku_slot_attempts", ["sku_code"])
    op.create_index("ix_campaign_sku_slot_attempt_state", "campaign_sku_slot_attempts", ["state"])


def downgrade() -> None:
    op.drop_table("campaign_sku_slot_attempts")
    op.drop_table("campaign_sku_slots")
