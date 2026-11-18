"""Logistics registrations and durable single-package submission claims."""
import sqlalchemy as sa
from alembic import op

revision = "0158"
down_revision = "0157"
branch_labels = depends_on = None


def upgrade():
    op.create_table("fulfillment_intakes",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("image_key", sa.String(256)),
        sa.Column("image_hash", sa.String(64)),
        sa.Column("order_no", sa.String(64)),
        sa.Column("sub_order_no", sa.String(64)),
        sa.Column("carrier", sa.String(64)),
        sa.Column("tracking_no", sa.String(128)),
        sa.Column("readbacks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(256), nullable=False),
        sa.Column("order_claim", sa.String(64), unique=True),
        sa.Column("parcel_claim", sa.String(196), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade():
    op.drop_table("fulfillment_intakes")
