# -*- coding: utf-8 -*-
"""packing_total_mismatch 自愈复核 (用户 2026-06-29: 改账期/删行后异常成僵尸 → 补 recheck)。

复核口径: 账期当前无打包行 → 销账; |本子合计 − 当前系统应付| ≤ 0.5 → 销账; 否则返回当前真实差。
全合成数据, 不碰生产。
"""
from decimal import Decimal

from app.models.exception import DataException
from app.models.finance import PackingBill
from app.services import exception_recheck_service as rk


def _row(db, month, name, fee, *, excluded=False):
    db.add(PackingBill(bill_month=month, customer_name=name,
                       packing_fee=Decimal(str(fee)), excluded=excluded))
    db.flush()


def _exc(db, month, declared, payable):
    ex = DataException(source_table="packing_bills", source_pk=month,
                       exception_type="packing_total_mismatch", severity="warning",
                       status="open", description="x",
                       context={"bill_month": month, "declared_total": declared,
                                "payable_total": payable, "diff": declared - payable})
    db.add(ex); db.flush()
    return ex


def test_recheck_clears_when_reconciled(db_session):
    """本子合计 = 当前应付 → recheck 返回 None (可销账)。"""
    _row(db_session, "2026-03", "甲", 100)
    _row(db_session, "2026-03", "乙", 200)
    ex = _exc(db_session, "2026-03", declared=300, payable=999)  # 旧记录payable过时
    assert rk.recheck(db_session, ex) is None   # 现应付300 = 本子300


def test_recheck_clears_when_month_emptied(db_session):
    """账期已无任何打包行(账册被挪走/删空) → 销账。"""
    ex = _exc(db_session, "2026-09", declared=300, payable=300)  # 该账期无行
    assert rk.recheck(db_session, ex) is None


def test_recheck_keeps_real_mismatch(db_session):
    """本子合计与当前应付仍差 → 返回原因 (不误关真不平)。"""
    _row(db_session, "2026-04", "甲", 100)
    ex = _exc(db_session, "2026-04", declared=500, payable=500)
    reason = rk.recheck(db_session, ex)
    assert reason and "仍差" in reason


def test_recheck_ignores_excluded_in_payable(db_session):
    """剔除行不计入应付: 本子300 vs 应付(剔除后)300 → 销账。"""
    _row(db_session, "2026-05", "甲", 300)
    _row(db_session, "2026-05", "改客户", 50, excluded=True)
    ex = _exc(db_session, "2026-05", declared=300, payable=350)
    assert rk.recheck(db_session, ex) is None


def test_bulk_close_resolved_closes_packing(db_session):
    """bulk_close_resolved 据 recheck 把已对平的打包异常置 resolved。"""
    _row(db_session, "2026-03", "甲", 300)
    ex = _exc(db_session, "2026-03", declared=300, payable=840)
    closed = rk.bulk_close_resolved(db_session, types=["packing_total_mismatch"])
    assert closed.get("packing_total_mismatch") == 1
    assert ex.status == "resolved"
