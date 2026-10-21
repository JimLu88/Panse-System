"""Independent human forwarding ledger; no production status backfill."""
from alembic import op
import sqlalchemy as sa
revision = '0155'
down_revision = '0154'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('factory_forwards',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer()),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('actor', sa.String(160)),
        sa.Column('changed_at', sa.DateTime(timezone=True)),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('image_message_id', sa.String(128)),
        sa.Column('card_message_id', sa.String(128)),
        sa.Column('card_state', sa.String(24), nullable=False),
        sa.Column('sync_pending', sa.Boolean(), nullable=False),
        sa.Column('sync_attempts', sa.Integer(), nullable=False),
        sa.Column('sync_error', sa.String(160)))
    op.create_index('ix_factory_forwards_order_id', 'factory_forwards', ['order_id'])


def downgrade():
    raise RuntimeError('Keep human forwarding history on compatible-code rollback.')
