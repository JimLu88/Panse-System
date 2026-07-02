"""放宽 feishu_sync_map.system_pk 到 255。

支付宝配对键 sync_key(形如 alipay:企业号:交易号:交易类型:金额:余额)会 >64 字符,
原 varchar(64) 装不下 → INSERT feishu_sync_map 报 StringDataRightTruncation,
导致支付宝 5 张表全部同步失败。幂等: 已是 255 则跳过。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def _col_len(table: str, col: str):
    insp = sa.inspect(op.get_bind())
    for c in insp.get_columns(table):
        if c["name"] == col:
            return getattr(c["type"], "length", None)
    return None


def upgrade() -> None:
    if _col_len("feishu_sync_map", "system_pk") != 255:
        op.alter_column(
            "feishu_sync_map", "system_pk",
            existing_type=sa.String(64), type_=sa.String(255),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.alter_column(
        "feishu_sync_map", "system_pk",
        existing_type=sa.String(255), type_=sa.String(64),
        existing_nullable=False,
    )
