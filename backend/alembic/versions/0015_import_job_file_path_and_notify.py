"""import_jobs.file_path + notify settings

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # file_path: 大文件先落盘 /tmp/panse_import/<job_id>.xlsx, worker 读文件而非内存,
    # 这样多 worker (N=2) 同时跑也不会一人吃 200MB 内存
    op.add_column("import_jobs", sa.Column("file_path", sa.Text, nullable=True))
    # cancel_requested: 不强 kill, 让 worker 在下一次 progress tick 自检退出
    op.add_column("import_jobs",
                  sa.Column("cancel_requested", sa.Boolean,
                            server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    op.drop_column("import_jobs", "cancel_requested")
    op.drop_column("import_jobs", "file_path")
