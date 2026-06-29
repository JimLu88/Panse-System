"""所有 alipay_flow_no String(64) → String(128) (修 09:40 自动对账截断, 用户 2026-06-29)。

支付宝退款流水号 transaction_no 可达 74 字符(如 2026...1428211271*F-refundplatform3-R-...),
源列 AlipayFlow.transaction_no 本就是 String(128); 但各业务表把它复制进 alipay_flow_no 列时仍是
varchar(64) → alipay_flow_router 建售后(after_sales)/backfill 回填订单时报 StringDataRightTruncation,
daily_0940_alipay_match 每天 fail。把所有 alipay_flow_no varchar(<128) 统一放宽到 128(与源列一致)。
动态扫 information_schema 覆盖全部表(after_sales/orders/refill/supplier_payments/...)防漏。
additive 扩容, 不丢数据; varchar 增长在 Postgres 是元数据变更, 保留索引。

Revision ID: 0101
Revises: 0100
"""
import sqlalchemy as sa
from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return  # SQLite(测试) 不限制 varchar 长度, 模型已声明 128, 无需迁移
    rows = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE column_name = 'alipay_flow_no' "
        "AND data_type IN ('character varying', 'varchar') "
        "AND (character_maximum_length IS NULL OR character_maximum_length < 128)"
    )).fetchall()
    for (tbl,) in rows:
        op.alter_column(tbl, "alipay_flow_no",
                        existing_type=sa.String(64), type_=sa.String(128),
                        existing_nullable=True)


def downgrade() -> None:
    # 收窄到 64 会截断已存的长退款流水号, 故 no-op (不主动回退, 避免丢数据)。
    pass
