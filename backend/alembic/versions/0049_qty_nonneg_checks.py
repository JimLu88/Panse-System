"""qty >= 0 完整性约束 (优化 #5).

用 PG 的 NOT VALID: 只校验今后新增/更新的行, 不回头校验存量数据 (避免历史脏数据
导致迁移失败)。仅在 Postgres 执行; SQLite (测试 create_all) 由模型层 CheckConstraint 覆盖。

幂等: 约束已存在则跳过 (避免重复 ADD CONSTRAINT 报错)。

Revision ID: 0049
Revises: 0048
"""
from alembic import op
from sqlalchemy import inspect

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_CHECKS = [
    ("orders", "ck_orders_qty_nonneg", "qty >= 0"),
    ("factory_orders", "ck_factory_orders_qty_nonneg", "qty >= 0"),
    ("refill_records", "ck_refill_records_qty_nonneg", "qty >= 0"),
]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, name, expr in _CHECKS:
        existing = {c["name"] for c in inspect(bind).get_check_constraints(table)}
        if name not in existing:
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, name, _expr in _CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
