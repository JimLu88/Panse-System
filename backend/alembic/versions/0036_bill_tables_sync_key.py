"""给 3 张账单表加 sync_key 飞书同步配对键 + 回填存量

这三张表 (refill_records / wanshifu_bills / logistics_bills) 原来用自增 id 做主键,
两端 id 对不上, 不能作为飞书同步配对键。这里加一个由业务字段拼成的稳定 sync_key,
并回填已有数据 (规则与 app/models/finance.py 的事件钩子一致)。

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("refill_records", "wanshifu_bills", "logistics_bills"):
        op.add_column(table, sa.Column("sync_key", sa.String(length=160), nullable=True))
        op.create_index(f"ix_{table}_sync_key", table, ["sync_key"])

    bind = op.get_bind()

    # 回填: 与 finance.py 中 _refill/_wanshifu/_logistics_sync_key 保持一致
    def d(v):
        return v.isoformat()[:10] if v is not None else ""

    rows = bind.execute(sa.text(
        "SELECT id, order_no, refill_date, sku, qty FROM refill_records")).fetchall()
    for r in rows:
        key = "refill:" + ":".join([
            str(r.order_no or "").strip(), d(r.refill_date),
            str(r.sku or "").strip(), str(r.qty if r.qty is not None else "")])
        bind.execute(sa.text("UPDATE refill_records SET sync_key=:k WHERE id=:i"),
                     {"k": key, "i": r.id})

    rows = bind.execute(sa.text(
        "SELECT id, order_no, bill_date, service_type, amount FROM wanshifu_bills")).fetchall()
    for r in rows:
        key = "wsf:" + ":".join([
            str(r.order_no or "").strip(), d(r.bill_date),
            str(r.service_type or "").strip(),
            str(r.amount if r.amount is not None else "")])
        bind.execute(sa.text("UPDATE wanshifu_bills SET sync_key=:k WHERE id=:i"),
                     {"k": key, "i": r.id})

    rows = bind.execute(sa.text(
        "SELECT id, tracking_no, order_no, bill_date, carrier, freight_amount FROM logistics_bills")).fetchall()
    for r in rows:
        if r.tracking_no:
            key = "log:" + str(r.tracking_no).strip()
        else:
            key = "log:" + ":".join([
                str(r.order_no or "").strip(), d(r.bill_date),
                str(r.carrier or "").strip(),
                str(r.freight_amount if r.freight_amount is not None else "")])
        bind.execute(sa.text("UPDATE logistics_bills SET sync_key=:k WHERE id=:i"),
                     {"k": key, "i": r.id})


def downgrade() -> None:
    for table in ("logistics_bills", "wanshifu_bills", "refill_records"):
        op.drop_index(f"ix_{table}_sync_key", table_name=table)
        op.drop_column(table, "sync_key")
