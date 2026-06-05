"""users.must_change_password — 强制首次登录改密 (默认 admin/admin).

幂等: 列已存在则跳过 (生产库可能已先行有此列, 直接 ADD 会撞 DuplicateColumn)。

Revision ID: 0048
Revises: 0047
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("users")}
    if "must_change_password" not in cols:
        op.add_column(
            "users",
            sa.Column("must_change_password", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )


def downgrade():
    op.drop_column("users", "must_change_password")
