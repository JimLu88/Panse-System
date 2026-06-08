"""导入原始文件归档表 imported_files (每次导入的表格/图片留档, 可回溯)。

幂等: 表已存在则跳过。

Revision ID: 0060
Revises: 0059
"""
import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "imported_files" in insp.get_table_names():
        return
    op.create_table(
        "imported_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("stored_path", sa.Text, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="web"),
        sa.Column("import_job_id", sa.Integer, nullable=True),
        sa.Column("row_summary", sa.JSON, nullable=True),
        sa.Column("uploaded_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_imported_files_kind", "imported_files", ["kind"])
    op.create_index("ix_imported_files_file_hash", "imported_files", ["file_hash"])
    op.create_index("ix_imported_files_import_job_id", "imported_files", ["import_job_id"])


def downgrade() -> None:
    op.drop_table("imported_files")
