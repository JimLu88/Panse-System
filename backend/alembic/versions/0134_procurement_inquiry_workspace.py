"""新增智能采购询价任务、商家队列与消息审计表。

Revision ID: 0134
Revises: 0133
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0134"
down_revision = "0133s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_tasks" not in tables:
        op.create_table(
            "procurement_tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_no", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=128), nullable=False),
            sa.Column("category", sa.String(length=24), nullable=False, server_default="daily"),
            sa.Column("item_name", sa.String(length=128), nullable=False),
            sa.Column("specification", sa.String(length=255)),
            sa.Column("quantity", sa.Numeric(12, 4), nullable=False, server_default="1"),
            sa.Column("unit", sa.String(length=16), nullable=False, server_default="件"),
            sa.Column("target_unit_price", sa.Numeric(14, 4)),
            sa.Column("requirements", sa.Text()),
            sa.Column("execution_mode", sa.String(length=16), nullable=False, server_default="assisted"),
            sa.Column("taobao_client_mode", sa.String(length=16), nullable=False, server_default="desktop"),
            sa.Column("channels", sa.JSON(), nullable=False),
            sa.Column("channel_daily_limits", sa.JSON(), nullable=False),
            sa.Column("followup_intervals_hours", sa.JSON(), nullable=False),
            sa.Column("planned_merchant_count", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("max_followup_rounds", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("ab_test_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ab_test_sample_size", sa.Integer(), nullable=False, server_default="6"),
            sa.Column("script_a", sa.Text()),
            sa.Column("script_b", sa.Text()),
            sa.Column("winning_variant", sa.String(length=8)),
            sa.Column("ai_model", sa.String(length=128)),
            sa.Column("ai_suggestion_note", sa.Text()),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.String(length=64)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("task_no"),
        )
        op.create_index("ix_procurement_tasks_task_no", "procurement_tasks", ["task_no"])
        op.create_index("ix_procurement_tasks_category", "procurement_tasks", ["category"])
        op.create_index("ix_procurement_tasks_status", "procurement_tasks", ["status"])
        op.create_index(
            "ix_procurement_tasks_status_created",
            "procurement_tasks",
            ["status", "created_at"],
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_inquiries" not in tables:
        op.create_table(
            "procurement_inquiries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "task_id",
                sa.Integer(),
                sa.ForeignKey("procurement_tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("slot_no", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=24), nullable=False),
            sa.Column("merchant_name", sa.String(length=128)),
            sa.Column("merchant_url", sa.String(length=1024)),
            sa.Column("product_url", sa.String(length=1024)),
            sa.Column("message_variant", sa.String(length=16), nullable=False, server_default="winner_pending"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
            sa.Column("followup_round", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_sent_at", sa.DateTime(timezone=True)),
            sa.Column("first_response_at", sa.DateTime(timezone=True)),
            sa.Column("last_message_at", sa.DateTime(timezone=True)),
            sa.Column("next_followup_at", sa.DateTime(timezone=True)),
            sa.Column("last_outbound_message", sa.Text()),
            sa.Column("last_inbound_message", sa.Text()),
            sa.Column("requires_wechat", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("wechat_contact", sa.String(length=128)),
            sa.Column("manual_reason", sa.String(length=255)),
            sa.Column("quote_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("quote_amount", sa.Numeric(14, 4)),
            sa.Column("normalized_unit_price", sa.Numeric(14, 4)),
            sa.Column("quote_payload", sa.JSON(), nullable=False),
            sa.Column("response_quality", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_procurement_inquiries_task_id", "procurement_inquiries", ["task_id"])
        op.create_index("ix_procurement_inquiries_channel", "procurement_inquiries", ["channel"])
        op.create_index("ix_procurement_inquiries_message_variant", "procurement_inquiries", ["message_variant"])
        op.create_index("ix_procurement_inquiries_status", "procurement_inquiries", ["status"])
        op.create_index("ix_procurement_inquiries_next_followup_at", "procurement_inquiries", ["next_followup_at"])
        op.create_index(
            "ix_procurement_inquiries_task_slot",
            "procurement_inquiries",
            ["task_id", "slot_no"],
            unique=True,
        )
        op.create_index(
            "ix_procurement_inquiries_due",
            "procurement_inquiries",
            ["status", "next_followup_at"],
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_messages" not in tables:
        op.create_table(
            "procurement_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "inquiry_id",
                sa.Integer(),
                sa.ForeignKey("procurement_inquiries.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("direction", sa.String(length=16), nullable=False),
            sa.Column("round_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("is_ai_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("requires_manual_review", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("event_at", sa.DateTime(timezone=True)),
            sa.Column("message_meta", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_procurement_messages_inquiry_id", "procurement_messages", ["inquiry_id"])
        op.create_index("ix_procurement_messages_direction", "procurement_messages", ["direction"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("procurement_messages", "procurement_inquiries", "procurement_tasks"):
        if table in tables:
            op.drop_table(table)
