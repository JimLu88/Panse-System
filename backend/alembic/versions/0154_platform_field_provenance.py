"""Add nullable provenance only; never clear/backfill historical field values."""
from alembic import op
import sqlalchemy as sa
revision = '0154'
down_revision = '0153'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('orders', sa.Column('platform_field_state', sa.JSON(), nullable=True))

def downgrade():
    raise RuntimeError('Keep provenance for safe compatible-code rollback; no destructive downgrade.')
