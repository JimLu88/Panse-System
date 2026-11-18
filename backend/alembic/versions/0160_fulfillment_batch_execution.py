"""Add platform_task_id and execution to fulfillment_batches."""
import sqlalchemy as sa
from alembic import op

revision = "0160"
down_revision = "0159"
branch_labels = depends_on = None


def upgrade():
    op.add_column("fulfillment_batches", sa.Column("platform_task_id", sa.String(64), nullable=True))
    op.add_column("fulfillment_batches", sa.Column("execution", sa.JSON(), nullable=True))
    op.create_index('uq_fulfillment_platform_task', 'fulfillment_batches', ['platform_task_id'], unique=True)


def downgrade():
    op.drop_index('uq_fulfillment_platform_task', table_name='fulfillment_batches')
    op.drop_column("fulfillment_batches", "execution")
    op.drop_column("fulfillment_batches", "platform_task_id")
