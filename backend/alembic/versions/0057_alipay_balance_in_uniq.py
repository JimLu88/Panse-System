"""把 balance 纳入支付宝流水唯一约束 (同号多笔不同余额的真实扣费都要入库).

业务: 同一交易流水号可能出现 2~3 次真实扣费 (类型/金额相同, 仅交易后余额不同)。
原四元组 (account, transaction_no, transaction_type, amount) 会把它们当成重复压成一条。
改为五元组 (..., balance) — 每笔交易后余额不同即视为不同流水, 全部入库。

幂等 (DROP IF EXISTS + CREATE); 仅 Postgres 执行 (SQLite/测试走 create_all, 模型已含 balance)。

Revision ID: 0057
Revises: 0056
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_NAME = "uq_alipay_flow_acct_no"
_COLS_NEW = ["account", "transaction_no", "transaction_type", "amount", "balance"]
_COLS_OLD = ["account", "transaction_no", "transaction_type", "amount"]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE alipay_flows DROP CONSTRAINT IF EXISTS {_NAME}')
    op.create_unique_constraint(_NAME, "alipay_flows", _COLS_NEW)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE alipay_flows DROP CONSTRAINT IF EXISTS {_NAME}')
    op.create_unique_constraint(_NAME, "alipay_flows", _COLS_OLD)
