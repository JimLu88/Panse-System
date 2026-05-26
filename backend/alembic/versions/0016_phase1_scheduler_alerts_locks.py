"""Phase 1: scheduled_job_runs + alerts + inventory_lock_ledger + product.priority + material.lead_time_days

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 定时任务执行日志
    op.create_table(
        "scheduled_job_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("job_label", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("detail", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("result_summary", sa.JSON),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scheduled_job_runs_job_id", "scheduled_job_runs", ["job_id"])
    op.create_index("ix_scheduled_job_runs_status", "scheduled_job_runs", ["status"])
    op.create_index("ix_scheduled_job_runs_job_status", "scheduled_job_runs", ["job_id", "status"])

    # 2. 告警 / 通知中心
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("dedupe_key", sa.String(128)),
        sa.Column("related_url", sa.String(256)),
        sa.Column("context_json", sa.JSON),
        sa.Column("sticky", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.String(64)),
        sa.Column("auto_resolve_until", sa.DateTime(timezone=True)),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alerts_kind", "alerts", ["kind"])
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_dedupe_key", "alerts", ["dedupe_key"])
    op.create_index("ix_alerts_resolved_at", "alerts", ["resolved_at"])
    op.create_index("ix_alerts_kind_resolved", "alerts", ["kind", "resolved_at"])
    op.create_index("ix_alerts_dedupe_resolved", "alerts", ["dedupe_key", "resolved_at"])

    # 3. 库存锁定 ledger
    op.create_table(
        "inventory_lock_ledger",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer),
        sa.Column("material_code", sa.String(64)),
        sa.Column("product_code", sa.String(64)),
        sa.Column("sku_code", sa.String(64)),
        sa.Column("warehouse", sa.String(64), server_default="default"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("actor", sa.String(64)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inventory_lock_source_kind", "inventory_lock_ledger", ["source_kind"])
    op.create_index("ix_inventory_lock_source_id", "inventory_lock_ledger", ["source_id"])
    op.create_index("ix_inventory_lock_material_code", "inventory_lock_ledger", ["material_code"])
    op.create_index("ix_inventory_lock_product_code", "inventory_lock_ledger", ["product_code"])
    op.create_index("ix_inventory_lock_kind", "inventory_lock_ledger", ["kind"])
    op.create_index("ix_inventory_lock_source", "inventory_lock_ledger",
                    ["source_kind", "source_id"])
    op.create_index("ix_inventory_lock_material_kind", "inventory_lock_ledger",
                    ["material_code", "kind"])

    # 4. Product/Material 加 priority + lead_time_days (Phase 1 准备好, Phase 4 用)
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("priority", sa.String(8),
                                      server_default="mid", nullable=False))
        # high / mid / low
    with op.batch_alter_table("materials") as batch_op:
        batch_op.add_column(sa.Column("lead_time_days", sa.Integer,
                                      server_default="0", nullable=False))
        # 物料补货周期天数 (开料 + 物流), 智能提前备货倒推用
        batch_op.add_column(sa.Column("priority", sa.String(8),
                                      server_default="mid", nullable=False))

    # 5. orders 加 is_historical (功能: 数据丢失水位线之前的影子单)
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("is_historical", sa.Boolean,
                                      server_default=sa.text("false"), nullable=False))
        batch_op.add_column(sa.Column("activate_at", sa.DateTime(timezone=True)))
        # 远期订单激活时间; NULL = 立即生效
        batch_op.add_column(sa.Column("last_outbound_at", sa.DateTime(timezone=True)))

    # 6. factory_orders 加 cancelled_reason / voided_at (功能 11: 作废)
    with op.batch_alter_table("factory_orders") as batch_op:
        batch_op.add_column(sa.Column("voided_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("voided_reason", sa.Text))
        batch_op.add_column(sa.Column("source_order_id", sa.Integer))
        # 关联回 platform_order


def downgrade() -> None:
    with op.batch_alter_table("factory_orders") as batch_op:
        batch_op.drop_column("source_order_id")
        batch_op.drop_column("voided_reason")
        batch_op.drop_column("voided_at")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("last_outbound_at")
        batch_op.drop_column("activate_at")
        batch_op.drop_column("is_historical")
    with op.batch_alter_table("materials") as batch_op:
        batch_op.drop_column("priority")
        batch_op.drop_column("lead_time_days")
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("priority")
    op.drop_table("inventory_lock_ledger")
    op.drop_table("alerts")
    op.drop_table("scheduled_job_runs")
