"""半成品/白坯库存表 semi_finished_inventory (R5, 功能开关打开后才用)。

按 semi_group 记 现有白坯 on_hand_qty + 在产白坯 in_production_qty, 供半成品备货计划算池化缺口。
纯建表, 幂等, 不动现有数据。默认关闭功能时此表可为空。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    if _has_table("semi_finished_inventory"):
        return
    op.create_table(
        "semi_finished_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("semi_group", sa.String(64), nullable=False),
        sa.Column("warehouse", sa.String(64), nullable=False, server_default="default"),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("on_hand_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("in_production_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("semi_group", name="uq_semi_finished_group"),
    )
    op.create_index("ix_semi_finished_group", "semi_finished_inventory", ["semi_group"])


def downgrade() -> None:
    if _has_table("semi_finished_inventory"):
        op.drop_table("semi_finished_inventory")
