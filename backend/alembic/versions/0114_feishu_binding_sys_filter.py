"""feishu_table_bindings 加 sys_filter (系统侧行过滤, JSON)。

多个绑定映射同一 system_table 时(如支付宝 5 个账户表都 → alipay_flows), 用 sys_filter
(如 {"account":"企业号"}) 让每张飞书表只同步匹配的行。纯追加列, 幂等, 不动现有数据。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("feishu_table_bindings", "sys_filter"):
        op.add_column("feishu_table_bindings", sa.Column("sys_filter", sa.String(255), nullable=True))


def downgrade() -> None:
    if _has_column("feishu_table_bindings", "sys_filter"):
        op.drop_column("feishu_table_bindings", "sys_filter")
