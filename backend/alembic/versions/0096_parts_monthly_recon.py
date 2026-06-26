"""parts_monthly_recon — 配件「工厂月度对账」总额录入 (用户 2026-06-26)。

大宗配件厂全是当地小厂、手写单, 无法逐笔配单 → 改按月对账: 我方导出当月「已发货」订单清单
(按发货日期, 100% 真实消耗了配件)给工厂, 工厂哪怕不逐单也会返一个月度总额。此表存这些总额
(每材料每月可多个供应商各一行)。bulk_material_recon 以它求和作「实际」列, 与预估/历史平均并排对比。

Revision ID: 0096
Revises: 0095
"""
import sqlalchemy as sa
from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parts_monthly_recon",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("material_key", sa.String(32), nullable=False, index=True),   # BULK_MATERIALS.key
        sa.Column("year_month", sa.String(7), nullable=False, index=True),       # 'YYYY-MM' (发货月)
        sa.Column("supplier", sa.String(128), nullable=True),                    # 哪家工厂(可多家)
        sa.Column("actual_total", sa.Numeric(12, 2), nullable=False),            # 工厂返回的当月总额
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_parts_monthly_recon_mk_ym", "parts_monthly_recon", ["material_key", "year_month"])


def downgrade() -> None:
    op.drop_index("ix_parts_monthly_recon_mk_ym", table_name="parts_monthly_recon")
    op.drop_table("parts_monthly_recon")
