"""定制占位符: pricing_sku 加 is_custom_placeholder (bool)。

淘宝"微定制/材质定制/尺寸定制/差价/追加配件"等占位链接SKU的标记。仅用于淘宝活动报名(导出活动价=现价×0.9),
不参与产品成本/利润/对账计算。纯加列, 幂等, 现有行 server_default false。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    try:
        return any(c["name"] == col for c in insp.get_columns(table))
    except Exception:
        return False


def upgrade() -> None:
    if not _has_column("pricing_sku", "is_custom_placeholder"):
        op.add_column("pricing_sku", sa.Column(
            "is_custom_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if _has_column("pricing_sku", "is_custom_placeholder"):
        op.drop_column("pricing_sku", "is_custom_placeholder")
