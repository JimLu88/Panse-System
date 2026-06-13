"""wanshifu_orders 万师傅安装订单档案 (38列订单导出格式, 含客户信息+订单配对结果)。

Revision ID: 0079
Revises: 0078
"""
import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "wanshifu_orders" in insp.get_table_names():
        return
    op.create_table(
        "wanshifu_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wsf_order_no", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("service_type", sa.String(64)),
        sa.Column("status", sa.String(64)),
        sa.Column("product_category", sa.String(64)),
        sa.Column("product_model", sa.String(255)),
        sa.Column("customer_name", sa.String(64), index=True),
        sa.Column("customer_phone", sa.String(32)),
        sa.Column("province", sa.String(32)),
        sa.Column("city", sa.String(32)),
        sa.Column("district", sa.String(32)),
        sa.Column("address", sa.String(255)),
        sa.Column("net_amount", sa.Numeric(12, 2)),
        sa.Column("service_fee", sa.Numeric(12, 2)),
        sa.Column("created_time", sa.DateTime(timezone=True), index=True),
        sa.Column("finished_time", sa.DateTime(timezone=True)),
        sa.Column("tracking_company", sa.String(64)),
        sa.Column("tracking_no", sa.String(128)),
        sa.Column("source_shop", sa.String(128)),
        sa.Column("matched_order_no", sa.String(64), index=True),
        sa.Column("match_method", sa.String(32)),
        sa.Column("match_note", sa.Text()),
        sa.Column("remark", sa.Text()),
        sa.Column("import_job_id", sa.Integer(),
                  sa.ForeignKey("import_jobs.id", ondelete="SET NULL"), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("wanshifu_orders")
