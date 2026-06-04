"""订单配件清单 (order_accessory_items) — 每单 BOM AC-* 物料采购 & 物流追踪

Revision ID: 0042
Revises: 0041
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_accessory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("order_no", sa.String(64), nullable=False, index=True),
        sa.Column("material_code", sa.String(32), nullable=False, index=True),
        sa.Column("material_name", sa.String(255), nullable=True),
        sa.Column("qty_required", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("is_factory_provided", sa.Boolean(), nullable=False, server_default="false"),
        # bom = BOM 自动带出; 客户备注 = 截图 OCR 备注识别的新增配件
        sa.Column("source", sa.String(16), nullable=False, server_default="bom"),
        # 未采购 / 已下单 / 运输中 / 已到货 / 工厂提供
        sa.Column("status", sa.String(32), nullable=False, server_default="未采购"),
        sa.Column("tracking_no", sa.String(128), nullable=True),
        sa.Column("carrier_code", sa.String(64), nullable=True),   # 快递100 承运商代码
        sa.Column("carrier_name", sa.String(64), nullable=True),   # 显示名 顺丰/中通...
        sa.Column("tracking_events", sa.JSON(), nullable=True),    # 缓存物流时间线
        sa.Column("tracking_last_status", sa.String(255), nullable=True),
        sa.Column("tracking_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("part_purchase_id", sa.Integer(), sa.ForeignKey("part_purchases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("alert_level", sa.String(16), nullable=True),    # None / warn / critical
        sa.Column("alert_reason", sa.String(255), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("order_id", "material_code", name="uq_order_accessory_item"),
    )


def downgrade() -> None:
    op.drop_table("order_accessory_items")
