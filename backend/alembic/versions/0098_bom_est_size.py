"""bom_lines 加 est_size + size_status (BOM 尺寸 AI 推演; 用户 2026-06-28, 配件 epic 阶段1)。

很多 BOM 行的尺寸在 remark 自由文本里、或干脆缺失(尤其岩板/玻璃/桌面等面积料)。
按 SKU 尺寸 AI 推演补一个尺寸 → 写 est_size 标 size_status='inferred'(预估, 可人工编辑);
人工编辑后置 'confirmed'(确认值, 前端二次确认)。**不动原 remark**(不污染真实数据);
计算用 remark 优先、缺则用 est_size。多单按 BOM 用量占比分摊成本时用此尺寸。

Revision ID: 0098
Revises: 0097
"""
import sqlalchemy as sa
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 推演/确认的尺寸串(如 "1800*800" / "长2.1米"); 不覆盖原 remark
    op.add_column("bom_lines", sa.Column("est_size", sa.String(128), nullable=True))
    # 'inferred'=AI预估(可改) | 'confirmed'=人工确认(二次确认过) | NULL=未推演
    op.add_column("bom_lines", sa.Column("size_status", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("bom_lines", "size_status")
    op.drop_column("bom_lines", "est_size")
