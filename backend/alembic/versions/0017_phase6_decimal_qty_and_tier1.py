"""Phase 6: 库存 qty → Decimal + Tier 1 业务表 (会计期间 / 订单事件 / 供应商评分)

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. PartInventory qty 从 Integer → Numeric(14,3)
    with op.batch_alter_table("part_inventory") as batch_op:
        batch_op.alter_column(
            "physical_qty",
            existing_type=sa.Integer(),
            type_=sa.Numeric(14, 3),
            existing_nullable=False,
            existing_server_default="0",
        )
        batch_op.alter_column(
            "locked_qty",
            existing_type=sa.Integer(),
            type_=sa.Numeric(14, 3),
            existing_nullable=False,
            existing_server_default="0",
        )
        batch_op.alter_column(
            "safety_stock",
            existing_type=sa.Integer(),
            type_=sa.Numeric(14, 3),
            existing_nullable=True,
        )
    with op.batch_alter_table("product_inventory") as batch_op:
        batch_op.alter_column(
            "physical_qty",
            existing_type=sa.Integer(),
            type_=sa.Numeric(14, 3),
            existing_nullable=False,
            existing_server_default="0",
        )
        batch_op.alter_column(
            "locked_qty",
            existing_type=sa.Integer(),
            type_=sa.Numeric(14, 3),
            existing_nullable=False,
            existing_server_default="0",
        )

    # 2. Tier 1 - 会计期间表 (业务: 关闭月份后不能改)
    op.create_table(
        "accounting_periods",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        # open / closed / locked
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by", sa.String(64)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("year", "month", name="uq_accounting_periods_ym"),
    )

    # 3. Tier 1 - 订单事件 (订单时间轴, 全审计 trail 统一入口)
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, nullable=False, index=True),
        sa.Column("kind", sa.String(48), nullable=False),
        # status_change / factory_order_generated / inventory_locked / inventory_released /
        # shipped / signed / refund_requested / aftersales_inbound / comment / system
        sa.Column("actor", sa.String(64)),
        sa.Column("summary", sa.String(256), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("context_json", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])
    op.create_index("ix_order_events_kind", "order_events", ["kind"])

    # 4. Tier 1 - 供应商评分快照
    op.create_table(
        "supplier_scores",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("supplier_id", sa.Integer, nullable=False, index=True),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("on_time_rate", sa.Numeric(5, 2)),         # 0-1
        sa.Column("return_rate", sa.Numeric(5, 2)),
        sa.Column("price_variance_pct", sa.Numeric(8, 2)),    # 与上月相比
        sa.Column("total_orders", sa.Integer, server_default="0"),
        sa.Column("total_amount", sa.Numeric(14, 2)),
        sa.Column("score", sa.Numeric(5, 2)),                # 综合 0-100
        sa.Column("rank", sa.Integer),
        sa.Column("detail_json", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("supplier_id", "year", "month",
                            name="uq_supplier_scores_sym"),
    )

    # 5. Tier 1 - AI 每日经营简报缓存
    op.create_table(
        "daily_briefings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("for_date", sa.Date, nullable=False, unique=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("highlights_json", sa.JSON),
        sa.Column("model", sa.String(64)),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # 6. Material - 增加停产 + 备用供应商
    with op.batch_alter_table("materials") as batch_op:
        batch_op.add_column(sa.Column("is_discontinued", sa.Boolean,
                                      server_default=sa.text("false"), nullable=False))
        batch_op.add_column(sa.Column("primary_supplier_id", sa.Integer))
        batch_op.add_column(sa.Column("alt_supplier_ids", sa.JSON))


def downgrade() -> None:
    with op.batch_alter_table("materials") as batch_op:
        batch_op.drop_column("alt_supplier_ids")
        batch_op.drop_column("primary_supplier_id")
        batch_op.drop_column("is_discontinued")
    op.drop_table("daily_briefings")
    op.drop_table("supplier_scores")
    op.drop_table("order_events")
    op.drop_table("accounting_periods")
    with op.batch_alter_table("product_inventory") as batch_op:
        batch_op.alter_column("locked_qty", type_=sa.Integer())
        batch_op.alter_column("physical_qty", type_=sa.Integer())
    with op.batch_alter_table("part_inventory") as batch_op:
        batch_op.alter_column("safety_stock", type_=sa.Integer())
        batch_op.alter_column("locked_qty", type_=sa.Integer())
        batch_op.alter_column("physical_qty", type_=sa.Integer())
