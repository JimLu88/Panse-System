"""支付宝流水 ↔ 供应商送货单 自动对账."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.finance import AlipayFlow
from app.models.supplier import DeliveryNote, Supplier
from app.services import supplier_payment_matcher as spm


def _mk_supplier(db, name="X木业", keywords=None, supplier_type="woodwork"):
    s = Supplier(
        name=name, supplier_type=supplier_type,
        alipay_counterparty_keywords=keywords if keywords is not None else [name],
    )
    db.add(s); db.flush()
    return s


def _mk_note(db, supplier_id, *, amount, on_date, status="confirmed", note_no=None):
    n = DeliveryNote(
        supplier_id=supplier_id,
        note_no=note_no or f"N-{supplier_id}-{on_date.isoformat()}-{amount}",
        delivery_date=on_date,
        total_amount=Decimal(str(amount)),
        status=status,
    )
    db.add(n); db.flush()
    return n


def _mk_flow(db, *, amount, on_time=None, counterparty="X木业有限公司",
             account="企业号", tx_no=None, rec_type="factory_payment",
             rec_status="open"):
    f = AlipayFlow(
        account=account,
        transaction_no=tx_no or f"TX{datetime.now().timestamp():.0f}",
        transaction_time=on_time or datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc),
        transaction_type="转账",
        counterparty=counterparty,
        amount=Decimal(str(amount)),  # 支出请传负数
        reconciliation_type=rec_type,
        reconciliation_status=rec_status,
    )
    db.add(f); db.flush()
    return f


def test_no_suppliers_returns_empty(db_session):
    _mk_flow(db_session, amount=-100)
    result = spm.reconcile(db_session)
    assert result.scanned == 0
    assert result.matches == []


def test_skips_income_flows(db_session):
    _mk_supplier(db_session)
    _mk_flow(db_session, amount=+500)  # 收入
    result = spm.reconcile(db_session)
    assert result.skipped == 1
    assert result.matches[0].decision == "skipped"


def test_no_supplier_match_when_counterparty_doesnt_hit(db_session):
    _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_flow(db_session, amount=-100, counterparty="完全无关的对手方")
    result = spm.reconcile(db_session)
    assert result.no_supplier == 1
    assert result.matches[0].decision == "no_supplier"
    assert result.matches[0].supplier_id is None


def test_keyword_substring_match(db_session):
    """counterparty 'X木业有限公司' 命中关键字 'X木业'."""
    s = _mk_supplier(db_session, name="木作工厂", keywords=["X木业"])
    _mk_flow(db_session, amount=-500, counterparty="X木业有限公司")
    _mk_note(db_session, s.id, amount=500, on_date=date(2026, 5, 12))
    result = spm.reconcile(db_session)
    assert result.matched_count == 1
    assert result.matches[0].supplier_id == s.id


def test_keyword_longest_wins(db_session):
    """两家关键字都能命中 → 选最长的那家."""
    s1 = _mk_supplier(db_session, name="A", keywords=["木业"])
    s2 = _mk_supplier(db_session, name="B", keywords=["佛山X木业"])
    _mk_flow(db_session, amount=-300, counterparty="佛山X木业有限公司")
    _mk_note(db_session, s1.id, amount=300, on_date=date(2026, 5, 12))
    _mk_note(db_session, s2.id, amount=300, on_date=date(2026, 5, 12))
    result = spm.reconcile(db_session)
    assert result.matches[0].supplier_id == s2.id


def test_exact_single_match_marks_paid(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    n = _mk_note(db_session, s.id, amount=580, on_date=date(2026, 5, 14), note_no="N-001")
    f = _mk_flow(db_session, amount=-580, counterparty="X木业",
                 tx_no="TX001")
    result = spm.reconcile(db_session)
    assert result.matched_count == 1
    m = result.matches[0]
    assert m.decision == "exact"
    assert m.matched_note_ids == [n.id]
    assert m.matched_note_nos == ["N-001"]
    # 落盘验证
    db_session.refresh(n); db_session.refresh(f)
    assert n.status == "paid"
    assert n.alipay_flow_no == "TX001"
    assert n.paid_at is not None
    assert f.reconciliation_status == "matched"
    assert "N-001" in (f.related_order_no or "")


def test_dry_run_does_not_persist(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    n = _mk_note(db_session, s.id, amount=580, on_date=date(2026, 5, 14))
    f = _mk_flow(db_session, amount=-580)
    result = spm.reconcile(db_session, dry_run=True)
    assert result.matched_count == 1
    db_session.refresh(n); db_session.refresh(f)
    assert n.status == "confirmed"
    assert f.reconciliation_status == "open"


def test_ambiguous_exact_same_amount_needs_review(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    n1 = _mk_note(db_session, s.id, amount=500, on_date=date(2026, 5, 10), note_no="A")
    n2 = _mk_note(db_session, s.id, amount=500, on_date=date(2026, 5, 12), note_no="B")
    f = _mk_flow(db_session, amount=-500)
    result = spm.reconcile(db_session)
    m = result.matches[0]
    assert m.decision == "needs_review"
    assert set(m.matched_note_ids) == {n1.id, n2.id}
    # 不应该落盘
    db_session.refresh(n1); db_session.refresh(n2); db_session.refresh(f)
    assert n1.status == "confirmed"
    assert n2.status == "confirmed"
    assert f.reconciliation_status == "open"


def test_combo_subset_sum_matches_single_combination(db_session):
    """3 张单据合并付款: 200 + 300 + 480 = 980, 流水 -980 → combo 匹配."""
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    n1 = _mk_note(db_session, s.id, amount=200, on_date=date(2026, 5, 10))
    n2 = _mk_note(db_session, s.id, amount=300, on_date=date(2026, 5, 11))
    n3 = _mk_note(db_session, s.id, amount=480, on_date=date(2026, 5, 12))
    f = _mk_flow(db_session, amount=-980)
    result = spm.reconcile(db_session)
    m = result.matches[0]
    assert m.decision == "combo"
    assert set(m.matched_note_ids) == {n1.id, n2.id, n3.id}
    # 落盘
    db_session.refresh(n1); db_session.refresh(n2); db_session.refresh(n3); db_session.refresh(f)
    assert n1.status == "paid" and n2.status == "paid" and n3.status == "paid"
    assert f.reconciliation_status == "matched"


def test_combo_two_valid_combinations_needs_review(db_session):
    """200+300=500 和 500=500 单张 → 但单张优先, 不进 combo. 改 100+400=500 和 200+300=500."""
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=100, on_date=date(2026, 5, 10))
    _mk_note(db_session, s.id, amount=400, on_date=date(2026, 5, 11))
    _mk_note(db_session, s.id, amount=200, on_date=date(2026, 5, 12))
    _mk_note(db_session, s.id, amount=300, on_date=date(2026, 5, 13))
    _mk_flow(db_session, amount=-500)
    result = spm.reconcile(db_session)
    assert result.matches[0].decision == "needs_review"


def test_no_combo_match_keeps_open(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=100, on_date=date(2026, 5, 10))
    _mk_note(db_session, s.id, amount=200, on_date=date(2026, 5, 11))
    f = _mk_flow(db_session, amount=-500)
    result = spm.reconcile(db_session)
    assert result.matches[0].decision == "no_candidates"
    db_session.refresh(f)
    assert f.reconciliation_status == "open"


def test_amount_tolerance(db_session):
    """流水 -580.01 vs 单据 580.00 应视为同一笔 (差 0.01 <= 0.02 容差)."""
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=580, on_date=date(2026, 5, 14))
    _mk_flow(db_session, amount="-580.01")
    result = spm.reconcile(db_session)
    assert result.matched_count == 1
    assert result.matches[0].decision == "exact"


def test_time_window_excludes_old_notes(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    # 单据日期比流水早 90 天 → 应排除 (window=60 默认)
    _mk_note(db_session, s.id, amount=580,
             on_date=date(2026, 5, 14) - timedelta(days=90))
    _mk_flow(db_session, amount=-580,
             on_time=datetime(2026, 5, 14, tzinfo=timezone.utc))
    result = spm.reconcile(db_session)
    assert result.matches[0].decision == "no_candidates"


def test_already_paid_notes_excluded(db_session):
    """status=paid 的单据不应再被匹配."""
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=580, on_date=date(2026, 5, 14), status="paid")
    _mk_flow(db_session, amount=-580)
    result = spm.reconcile(db_session)
    assert result.matches[0].decision == "no_candidates"


def test_only_factory_payment_flows_scanned(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=580, on_date=date(2026, 5, 14))
    # 没标 factory_payment 的不扫
    _mk_flow(db_session, amount=-580, rec_type=None, tx_no="X1")
    _mk_flow(db_session, amount=-580, rec_type="promotion", tx_no="X2")
    result = spm.reconcile(db_session)
    assert result.scanned == 0


def test_already_matched_flows_not_rescanned(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=580, on_date=date(2026, 5, 14))
    _mk_flow(db_session, amount=-580, rec_status="matched")
    result = spm.reconcile(db_session)
    assert result.scanned == 0


def test_apply_manual_match_validates_amount(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    n1 = _mk_note(db_session, s.id, amount=100, on_date=date(2026, 5, 10))
    n2 = _mk_note(db_session, s.id, amount=200, on_date=date(2026, 5, 11))
    f = _mk_flow(db_session, amount=-500, tx_no="MX1")
    # 用户错选 → 100+200 != 500, 应该报错
    with pytest.raises(ValueError) as ei:
        spm.apply_manual_match(db_session, flow_id=f.id, note_ids=[n1.id, n2.id])
    assert "金额对不上" in str(ei.value)


def test_apply_manual_match_succeeds(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    n1 = _mk_note(db_session, s.id, amount=200, on_date=date(2026, 5, 10), note_no="A")
    n2 = _mk_note(db_session, s.id, amount=300, on_date=date(2026, 5, 11), note_no="B")
    f = _mk_flow(db_session, amount=-500, tx_no="MX2")
    fm = spm.apply_manual_match(db_session, flow_id=f.id, note_ids=[n1.id, n2.id])
    assert fm.decision == "combo"
    assert set(fm.matched_note_ids) == {n1.id, n2.id}
    db_session.refresh(n1); db_session.refresh(n2); db_session.refresh(f)
    assert n1.status == "paid"
    assert n2.status == "paid"
    assert f.reconciliation_status == "matched"
    assert n1.alipay_flow_no == "MX2"


def test_apply_manual_match_missing_flow_raises(db_session):
    with pytest.raises(ValueError):
        spm.apply_manual_match(db_session, flow_id=99999, note_ids=[1])


def test_account_filter_scans_only_one_account(db_session):
    s = _mk_supplier(db_session, name="X木业", keywords=["X木业"])
    _mk_note(db_session, s.id, amount=100, on_date=date(2026, 5, 14), note_no="A1")
    _mk_note(db_session, s.id, amount=200, on_date=date(2026, 5, 14), note_no="A2")
    _mk_flow(db_session, amount=-100, account="企业号", tx_no="E1")
    _mk_flow(db_session, amount=-200, account="个体户私账", tx_no="P1")
    result = spm.reconcile(db_session, account="企业号")
    assert result.scanned == 1
    assert result.matches[0].matched_note_nos == ["A1"]
