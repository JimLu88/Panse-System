"""pricing_sku_promo.coupon_floor_price — 校验期最低【普惠券后价】历史线(平台回执学来的真线)

与 enrolled_floor_price 的区别 (2026-07-16 报名价重构, 别混用):
  - enrolled_floor_price = 上一场已生效【活动价】(报名价维度) → 封顶新【报名价】;
  - coupon_floor_price   = 平台校验期内最低【普惠券后价】(到手维度) → 封顶新【名义券后】,
    即 报名价 P ≤ coupon_floor ÷ (1−官方比例)。
根因: 旧「叠加法」下真实到手 = 活动价×(1−比例) − 单品立减, 比"老活动价"低整整一刀立减,
故只封顶老活动价挡不住真线 → 2026-07-16 88VIP 60品报名 42 失败(142行券后线)的机制根因。
数据来源: 千牛报名结果表/批量操作反馈里平台点名的"最低普惠券后价"。只降不抬(线只会更低)。

Revision ID: 0126
Revises: 0125
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    from sqlalchemy import inspect
    return any(c["name"] == col for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("pricing_sku_promo", "coupon_floor_price"):
        op.add_column("pricing_sku_promo",
                      sa.Column("coupon_floor_price", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    if _has_column("pricing_sku_promo", "coupon_floor_price"):
        op.drop_column("pricing_sku_promo", "coupon_floor_price")
