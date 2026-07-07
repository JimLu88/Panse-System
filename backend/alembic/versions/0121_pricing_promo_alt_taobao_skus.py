"""一码多SKU: pricing_sku_promo 加 alt_taobao_sku_ids (JSON list)。

同一商家编码在淘宝挂的其它 SKUID(主 = taobao_sku_id)。纯加列, 幂等, nullable, 不动现有数据。
导出「大促报名 / 单品立减」时: 主 SKUID + 每个 alt 各出一行, 同价。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    try:
        return any(c["name"] == col for c in insp.get_columns(table))
    except Exception:
        return False


def upgrade() -> None:
    if not _has_column("pricing_sku_promo", "alt_taobao_sku_ids"):
        op.add_column("pricing_sku_promo", sa.Column("alt_taobao_sku_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column("pricing_sku_promo", "alt_taobao_sku_ids"):
        op.drop_column("pricing_sku_promo", "alt_taobao_sku_ids")
