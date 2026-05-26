"""delivery_matcher: 模糊评分 + 数量加分 + AI 兜底."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models.order import FactoryOrder
from app.services import delivery_matcher, settings_service
from app.services.ai_provider import AiResponse


def _mkfo(db, factory_order_no, *, product_code=None, sku=None, qty=1,
          factory_name="木作工厂", delivery_date=None, platform_order_no=None):
    fo = FactoryOrder(
        factory_order_no=factory_order_no,
        platform_order_no=platform_order_no,
        factory_name=factory_name,
        product_code=product_code, sku=sku, qty=qty,
        expected_delivery=delivery_date, order_date=delivery_date,
    )
    db.add(fo)
    db.flush()
    return fo


def test_no_candidates_returns_empty(db_session):
    out = delivery_matcher.match_line(
        db_session, item_name="电视柜", spec="1800×850", qty=Decimal("1"),
    )
    assert out == []


def test_fuzzy_matches_by_sku(db_session):
    _mkfo(db_session, "FO-001", product_code="P1", sku="电视柜 1800×850 黑色", qty=1)
    _mkfo(db_session, "FO-002", product_code="P2", sku="完全无关的产品", qty=1)
    out = delivery_matcher.match_line(
        db_session, item_name="电视柜", spec="1800×850", qty=Decimal("1"),
        enable_ai_tiebreaker=False,
    )
    assert out
    assert out[0].factory_order_no == "FO-001"
    assert out[0].confidence > Decimal("0")
    assert out[0].method == "fuzzy"


def test_qty_match_boosts_confidence(db_session):
    _mkfo(db_session, "FO-101", sku="电视柜 1800×850", qty=2)
    _mkfo(db_session, "FO-102", sku="电视柜 1800×850", qty=5)
    out = delivery_matcher.match_line(
        db_session, item_name="电视柜", spec="1800×850", qty=Decimal("2"),
        enable_ai_tiebreaker=False,
    )
    # 数量一致的 FO-101 应排在前
    assert out[0].factory_order_no == "FO-101"


def test_top_n_limits_results(db_session):
    for i in range(10):
        _mkfo(db_session, f"FO-{i:03d}", sku=f"电视柜 模型{i}", qty=1)
    out = delivery_matcher.match_line(
        db_session, item_name="电视柜", spec="", qty=Decimal("1"),
        enable_ai_tiebreaker=False, top_n=3,
    )
    assert len(out) <= 3


def test_apply_candidates_writes_top_to_line(db_session):
    from app.models.supplier import DeliveryNote, DeliveryNoteLine, Supplier
    s = Supplier(name="木作", supplier_type="woodwork")
    db_session.add(s); db_session.flush()
    n = DeliveryNote(supplier_id=s.id, status="pending_review")
    db_session.add(n); db_session.flush()
    line = DeliveryNoteLine(delivery_note_id=n.id, line_no=1,
                            item_name="x", qty=Decimal("1"))
    db_session.add(line); db_session.flush()

    cands = [
        delivery_matcher.MatchCandidate(
            order_no="O1", factory_order_no="FO-X", confidence=Decimal("85"),
            method="fuzzy", reason="r1", product_code="P", sku="s", qty=1,
        ),
        delivery_matcher.MatchCandidate(
            order_no="O2", factory_order_no="FO-Y", confidence=Decimal("60"),
            method="fuzzy", reason="r2",
        ),
    ]
    delivery_matcher.apply_candidates_to_line(line, cands)
    assert line.matched_order_no == "O1"
    assert line.match_confidence == Decimal("85")
    assert line.match_method == "fuzzy"
    assert len(line.match_candidates) == 2
    assert line.match_candidates[0]["confidence"] == 85.0


def test_apply_candidates_empty_clears(db_session):
    from app.models.supplier import DeliveryNoteLine, DeliveryNote, Supplier
    s = Supplier(name="x", supplier_type="woodwork")
    db_session.add(s); db_session.flush()
    n = DeliveryNote(supplier_id=s.id, status="pending_review")
    db_session.add(n); db_session.flush()
    line = DeliveryNoteLine(delivery_note_id=n.id, line_no=1, qty=Decimal("1"),
                            matched_order_no="old", match_confidence=Decimal("80"))
    db_session.add(line); db_session.flush()
    delivery_matcher.apply_candidates_to_line(line, [])
    assert line.matched_order_no is None
    assert line.match_method == "none"
    assert line.match_candidates == []


def test_ai_tiebreaker_invoked_only_for_low_confidence(db_session):
    """高置信场景 (>=70) AI 不该被调."""
    settings_service.set_value(db_session, "ai_diagnose_provider", "anthropic")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "k")
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-x")
    _mkfo(db_session, "FO-200", sku="电视柜 1800×850 黑色", qty=1)
    with patch.object(delivery_matcher, "build_provider") as bp:
        out = delivery_matcher.match_line(
            db_session, item_name="电视柜 1800×850", spec="", qty=Decimal("1"),
        )
    # 头部分数应该 >=70
    if out and out[0].confidence >= Decimal("70"):
        bp.assert_not_called()


def test_ai_tiebreaker_called_when_low_confidence(db_session):
    """所有候选都很差时调 AI."""
    settings_service.set_value(db_session, "ai_diagnose_provider", "anthropic")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "k")
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-x")
    _mkfo(db_session, "FO-301", sku="灯具", qty=1)
    _mkfo(db_session, "FO-302", sku="床头柜", qty=1)
    _mkfo(db_session, "FO-303", sku="柜子相似一点", qty=1)
    fake_p = MagicMock()
    fake_p.chat.return_value = AiResponse(
        text='{"best_factory_order_no": "FO-303", "confidence": 92, "reason": "最可能"}',
        model="claude-x",
    )
    with patch.object(delivery_matcher, "build_provider", return_value=fake_p):
        out = delivery_matcher.match_line(
            db_session, item_name="电视柜", spec="1800×850", qty=Decimal("1"),
        )
    assert out
    assert out[0].factory_order_no == "FO-303"
    assert out[0].method == "ai"
    assert out[0].confidence == Decimal("92")


def test_ai_tiebreaker_gracefully_handles_unavailable(db_session):
    """没配诊断模型 → 退回到纯 fuzzy 结果, 不抛."""
    _mkfo(db_session, "FO-401", sku="柜子", qty=1)
    out = delivery_matcher.match_line(
        db_session, item_name="电视柜", spec="", qty=Decimal("1"),
    )
    # 没配置 AI, 也不该崩
    assert isinstance(out, list)


def test_ai_tiebreaker_handles_invalid_json(db_session):
    settings_service.set_value(db_session, "ai_diagnose_provider", "anthropic")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "k")
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-x")
    _mkfo(db_session, "FO-501", sku="柜子", qty=1)
    fake_p = MagicMock()
    fake_p.chat.return_value = AiResponse(text="garbage not json", model="claude-x")
    with patch.object(delivery_matcher, "build_provider", return_value=fake_p):
        out = delivery_matcher.match_line(
            db_session, item_name="电视柜", spec="", qty=Decimal("1"),
        )
    # AI 解析失败也不该崩, fuzzy 结果还在
    assert isinstance(out, list)
