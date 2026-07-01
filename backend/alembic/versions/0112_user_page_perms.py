"""子账号页面权限: users.page_perms (JSON, 可空).

None = 不受限 (admin / 主账号 / 存量账号一律全看); list[str] = 仅可见列出的页面 permKey。
纯追加列, 幂等 (重复升级不报错), 不动任何现有数据。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("users", "page_perms"):
        op.add_column("users", sa.Column("page_perms", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "page_perms"):
        op.drop_column("users", "page_perms")
