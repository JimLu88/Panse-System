"""orders 补全订单总表财务列 — 买家应付/店铺实收/税费/其它费用/总成本 +
售后费用冗余列 (好评返/二次维修/返厂运费/工厂补偿/物流补偿/补偿总额) + 退款状态/金额/日期.

Revision ID: 0046
Revises: 0045
"""
from alembic import op
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


# (列名, 类型) — 全部 nullable, 向后兼容
_COLUMNS = [
    ("buyer_payable_amount", sa.Numeric(12, 2)),    # 买家应付金额
    ("shop_received_amount", sa.Numeric(12, 2)),    # 店铺实收金额
    ("tax", sa.Numeric(12, 2)),                     # 税费
    ("other_fee", sa.Numeric(12, 2)),               # 其它费用
    ("total_cost", sa.Numeric(12, 2)),              # 总成本
    ("good_review_refund", sa.Numeric(12, 2)),      # 好评/差价返
    ("second_visit_fee", sa.Numeric(12, 2)),        # 二次上门维修费
    ("return_pack_freight", sa.Numeric(12, 2)),     # 返厂打包运费
    ("factory_compensation", sa.Numeric(12, 2)),    # 工厂补偿
    ("logistics_compensation", sa.Numeric(12, 2)),  # 物流补偿
    ("compensation_total", sa.Numeric(12, 2)),      # 补偿总金额
    ("refund_status", sa.String(32)),               # 退款状态
    ("refund_amount", sa.Numeric(12, 2)),           # 退款金额
    ("refund_date", sa.Date()),                     # 退款日期
]


def upgrade():
    for name, type_ in _COLUMNS:
        op.add_column("orders", sa.Column(name, type_, nullable=True))
    # refill_records: schema 早有 remark 字段 (备注/补单状态), 但表缺列 —— 含备注的补单行
    # 过去会让 RefillRecord(**payload) 抛 TypeError 整行失败。补列修复。
    op.add_column("refill_records", sa.Column("remark", sa.String(255), nullable=True))
    # part_purchases: 新增配件采购导入, 补 remark 列让 备注 不丢。
    op.add_column("part_purchases", sa.Column("remark", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("part_purchases", "remark")
    op.drop_column("refill_records", "remark")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("orders", name)
