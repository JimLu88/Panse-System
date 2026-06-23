"""factory_recon_items 加 source_file_hash (无订单号备货行按整份文件去重)。

修工厂对账导入去重 bug: 旧 _dedup_key 对无订单号的备货行(同价同品)按内容去重 → 同一张表里
多张一模一样的备货被误删(玉山博冠少 2 张备货, 当时手工补录未根治)。方案A(2026-06-24 用户拍板):
备货行改按"整份文件 sha256"去重 —— 同一份文件重导才判重、同表多张相同备货全保留。

Revision ID: 0092
Revises: 0091
"""
import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "factory_recon_items",
        sa.Column("source_file_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_factory_recon_items_source_file_hash",
        "factory_recon_items", ["source_file_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_factory_recon_items_source_file_hash",
                  table_name="factory_recon_items")
    op.drop_column("factory_recon_items", "source_file_hash")
