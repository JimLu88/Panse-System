"""评价资产台账 review_assets (Plan1 v2): 带图好评资产台账 + 折叠倒计时。

纯建表, 幂等, 不动现有数据。补单=刷单口径走 source 字段; review_date 可空(pending_review);
fold_due_date/status 建索引供 daily_0900_review_asset_remind 高效扫描。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    if _has_table("review_assets"):
        return
    op.create_table(
        "review_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=False),
        sa.Column("shop", sa.String(32), nullable=True),
        sa.Column("product_code", sa.String(32), nullable=True),
        sa.Column("sku_name", sa.String(128), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("review_text", sa.Text(), nullable=True),
        sa.Column("fold_due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_review"),
        sa.Column("source", sa.String(16), nullable=False, server_default="refill"),
        sa.Column("screenshot_file_id", sa.Integer(), sa.ForeignKey("imported_files.id"), nullable=True),
        sa.Column("last_notified_date", sa.Date(), nullable=True),
        sa.Column("last_notified_level", sa.String(16), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_review_assets_order_no", "review_assets", ["order_no"])
    op.create_index("ix_review_assets_order_id", "review_assets", ["order_id"])
    op.create_index("ix_review_assets_product_code", "review_assets", ["product_code"])
    op.create_index("ix_review_assets_review_date", "review_assets", ["review_date"])
    op.create_index("ix_review_assets_fold_due_date", "review_assets", ["fold_due_date"])
    op.create_index("ix_review_assets_status", "review_assets", ["status"])
    op.create_index("ix_review_assets_shop", "review_assets", ["shop"])


def downgrade() -> None:
    if _has_table("review_assets"):
        op.drop_table("review_assets")
