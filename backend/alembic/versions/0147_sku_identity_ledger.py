"""Add append-only Taobao SKU identity ledger.

Revision ID: 0147
Revises: 0146
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa


revision = "0147"
down_revision = "0146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sku_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("taobao_item_id", sa.String(64), nullable=False),
        sa.Column("taobao_sku_id", sa.String(64), nullable=False),
        sa.Column("merchant_code", sa.String(96)),
        sa.Column("sku_spec", sa.Text()),
        sa.Column("sku_code", sa.String(64)),
        sa.Column("product_code", sa.String(64)),
        sa.Column("is_custom_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_sale_state", sa.String(32)),
        sa.Column("latest_daily_price", sa.Numeric(12, 2)),
        sa.Column("latest_evidence_source", sa.String(128), nullable=False),
        sa.Column("latest_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("conflict_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("taobao_item_id", "taobao_sku_id", name="uq_sku_identity_item_sku"),
    )
    for name, cols in (
        ("ix_sku_identities_item", ["taobao_item_id"]),
        ("ix_sku_identities_sku", ["taobao_sku_id"]),
        ("ix_sku_identities_merchant", ["merchant_code"]),
        ("ix_sku_identities_sku_code", ["sku_code"]),
        ("ix_sku_identities_product_code", ["product_code"]),
    ):
        op.create_index(name, "sku_identities", cols)
    op.create_table(
        "sku_identity_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("identity_id", sa.Integer(), sa.ForeignKey("sku_identities.id")),
        sa.Column("taobao_item_id", sa.String(64), nullable=False),
        sa.Column("taobao_sku_id", sa.String(64), nullable=False),
        sa.Column("merchant_code", sa.String(96)),
        sa.Column("sku_spec", sa.Text()),
        sa.Column("sku_code", sa.String(64)),
        sa.Column("product_code", sa.String(64)),
        sa.Column("is_custom_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("daily_price", sa.Numeric(12, 2)),
        sa.Column("sale_state", sa.String(32)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_source", sa.String(128), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON()),
    )
    for name, cols in (
        ("ix_sku_identity_observation_identity", ["identity_id"]),
        ("ix_sku_identity_observation_item", ["taobao_item_id"]),
        ("ix_sku_identity_observation_sku", ["taobao_sku_id"]),
        ("ix_sku_identity_observation_observed", ["observed_at"]),
    ):
        op.create_index(name, "sku_identity_observations", cols)
    op.create_table(
        "sku_physical_slot_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("taobao_item_id", sa.String(64), nullable=False),
        sa.Column("parent_taobao_sku_id", sa.String(64)),
        sa.Column("parent_merchant_code", sa.String(96), nullable=False),
        sa.Column("target_merchant_code", sa.String(96), nullable=False, unique=True),
        sa.Column("source_option", sa.String(255), nullable=False),
        sa.Column("target_option", sa.String(255), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("authorization_ref", sa.String(255), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("product_create_status", sa.String(32), nullable=False),
        sa.Column("product_save_status", sa.String(32), nullable=False),
        sa.Column("campaign_signup_status", sa.String(32), nullable=False),
        sa.Column("proposed_fields", sa.JSON()),
        sa.Column("evidence_source", sa.String(128), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("taobao_item_id", "slot_number", name="uq_sku_physical_slot_item_number"),
    )
    op.create_index("ix_sku_slot_proposals_item", "sku_physical_slot_proposals", ["taobao_item_id"])
    op.create_index("ix_sku_slot_proposals_state", "sku_physical_slot_proposals", ["lifecycle_state"])


def downgrade() -> None:
    op.drop_table("sku_physical_slot_proposals")
    op.drop_table("sku_identity_observations")
    op.drop_table("sku_identities")
