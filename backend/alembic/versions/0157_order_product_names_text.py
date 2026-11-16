"""Keep complete marketplace titles across parent, child and factory orders."""
from alembic import op
import sqlalchemy as sa
revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None
TABLES = ("orders", "order_details", "factory_orders")
def upgrade():
    for table in TABLES:
        op.alter_column(table, "product_name", existing_type=sa.String(255), type_=sa.Text(), existing_nullable=True)
def downgrade():
    # Refuse rollback if it would truncate a title. Data must never be shortened.
    connection = op.get_bind()
    for table in TABLES:
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table} WHERE length(product_name)>255")).scalar()
        if count:
            raise RuntimeError("Cannot narrow product_name while complete long titles exist")
    for table in TABLES:
        op.alter_column(table, "product_name", existing_type=sa.Text(), type_=sa.String(255), existing_nullable=True)
