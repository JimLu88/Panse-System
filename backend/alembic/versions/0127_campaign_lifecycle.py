"""活动生命周期系统三表 (2026-07-17 spec: docs/活动生命周期系统_执行plan.md §五)。

- campaign_plans         活动计划 (类型/档期精确到秒/千牛标题/状态机 draft→…→alarmed)
- campaign_recon_reports 核对报告 (每次核对一行: 汇总+逐SKU JSON, >2元报警计数)
- campaign_calendar      活动日历 (每日发现的千牛活动 + 提醒去重日期)

纯建表, 幂等, 不动现有数据、不碰 pricing_sku / pricing_sku_promo。

Revision ID: 0127
Revises: 0126
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    if not _has_table("campaign_plans"):
        op.create_table(
            "campaign_plans",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("campaign_type", sa.String(32), nullable=False),
            sa.Column("tier", sa.String(16), nullable=False, server_default="big"),
            sa.Column("start_at", sa.DateTime(), nullable=True),   # 档期精确到秒
            sa.Column("end_at", sa.DateTime(), nullable=True),
            sa.Column("qn_campaign_title", sa.String(255), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_campaign_plans_campaign_type", "campaign_plans", ["campaign_type"])
        op.create_index("ix_campaign_plans_status", "campaign_plans", ["status"])

    if not _has_table("campaign_recon_reports"):
        op.create_table(
            "campaign_recon_reports",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("rows", sa.JSON(), nullable=True),
            sa.Column("alarm_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_campaign_recon_reports_plan_id", "campaign_recon_reports", ["plan_id"])

    if not _has_table("campaign_calendar"):
        op.create_table(
            "campaign_calendar",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("start_at", sa.DateTime(), nullable=True),
            sa.Column("end_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(32), nullable=True),
            sa.Column("source", sa.String(16), nullable=False, server_default="discovery"),
            sa.Column("last_notified_on", sa.Date(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("title", "start_at", name="uq_campaign_calendar_title_start"),
        )
        op.create_index("ix_campaign_calendar_title", "campaign_calendar", ["title"])


def downgrade() -> None:
    for name in ("campaign_calendar", "campaign_recon_reports", "campaign_plans"):
        if _has_table(name):
            op.drop_table(name)
