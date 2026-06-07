"""账户余额加「统计日期」as_of_date — 余额是某天的快照, 新鲜度按此算而非入库时间。

幂等: 列已存在则跳过。

Revision ID: 0059
Revises: 0058
"""
import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("account_balances")}
    if "as_of_date" not in cols:
        op.add_column("account_balances", sa.Column("as_of_date", sa.Date(), nullable=True))
        op.create_index("ix_account_balances_as_of_date", "account_balances", ["as_of_date"])


def downgrade() -> None:
    op.drop_index("ix_account_balances_as_of_date", table_name="account_balances")
    op.drop_column("account_balances", "as_of_date")
