"""master data hub fields + order dual sign-off

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Product: master data fields
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("image_url", sa.String(512), nullable=True))
        batch.add_column(sa.Column("custom_scope", sa.Text, nullable=True))
        batch.add_column(sa.Column("size_detail", sa.Text, nullable=True))
        batch.add_column(sa.Column("aux_material", sa.Text, nullable=True))
        batch.add_column(sa.Column("description", sa.Text, nullable=True))

    # Material: area fields for micro-customization price calc
    with op.batch_alter_table("materials") as batch:
        batch.add_column(sa.Column("area", sa.Numeric(10, 4), nullable=True))
        batch.add_column(sa.Column("width_mm", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("height_mm", sa.Numeric(10, 2), nullable=True))

    # Order: dual sign-off fields
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("tracking_confirmed", sa.Boolean, nullable=False, server_default="0"))
        batch.add_column(sa.Column("manual_confirmed", sa.Boolean, nullable=False, server_default="0"))
        batch.add_column(sa.Column("signoff_questioned", sa.Boolean, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("signoff_questioned")
        batch.drop_column("manual_confirmed")
        batch.drop_column("tracking_confirmed")

    with op.batch_alter_table("materials") as batch:
        batch.drop_column("height_mm")
        batch.drop_column("width_mm")
        batch.drop_column("area")

    with op.batch_alter_table("products") as batch:
        batch.drop_column("description")
        batch.drop_column("aux_material")
        batch.drop_column("size_detail")
        batch.drop_column("custom_scope")
        batch.drop_column("image_url")
