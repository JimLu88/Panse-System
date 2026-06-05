"""导入字段补全 — BOM 备注改 TEXT + 8 张表新增导入字段 (全部 nullable, 幂等).

修复:
  1) BOM 备注(尺寸/工艺说明)超 255 字符 → StringDataRightTruncation → 整表导入失败;
  2) 成品/配件库存、订单、工厂单、补单、支付宝流水、售后、工厂对账 多个字段无存储列,
     导入时被静默丢弃。本迁移补齐存储列, 配合 excel_schemas 别名后即可导入。

全部新列 nullable、向后兼容; 幂等 (列已存在则跳过), Postgres 与 SQLite 通用。

Revision ID: 0051
Revises: 0050
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


# (表, 列名, 类型) — 全部 nullable
_ADD_COLUMNS = [
    # 4a 成品库存
    ("product_inventory", "product_name", sa.String(255)),
    ("product_inventory", "last_inbound_at", sa.Date()),
    ("product_inventory", "last_outbound_at", sa.Date()),
    ("product_inventory", "avg_daily_sales", sa.Numeric(12, 3)),
    ("product_inventory", "stock_status", sa.String(32)),
    ("product_inventory", "stock_alert_status", sa.String(32)),
    ("product_inventory", "slow_moving_status", sa.String(32)),
    ("product_inventory", "auto_restock_qty", sa.Numeric(14, 3)),
    # 4b 配件库存
    ("part_inventory", "lead_time_days", sa.Integer()),
    ("part_inventory", "slow_moving_days", sa.Integer()),
    ("part_inventory", "avg_daily_sales", sa.Numeric(12, 3)),
    ("part_inventory", "stock_status", sa.String(32)),
    ("part_inventory", "stock_alert_status", sa.String(32)),
    ("part_inventory", "slow_moving_status", sa.String(32)),
    ("part_inventory", "auto_restock_qty", sa.Numeric(14, 3)),
    # 5 订单总表
    ("orders", "order_profit", sa.Numeric(12, 2)),
    ("orders", "lock_status", sa.String(32)),
    # 6 工厂下单表
    ("factory_orders", "product_name", sa.String(255)),
    # 8 补单记录
    ("refill_records", "supplier_payment", sa.Numeric(12, 2)),
    ("refill_records", "alipay_flow_no", sa.String(64)),
    ("refill_records", "tracking_no", sa.String(128)),
    ("refill_records", "fee_remark", sa.Text()),
    # 9 支付宝流水 — 平台订单号 (爱群号等会把多笔订单号拼一格, 实测达 279 字符 → Text 不限长)
    ("alipay_flows", "platform_order_no", sa.Text()),
    # 18 售后表
    ("after_sales", "taobao_backend_note", sa.Text()),
    # 11 工厂对账
    ("factory_reconciliations", "billing_period", sa.String(64)),
]


def _has_column(table: str, col: str) -> bool:
    return col in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for table, col, type_ in _ADD_COLUMNS:
        if not _has_column(table, col):
            op.add_column(table, sa.Column(col, type_, nullable=True))
    # BOM 备注: VARCHAR(255) → TEXT (长尺寸/工艺说明)。仅 PG 需要; SQLite 动态类型无所谓。
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("bom_lines", "remark", type_=sa.Text(), existing_nullable=True)


def downgrade():
    for table, col, _type in reversed(_ADD_COLUMNS):
        if _has_column(table, col):
            op.drop_column(table, col)
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("bom_lines", "remark", type_=sa.String(255), existing_nullable=True)
