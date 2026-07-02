"""products 加 R5 半成品/白坯 打标字段 (默认关, 功能开关打开后才用)。

semi_finished_eligible (bool, 默认 false) = 该产品可用白坯前段生产;
semi_group (str) = 共享同一白坯的分组码, 供池化归集备货量。纯追加列, 幂等。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("products", "semi_finished_eligible"):
        op.add_column("products", sa.Column(
            "semi_finished_eligible", sa.Boolean(), nullable=False, server_default="false"))
    if not _has_column("products", "semi_group"):
        op.add_column("products", sa.Column("semi_group", sa.String(64), nullable=True))
        op.create_index("ix_products_semi_group", "products", ["semi_group"])


def downgrade() -> None:
    if _has_column("products", "semi_group"):
        op.drop_index("ix_products_semi_group", "products")
        op.drop_column("products", "semi_group")
    if _has_column("products", "semi_finished_eligible"):
        op.drop_column("products", "semi_finished_eligible")
