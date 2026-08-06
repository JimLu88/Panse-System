"""新增采购桌面执行器租约、平台回执与心跳状态。

Revision ID: 0135
Revises: 0134
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0135"
down_revision = "0134"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_inquiries" in tables:
        columns = _columns("procurement_inquiries")
        additions = (
            ("lease_token", sa.String(length=64)),
            ("leased_by", sa.String(length=64)),
            ("lease_until", sa.DateTime(timezone=True)),
            ("execution_attempts", sa.Integer(), False, "0"),
            ("external_thread_id", sa.String(length=255)),
            ("external_message_id", sa.String(length=255)),
            ("last_execution_error", sa.Text()),
            ("last_observed_at", sa.DateTime(timezone=True)),
            ("last_executor_mode", sa.String(length=16)),
        )
        for item in additions:
            name, column_type = item[0], item[1]
            if name in columns:
                continue
            nullable = item[2] if len(item) > 2 else True
            server_default = item[3] if len(item) > 3 else None
            op.add_column(
                "procurement_inquiries",
                sa.Column(
                    name,
                    column_type,
                    nullable=nullable,
                    server_default=server_default,
                ),
            )
        op.create_index(
            "ix_procurement_inquiries_lease_token",
            "procurement_inquiries",
            ["lease_token"],
            unique=True,
        )
        op.create_index(
            "ix_procurement_inquiries_leased_by",
            "procurement_inquiries",
            ["leased_by"],
        )
        op.create_index(
            "ix_procurement_inquiries_lease_until",
            "procurement_inquiries",
            ["lease_until"],
        )
        op.create_index(
            "ix_procurement_inquiries_external_thread_id",
            "procurement_inquiries",
            ["external_thread_id"],
        )
        op.create_index(
            "ix_procurement_inquiries_external_message_id",
            "procurement_inquiries",
            ["external_message_id"],
        )

    if "procurement_messages" in tables:
        columns = _columns("procurement_messages")
        if "external_message_id" not in columns:
            op.add_column(
                "procurement_messages",
                sa.Column("external_message_id", sa.String(length=255)),
            )
        op.create_unique_constraint(
            "uq_procurement_message_external",
            "procurement_messages",
            ["inquiry_id", "direction", "external_message_id"],
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_agent_states" not in tables:
        op.create_table(
            "procurement_agent_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("agent_id", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=128)),
            sa.Column("host_label", sa.String(length=128)),
            sa.Column("version", sa.String(length=32)),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="dry_run"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="online"),
            sa.Column("capabilities", sa.JSON(), nullable=False),
            sa.Column(
                "current_inquiry_id",
                sa.Integer(),
                sa.ForeignKey("procurement_inquiries.id", ondelete="SET NULL"),
            ),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_error", sa.Text()),
            sa.Column("counters", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("agent_id"),
        )
        op.create_index(
            "ix_procurement_agent_states_agent_id",
            "procurement_agent_states",
            ["agent_id"],
            unique=True,
        )
        op.create_index(
            "ix_procurement_agent_states_current_inquiry_id",
            "procurement_agent_states",
            ["current_inquiry_id"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_agent_states" in tables:
        op.drop_table("procurement_agent_states")
    if "procurement_messages" in tables:
        columns = _columns("procurement_messages")
        if "external_message_id" in columns:
            op.drop_constraint(
                "uq_procurement_message_external",
                "procurement_messages",
                type_="unique",
            )
            op.drop_column("procurement_messages", "external_message_id")
    if "procurement_inquiries" in tables:
        columns = _columns("procurement_inquiries")
        for name in (
            "last_executor_mode",
            "last_observed_at",
            "last_execution_error",
            "external_message_id",
            "external_thread_id",
            "execution_attempts",
            "lease_until",
            "leased_by",
            "lease_token",
        ):
            if name in columns:
                op.drop_column("procurement_inquiries", name)
