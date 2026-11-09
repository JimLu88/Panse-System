"""Persist buyer-confirmed receipt separately from logistics signoff."""
from alembic import op
import sqlalchemy as sa

revision = '0156'
down_revision = '0155'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('orders', sa.Column('customer_confirmed_date', sa.Date(), nullable=True))


def downgrade():
    op.drop_column('orders', 'customer_confirmed_date')
