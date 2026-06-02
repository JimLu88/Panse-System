"""放宽支付宝流水唯一约束: 支持同号配对流水 (分账 + 在线支付)

同一笔淘宝收款, 支付宝会产生两条共用同一「交易流水号」的流水:
  - 在线支付: 客户实付货款 (正, 本店收入)
  - 分账    : 淘宝支付手续费 (负, 约千分之六)
原唯一约束 (account, transaction_no) 会把这对流水判成重复, 导入时静默丢弃其一,
造成收入或手续费缺失、账对不平。改为 (account, transaction_no, transaction_type, amount):
  - 同号不同类型/不同金额 → 视为不同流水, 都可入库 (正常配对)
  - 同号 + 同类型 + 同金额 → 仍视为真重复, 去重
真正的「同号同类型」疑似重复由 data_quality_service.scan_alipay_duplicate_flow 捞成异常。

Revision ID: 0039
Revises: 0038
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_alipay_flow_acct_no", "alipay_flows", type_="unique")
    op.create_unique_constraint(
        "uq_alipay_flow_acct_no",
        "alipay_flows",
        ["account", "transaction_no", "transaction_type", "amount"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_alipay_flow_acct_no", "alipay_flows", type_="unique")
    op.create_unique_constraint(
        "uq_alipay_flow_acct_no",
        "alipay_flows",
        ["account", "transaction_no"],
    )
