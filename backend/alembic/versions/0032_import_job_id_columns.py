"""add import_job_id to importable tables

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

TABLES = [
    "orders",
    "alipay_flows",
    "factory_orders",
    "factory_reconciliations",
    "delivery_notes",
    "delivery_note_lines",
]

def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column(
            "import_job_id", sa.Integer(),
            sa.ForeignKey("import_jobs.id", ondelete="SET NULL"),
            nullable=True, index=True,
        ))

def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "import_job_id")
