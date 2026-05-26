"""feishu binding: allow one system_table to bind multiple feishu tables

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 原列以 unique=True 创建, Postgres 默认约束名 {table}_{column}_key。
    # 用 IF EXISTS 保证不同环境(命名可能不同/已被手动删过)都不会硬失败。
    op.execute(
        "ALTER TABLE feishu_table_bindings "
        "DROP CONSTRAINT IF EXISTS feishu_table_bindings_system_table_key"
    )
    op.create_unique_constraint(
        "uq_feishu_binding_table_pair",
        "feishu_table_bindings",
        ["system_table", "feishu_table_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_feishu_binding_table_pair", "feishu_table_bindings", type_="unique"
    )
    op.create_unique_constraint(
        "feishu_table_bindings_system_table_key",
        "feishu_table_bindings",
        ["system_table"],
    )
