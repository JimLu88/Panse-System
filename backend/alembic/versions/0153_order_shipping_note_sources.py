"""Separate platform remark tags from confirmed month-granular shipping facts."""
from alembic import op
import sqlalchemy as sa
revision = '0153'
down_revision = '0152'
branch_labels = None
depends_on = None

def upgrade():
    for column in (
        sa.Column('platform_remark_tags', sa.Text(), nullable=True),
        sa.Column('platform_remark_tags_source', sa.String(128), nullable=True),
        sa.Column('platform_remark_tags_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('customer_shipping_month', sa.String(7), nullable=True),
        sa.Column('customer_shipping_month_source', sa.String(128), nullable=True),
        sa.Column('customer_shipping_month_confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('customer_shipping_preserve_production', sa.Boolean(), server_default=sa.false(), nullable=False),
    ):
        op.add_column('orders', column)

def downgrade():
    raise RuntimeError('Shipping confirmations must be preserved; use compatible code rollback, not destructive schema downgrade.')
