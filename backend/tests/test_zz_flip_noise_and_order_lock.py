# -*- coding: utf-8 -*-
"""flip 僵尸报警三修 (用户 2026-07-13 "没有问题的你直接处理销账"):

1. 修改档案数值等价不记 ('0.00'→'0' 曾被记成变化);
2. 翻烧饼检测器: 数值归一+连续同值去重, 同值重写不算回跳;
3. 订单人工锁: 人裁定过财务/状态的单(source=manual/web), 重导不覆盖这些列。
"""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.field_change import FieldChange
from app.models.order import Order
from app.services import field_change_service as fcs
from app.services import import_flip_monitor_service as flipmon
from app.services import taobao_order_import as tio


# ── 1) 审计: 数值等价不记 ────────────────────────────────────────────────
def test_record_skips_numeric_equal(db_session):
    fcs.record(db_session, table="orders", pk="N1", field="refund_amount",
               old=Decimal("0.00"), new=0, actor="订单重导", source="import")
    fcs.record(db_session, table="orders", pk="N1", field="paid_amount",
               old="42.40", new="42.4", actor="订单重导", source="import")
    assert db_session.query(FieldChange).filter_by(row_pk="N1").count() == 0
    fcs.record(db_session, table="orders", pk="N1", field="refund_amount",
               old=Decimal("0.00"), new=Decimal("42.40"), actor="订单重导", source="import")
    assert db_session.query(FieldChange).filter_by(row_pk="N1").count() == 1   # 真变化照记


# ── 2) 检测器: 同值重写不算回跳 ──────────────────────────────────────────
def _fc(db, no, field, new, days_ago=0.0):
    db.add(FieldChange(table_name="orders", row_pk=no, field=field,
                       old_value="x", new_value=str(new), actor="订单重导", source="import",
                       created_at=datetime.now().astimezone() - timedelta(days=days_ago)))
    db.flush()


def test_flip_fields_ignores_same_value_rewrites(db_session):
    """'0.00'→'0'→'0' 这类同值重写序列 → 不算震荡; A→B→A 才算。"""
    _fc(db_session, "F1", "refund_amount", "0.00", 1.0)
    _fc(db_session, "F1", "refund_amount", "0", 0.5)
    _fc(db_session, "F1", "refund_amount", "0", 0.1)
    db_session.commit()
    assert flipmon._flip_fields(db_session, "F1", 3) == {}

    _fc(db_session, "F2", "paid_amount", "2829.50", 1.2)
    _fc(db_session, "F2", "paid_amount", "2871.90", 0.8)
    _fc(db_session, "F2", "paid_amount", "2829.50", 0.2)   # 真回跳 A→B→A
    db_session.commit()
    assert "paid_amount" in flipmon._flip_fields(db_session, "F2", 3)


# ── 3) 订单人工锁: 重导不覆盖人裁定的财务/状态 ────────────────────────────
def _sales_csv() -> bytes:
    rows = [
        ["子订单编号", "主订单编号", "标题", "价格", "购买数量", "外部系统编号",
         "商品属性", "订单状态", "商家编码", "买家应付货款", "退款状态", "退款金额", "订单创建时间"],
        ["B200", "B200", "畔色岩板餐桌", "5100.00", "1", "23210020201",
         "颜色分类：砂白色2.0米岩板餐桌", "交易成功", "23210020201", "2933.78",
         "没有申请退款", "无退款申请", "2026-01-18 13:39:27"],
    ]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("gbk")


def test_reimport_respects_human_lock(db_session):
    """人裁定过(source=manual)的单: 重导后 状态/实付/应付 原样; 无锁对照单照常刷新。"""
    db_session.add(Order(platform="淘宝", order_no="B200", status="paid",
                         paid_amount=Decimal("4500.36"), refund_amount=Decimal("1795.17")))
    db_session.commit()
    fcs.record(db_session, table="orders", pk="B200", field="paid_amount",
               old="2705.19", new="4500.36", actor="用户裁定", source="manual")
    db_session.commit()
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _sales_csv())
    assert rep.updated == 1
    o = db_session.query(Order).filter_by(order_no="B200").one()
    assert o.status == "paid"                             # 没被刷成 signed
    assert o.paid_amount == Decimal("4500.36")            # 人拍板的实付纹丝不动
    assert o.refund_amount == Decimal("1795.17")
    assert o.buyer_payable_amount is None                 # 应付也没被导入覆盖


def test_refund_recon_dateless_bucket_not_alarmed(db_session):
    """缺退款日期的应退 → '(无日期)' 桶降级 not_available, 不记异常(复位退款额后不造新僵尸)。"""
    from app.services import reconciliation_service as rec
    db_session.add(Order(platform="淘宝", order_no="ND1",
                         refund_amount=Decimal("1795.17"), refund_date=None))
    db_session.commit()
    res = rec.run_refund_reconciliation(db_session, record_exceptions=True)
    nd = next((d for d in res.diffs if d.key == "(无日期)"), None)
    assert nd is not None
    assert nd.severity == "not_available"
    from app.models.exception import DataException
    assert db_session.query(DataException).filter(
        DataException.source_pk.like("refund_reconciliation:%无日期%")).count() == 0


def test_reimport_without_lock_still_updates(db_session):
    """对照: 无人工档案的单, 重导照常刷新(既有幂等行为不回退)。"""
    db_session.add(Order(platform="淘宝", order_no="B200", status="pending_payment"))
    db_session.commit()
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _sales_csv())
    assert rep.updated == 1
    o = db_session.query(Order).filter_by(order_no="B200").one()
    assert o.status == "signed"
    assert o.buyer_payable_amount == Decimal("2933.78")
