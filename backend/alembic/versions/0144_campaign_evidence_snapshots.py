"""Persist campaign platform evidence and terminal receipts.

Revision ID: 0144
Revises: 0143
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = "0144"
down_revision = "0143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_evidence_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("web_agent_job_id", sa.String(length=64)),
        sa.Column("scope_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_status", sa.String(length=32), nullable=False),
        sa.Column("platform_summary", sa.JSON()),
        sa.Column("rows", sa.JSON()),
        sa.Column("failure_rows", sa.JSON()),
        sa.Column("execution_boundary", sa.JSON(), nullable=False),
        sa.Column("artifact_kind", sa.String(length=64)),
        sa.Column("artifact_filename", sa.String(length=255)),
        sa.Column("artifact_sha256", sa.String(length=64)),
        sa.Column("artifact_size", sa.Integer()),
        sa.Column("artifact_blob", sa.LargeBinary()),
        sa.Column("failure_artifact_filename", sa.String(length=255)),
        sa.Column("failure_artifact_sha256", sa.String(length=64)),
        sa.Column("failure_artifact_size", sa.Integer()),
        sa.Column("failure_artifact_blob", sa.LargeBinary()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_campaign_evidence_request_id"),
    )
    op.create_index("ix_campaign_evidence_plan_id", "campaign_evidence_snapshots", ["plan_id"])
    op.create_index("ix_campaign_evidence_workflow_key", "campaign_evidence_snapshots", ["workflow_key"])
    op.create_index("ix_campaign_evidence_type", "campaign_evidence_snapshots", ["evidence_type"])


def downgrade() -> None:
    op.drop_index("ix_campaign_evidence_type", table_name="campaign_evidence_snapshots")
    op.drop_index("ix_campaign_evidence_workflow_key", table_name="campaign_evidence_snapshots")
    op.drop_index("ix_campaign_evidence_plan_id", table_name="campaign_evidence_snapshots")
    op.drop_table("campaign_evidence_snapshots")
