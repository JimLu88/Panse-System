"""pricing_sku_promo.enrolled_floor_price — 校验期已生效活动价(普惠券后价底线参考)

淘宝规则: 活动券后价不得高于校验期内最低普惠券后价 → 已生效的上一场活动价就是硬底。
第一场活动导出(已报商品列表)按 SKUID 导入此列; 占位SKU报名价封顶到它, 预检超线红字预警。
(2026-07-12 用户: 第二场超级88导入 62 件全失败, 元凶=定制占位SKU报价高于第一场已生效价)

Revision ID: 0125
Revises: 0124
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    from sqlalchemy import inspect
    return any(c["name"] == col for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("pricing_sku_promo", "enrolled_floor_price"):
        op.add_column("pricing_sku_promo",
                      sa.Column("enrolled_floor_price", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    if _has_column("pricing_sku_promo", "enrolled_floor_price"):
        op.drop_column("pricing_sku_promo", "enrolled_floor_price")
