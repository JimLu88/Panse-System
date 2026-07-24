"""蜜蜡色榉木样块轮换到新 skuId，并隔离旧 skuId 的平台历史线。

Revision ID: 0132
Revises: 0131
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None

_SKU_CODE = "PPS2398001060614"
_NEW_SKU_ID = "6282622238127"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "pricing_sku" in tables:
        bind.execute(sa.text(
            """
            UPDATE pricing_sku
               SET is_custom_placeholder = false,
                   list_price = 40,
                   daily_price = 30
             WHERE sku_code = :sku_code
            """
        ), {"sku_code": _SKU_CODE})
    if "pricing_sku_promo" in tables:
        bind.execute(sa.text(
            """
            UPDATE pricing_sku_promo
               SET taobao_sku_id = :new_sku_id,
                   alt_taobao_sku_ids = NULL,
                   taobao_activity_price = 30,
                   mid_buyer_price = 21.02,
                   big_buyer_price = 20.41,
                   enrolled_floor_price = 30,
                   coupon_floor_price = 21.02
             WHERE sku_code = :sku_code
            """
        ), {"sku_code": _SKU_CODE, "new_sku_id": _NEW_SKU_ID})


def downgrade() -> None:
    # 已轮换 skuId 及其平台历史线不能安全回退到旧物理槽位。
    pass
