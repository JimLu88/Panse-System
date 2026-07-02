"""成品库存流水表 product_stock_movement (R3: 现货自动进出库的审计+幂等账)。

记录 出库(ship)/入库(restock_receipt)/冲正(reversal) 每笔现货增减; 唯一 (reason, entity_type,
entity_id) 保证同一业务事件只落一次。纯建表, 幂等, 不动现有数据。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    if _has_table("product_stock_movement"):
        return
    op.create_table(
        "product_stock_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse", sa.String(64), nullable=False, server_default="default"),
        sa.Column("product_code", sa.String(32), nullable=False),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("reason", "entity_type", "entity_id", name="uq_prod_stock_move_event"),
    )
    op.create_index("ix_prod_stock_move_wh", "product_stock_movement", ["warehouse"])
    op.create_index("ix_prod_stock_move_pc", "product_stock_movement", ["product_code"])
    op.create_index("ix_prod_stock_move_reason", "product_stock_movement", ["reason"])
    op.create_index("ix_prod_stock_move_entity", "product_stock_movement", ["entity_id"])


def downgrade() -> None:
    if _has_table("product_stock_movement"):
        op.drop_table("product_stock_movement")
