"""Extend procurement inquiries with discovery and decision tracking.

Revision ID: 0138
Revises: 0137
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0138"
down_revision = "0137"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    task_columns = _columns("procurement_tasks")
    if "search_queries" not in task_columns:
        op.add_column(
            "procurement_tasks",
            sa.Column(
                "search_queries",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            ),
        )

    inquiry_columns = _columns("procurement_inquiries")
    additions = (
        ("merchant_external_id", sa.Column("merchant_external_id", sa.String(255))),
        ("discovery_query", sa.Column("discovery_query", sa.String(255))),
        ("discovered_at", sa.Column("discovered_at", sa.DateTime(timezone=True))),
        ("candidate_score", sa.Column("candidate_score", sa.Numeric(5, 2))),
        ("candidate_reason", sa.Column("candidate_reason", sa.Text())),
        (
            "candidate_snapshot",
            sa.Column(
                "candidate_snapshot",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json"),
            ),
        ),
        ("candidate_dedupe_key", sa.Column("candidate_dedupe_key", sa.String(64))),
        ("source_rank", sa.Column("source_rank", sa.Integer())),
        (
            "discovery_attempts",
            sa.Column("discovery_attempts", sa.Integer(), nullable=False, server_default="0"),
        ),
        ("last_discovery_error", sa.Column("last_discovery_error", sa.Text())),
        (
            "decision_status",
            sa.Column(
                "decision_status",
                sa.String(16),
                nullable=False,
                server_default="pending",
            ),
        ),
        ("decision_note", sa.Column("decision_note", sa.Text())),
        ("decided_at", sa.Column("decided_at", sa.DateTime(timezone=True))),
        ("decided_by", sa.Column("decided_by", sa.String(64))),
        (
            "supplier_id",
            sa.Column(
                "supplier_id",
                sa.Integer(),
                sa.ForeignKey("suppliers.id", ondelete="SET NULL"),
            ),
        ),
        (
            "part_purchase_id",
            sa.Column(
                "part_purchase_id",
                sa.Integer(),
                sa.ForeignKey("part_purchases.id", ondelete="SET NULL"),
            ),
        ),
    )
    for name, column in additions:
        if name not in inquiry_columns:
            op.add_column("procurement_inquiries", column)

    indexes = _indexes("procurement_inquiries")
    if "uq_procurement_inquiries_task_candidate" not in indexes:
        op.create_index(
            "uq_procurement_inquiries_task_candidate",
            "procurement_inquiries",
            ["task_id", "candidate_dedupe_key"],
            unique=True,
        )
    for name, columns in (
        ("ix_procurement_inquiries_decision_status", ["decision_status"]),
        ("ix_procurement_inquiries_supplier_id", ["supplier_id"]),
        ("ix_procurement_inquiries_part_purchase_id", ["part_purchase_id"]),
    ):
        if name not in indexes:
            op.create_index(name, "procurement_inquiries", columns)


def downgrade() -> None:
    indexes = _indexes("procurement_inquiries")
    for name in (
        "ix_procurement_inquiries_part_purchase_id",
        "ix_procurement_inquiries_supplier_id",
        "ix_procurement_inquiries_decision_status",
        "uq_procurement_inquiries_task_candidate",
    ):
        if name in indexes:
            op.drop_index(name, table_name="procurement_inquiries")

    inquiry_columns = _columns("procurement_inquiries")
    for name in (
        "part_purchase_id",
        "supplier_id",
        "decided_by",
        "decided_at",
        "decision_note",
        "decision_status",
        "last_discovery_error",
        "discovery_attempts",
        "source_rank",
        "candidate_dedupe_key",
        "candidate_snapshot",
        "candidate_reason",
        "candidate_score",
        "discovered_at",
        "discovery_query",
        "merchant_external_id",
    ):
        if name in inquiry_columns:
            op.drop_column("procurement_inquiries", name)

    if "search_queries" in _columns("procurement_tasks"):
        op.drop_column("procurement_tasks", "search_queries")
