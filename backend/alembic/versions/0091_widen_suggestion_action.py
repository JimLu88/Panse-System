"""data_exceptions.suggestion_action varchar(64) → TEXT。

修 factory_bill_on_dead_order 等数据质量扫描器: 建议文案 >64 字符时 INSERT 报
StringDataRightTruncation, 异常静默写不进库 (2026-06-23 用户发现)。

Revision ID: 0091
Revises: 0090
"""
import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 仅 Postgres 把 varchar(64) 改 TEXT; 已是 TEXT 则等价无害(幂等)。
    # sqlite(测试库)类型无长度约束, 跳过, 避免 batch-alter 复杂度。
    if bind.dialect.name == "postgresql":
        op.alter_column("data_exceptions", "suggestion_action",
                        type_=sa.Text(), existing_type=sa.String(64),
                        existing_nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        try:
            op.alter_column("data_exceptions", "suggestion_action",
                            type_=sa.String(64), existing_type=sa.Text(),
                            existing_nullable=True)
        except Exception:  # noqa: BLE001 — 已有 >64 字符数据时不强行截断
            pass
