"""修正蜜蜡色榉木样块的活动定价分类。

Revision ID: 0131
Revises: 0130
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None

_SKU_CODE = "PPS2398001060614"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "pricing_sku" in tables:
        bind.execute(sa.text(
            """
            UPDATE pricing_sku
               SET is_custom_placeholder = false,
                   list_price = COALESCE(list_price, 40)
             WHERE sku_code = :sku_code
            """
        ), {"sku_code": _SKU_CODE})
    if "pricing_sku_promo" in tables:
        bind.execute(sa.text(
            """
            UPDATE pricing_sku_promo
               SET taobao_activity_price = 25,
                   mid_buyer_price = 21.02,
                   big_buyer_price = 20.41
             WHERE sku_code = :sku_code
            """
        ), {"sku_code": _SKU_CODE})


def downgrade() -> None:
    # 这是已确认的业务数据纠错；降级代码版本时也不能把真实商品重新标成定制占位。
    pass
