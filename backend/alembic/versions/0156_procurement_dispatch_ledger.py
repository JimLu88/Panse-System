"""Add paused account bindings and persistent send intents; no data backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0156"
down_revision = "0155"
branch_labels = None
depends_on = None


def _timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def _ref(name, table, kind=sa.Integer, **kwargs):
    return sa.Column(name, kind, sa.ForeignKey(table + ".id", ondelete="RESTRICT"), nullable=False, **kwargs)


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_input_groups" not in existing:
        op.create_table("procurement_input_groups",
            sa.Column("executor_id", sa.String(64), primary_key=True),
            sa.Column("input_group", sa.String(64), primary_key=True),
            sa.Column("revision", sa.Integer, nullable=False, server_default="0"))
    if "procurement_account_bindings" not in existing:
        op.create_table("procurement_account_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("channel", sa.String(24), nullable=False),
            sa.Column("identity_hash", sa.String(64), nullable=False),
            sa.Column("executor_id", sa.String(64), nullable=False),
            sa.Column("input_group", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="paused"),
            sa.Column("new_24h", sa.Integer, nullable=False, server_default="25"),
            sa.Column("new_1h", sa.Integer, nullable=False, server_default="6"),
            sa.Column("messages_24h", sa.Integer, nullable=False, server_default="40"),
            sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
            *_timestamps(),
            sa.UniqueConstraint("channel", "identity_hash", name="uq_procurement_account_identity"),
            sa.CheckConstraint("new_24h BETWEEN 1 AND 25 AND new_1h BETWEEN 1 AND 6 AND messages_24h BETWEEN 1 AND 40", name="ck_procurement_account_caps"))
    if "procurement_task_accounts" not in existing:
        op.create_table("procurement_task_accounts",
            _ref("task_id", "procurement_tasks", primary_key=True),
            sa.Column("channel", sa.String(24), primary_key=True),
            _ref("account_id", "procurement_account_bindings", sa.String(36)))
    if "procurement_contact_claims" not in existing:
        op.create_table("procurement_contact_claims",
            sa.Column("id", sa.String(36), primary_key=True),
            _ref("task_id", "procurement_tasks", index=True),
            _ref("inquiry_id", "procurement_inquiries", unique=True),
            _ref("account_id", "procurement_account_bindings", sa.String(36)),
            sa.Column("merchant_hash", sa.String(64), nullable=False),
            sa.Column("attempted_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("task_id", "merchant_hash", name="uq_procurement_contact_identity"))
    if "procurement_send_intents" not in existing:
        op.create_table("procurement_send_intents",
            sa.Column("id", sa.String(36), primary_key=True),
            _ref("task_id", "procurement_tasks", index=True),
            _ref("inquiry_id", "procurement_inquiries"),
            _ref("account_id", "procurement_account_bindings", sa.String(36), index=True),
            _ref("contact_id", "procurement_contact_claims", sa.String(36)),
            sa.Column("action_key", sa.String(64), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_at", sa.DateTime(timezone=True)),
            sa.Column("permit_hash", sa.String(64)),
            sa.Column("permit_until", sa.DateTime(timezone=True)),
            sa.Column("reconcile_attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("reconcile_started_at", sa.DateTime(timezone=True)),
            sa.Column("external_message_id", sa.String(255)),
            sa.Column("observed_at", sa.DateTime(timezone=True)),
            sa.Column("confirmed_at", sa.DateTime(timezone=True)),
            *_timestamps(),
            sa.UniqueConstraint("task_id", "inquiry_id", "action_key", name="uq_procurement_intent_action"),
            sa.UniqueConstraint("account_id", "external_message_id", name="uq_procurement_intent_receipt"),
            sa.CheckConstraint("reconcile_attempts BETWEEN 0 AND 1", name="ck_procurement_reconcile_once"),
            sa.CheckConstraint("state IN ('reserved','executing','unknown','reconciling','unresolved','confirmed_sent','cancelled')", name="ck_procurement_intent_state"))


def downgrade():
    raise RuntimeError("采购发送证据和未知名额必须保留，禁止删除账本回退")
