"""配件采购发票文件表 + PartPurchase OCR 关联字段

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchase_files_period", "purchase_files", ["year", "month"])

    with op.batch_alter_table("part_purchases") as batch_op:
        batch_op.add_column(sa.Column("source_file_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ocr_warnings", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("ocr_model", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_part_purchases_source_file", "purchase_files", ["source_file_id"], ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("part_purchases") as batch_op:
        batch_op.drop_constraint("fk_part_purchases_source_file", type_="foreignkey")
        batch_op.drop_column("ocr_model")
        batch_op.drop_column("ocr_warnings")
        batch_op.drop_column("source_file_id")

    op.drop_index("ix_purchase_files_period", table_name="purchase_files")
    op.drop_table("purchase_files")
