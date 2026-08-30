"""Add durable one-shot campaign execution attempts.

Revision ID: 0145
Revises: 0144
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_execution_attempts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False,
                  server_default="signup"),
        sa.Column("scope_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("write_claimed", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("write_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("platform_write_observed", sa.Boolean()),
        sa.Column("automatic_retry_allowed", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("request_id", sa.String(length=64)),
        sa.Column("web_agent_job_id", sa.String(length=64)),
        sa.Column("last_step", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("result_summary", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_campaign_execution_request_id"),
        sa.UniqueConstraint(
            "workflow_key", "operation", "scope_sha256",
            name="uq_campaign_execution_workflow_operation_scope"),
    )
    op.create_index(
        "ix_campaign_execution_plan_id", "campaign_execution_attempts", ["plan_id"])
    op.create_index(
        "ix_campaign_execution_workflow_key", "campaign_execution_attempts",
        ["workflow_key"])
    op.create_index(
        "ix_campaign_execution_state", "campaign_execution_attempts", ["state"])
    # Correct the one historical plan-7 snapshot whose paused enrolled rows
    # were labelled as drafts.  Preserve the original item list as
    # ``accepted_item_ids`` and make the compatibility draft list explicitly
    # empty so old callers fail closed.
    op.execute(sa.text("""
        UPDATE campaign_evidence_snapshots
        SET result_status = 'partial_enrollment_audited',
            platform_summary = (
                (platform_summary::jsonb
                 - 'draft_imported_item_ids'
                 - 'published'
                 - 'stopped_before'
                 - 'platform_write_kind')
                || jsonb_build_object(
                    'platform_write_kind', 'partial_enrollment_import',
                    'enrollment_record_created', true,
                    'active', false,
                    'published', null,
                    'stopped_before', null,
                    'accepted_item_ids', COALESCE(
                        platform_summary::jsonb->'draft_imported_item_ids', '[]'::jsonb),
                    'enrolled_paused_item_ids', COALESCE(
                        platform_summary::jsonb->'official_paused_or_pending_item_ids',
                        '[]'::jsonb),
                    'draft_imported_item_ids', '[]'::jsonb,
                    'state_interpretation_version', 2,
                    'recovery_blocker',
                    'paused enrolled records are not publishable drafts'
                )
            )::json
        WHERE evidence_type = 'plan7_remaining_partial_import_audit'
          AND result_status = 'partial_draft_import_audited'
    """))


def downgrade() -> None:
    op.drop_index("ix_campaign_execution_state",
                  table_name="campaign_execution_attempts")
    op.drop_index("ix_campaign_execution_workflow_key",
                  table_name="campaign_execution_attempts")
    op.drop_index("ix_campaign_execution_plan_id",
                  table_name="campaign_execution_attempts")
    op.drop_table("campaign_execution_attempts")
