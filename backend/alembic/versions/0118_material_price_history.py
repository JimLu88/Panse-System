"""物料价格历史 material_price_history (按生效日版本化, 用户 2026-07-03)。

改物料价记一行(生效日); 成本按订单 order_date 取当时生效价 → 改价前订单用旧价、改价后用新价。
纯建表, 幂等。种子(每物料当前价 effective_from=基线) 由 material_price_service.seed_baseline 单独跑。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    if _has_table("material_price_history"):
        return
    op.create_table(
        "material_price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_code", sa.String(32), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_mat_price_hist_code", "material_price_history", ["material_code"])
    op.create_index("ix_mat_price_hist_eff", "material_price_history", ["effective_from"])


def downgrade() -> None:
    if _has_table("material_price_history"):
        op.drop_table("material_price_history")
