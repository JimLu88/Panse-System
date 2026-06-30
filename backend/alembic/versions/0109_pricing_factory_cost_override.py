"""定价表 pricing_sku 加 factory_cost_override (工厂成本手动覆盖标记)。

回填: 现存 factory_cost != 木作+包装+外配件 的行 = 历史手改/导入覆盖 → 标 True 保住, 不被自动派生冲掉。

Revision ID: 0109
Revises: 0108
"""
import sqlalchemy as sa
from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if "pricing_sku" not in sa.inspect(bind).get_table_names():
        return
    if not _has_column(bind, "pricing_sku", "factory_cost_override"):
        op.add_column("pricing_sku", sa.Column(
            "factory_cost_override", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    # 回填: 工厂成本 != 木作+包装+外配件 的行视为手动覆盖, 标 True (保住既有手改值)
    op.execute(
        "UPDATE pricing_sku SET factory_cost_override = true "
        "WHERE factory_cost IS NOT NULL AND "
        "COALESCE(wood_cost,0)+COALESCE(packaging_cost,0)+COALESCE(external_parts_cost,0) <> factory_cost"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "pricing_sku" in sa.inspect(bind).get_table_names() and _has_column(bind, "pricing_sku", "factory_cost_override"):
        op.drop_column("pricing_sku", "factory_cost_override")
