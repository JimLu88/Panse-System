"""Official batch shipping files, without claiming platform delivery."""
import sqlalchemy as sa
from alembic import op

revision = "0159"
down_revision = "0158"
branch_labels = depends_on = None


def upgrade():
    op.add_column("fulfillment_intakes", sa.Column("batch_id", sa.String(64)))
    op.add_column("fulfillment_intakes", sa.Column("review_evidence", sa.JSON()))
    op.create_index("ix_fulfillment_intakes_batch_id", "fulfillment_intakes", ["batch_id"])
    op.create_table("fulfillment_batches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("file_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade():
    op.drop_table("fulfillment_batches")
    op.drop_index("ix_fulfillment_intakes_batch_id", table_name="fulfillment_intakes")
    op.drop_column("fulfillment_intakes", "review_evidence")
    op.drop_column("fulfillment_intakes", "batch_id")
