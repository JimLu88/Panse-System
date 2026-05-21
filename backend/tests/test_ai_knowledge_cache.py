"""AI 知识库缓存命中测试 (plan §12.2)。

第一次诊断 → 真调 API (mock), 写知识库。
第二次诊断同类异常 → 命中知识库, 不再调 API。
"""
from unittest.mock import MagicMock, patch

from app.models.exception import DataException
from app.models.knowledge import AiKnowledge
from app.services import ai_assistant


def _fake_response(text="【发生了什么】测试。", in_tok=100, out_tok=50):
    resp = MagicMock()
    resp.model = "claude-sonnet-4-6"
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    resp.usage = MagicMock(
        input_tokens=in_tok, output_tokens=out_tok,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return resp


def _mk_exc(db, *, pk="O1", desc="订单 O1 引用了不存在的产品编码 PPS999"):
    e = DataException(
        source_table="orders", source_pk=pk,
        exception_type="dangling_product_code",
        severity="error", description=desc,
    )
    db.add(e)
    db.flush()
    return e


def test_first_call_writes_knowledge(db_session):
    exc = _mk_exc(db_session)
    with patch.object(ai_assistant, "_client") as mocked:
        client = MagicMock()
        client.messages.create.return_value = _fake_response(text="first-answer")
        mocked.return_value = client
        log, ai = ai_assistant.diagnose_exception(db_session, exc.id)
    assert ai.text == "first-answer"
    assert db_session.query(AiKnowledge).count() == 1
    k = db_session.query(AiKnowledge).one()
    assert k.usage_count == 1
    assert k.solution_text == "first-answer"


def test_second_call_same_type_hits_cache(db_session):
    exc1 = _mk_exc(db_session, pk="O1")
    with patch.object(ai_assistant, "_client") as mocked:
        client = MagicMock()
        client.messages.create.return_value = _fake_response(text="cached-answer")
        mocked.return_value = client
        ai_assistant.diagnose_exception(db_session, exc1.id)
        api_calls_before = client.messages.create.call_count

        # 第二条异常: 同类型, 描述只 ID 变 → 应命中缓存
        exc2 = _mk_exc(db_session, pk="O2", desc="订单 O2 引用了不存在的产品编码 PPS888")
        log, ai = ai_assistant.diagnose_exception(db_session, exc2.id)

        assert client.messages.create.call_count == api_calls_before  # API 没被再调
    assert ai is not None
    assert ai.text == "cached-answer"
    assert "[cache hit]" in ai.model
    # usage_count 自增
    k = db_session.query(AiKnowledge).one()
    assert k.usage_count == 2


def test_different_exception_type_no_cache_hit(db_session):
    exc1 = _mk_exc(db_session, pk="O1")
    with patch.object(ai_assistant, "_client") as mocked:
        client = MagicMock()
        client.messages.create.return_value = _fake_response(text="answer1")
        mocked.return_value = client
        ai_assistant.diagnose_exception(db_session, exc1.id)

        # 不同类型异常
        exc2 = DataException(
            source_table="materials", source_pk="AC-1000",
            exception_type="custom_material_missing_price",
            severity="warning", description="定制物料缺价",
        )
        db_session.add(exc2)
        db_session.flush()

        client.messages.create.return_value = _fake_response(text="answer2")
        log, ai = ai_assistant.diagnose_exception(db_session, exc2.id)

    assert ai.text == "answer2"
    assert db_session.query(AiKnowledge).count() == 2


def test_cache_handles_no_api_key(db_session, monkeypatch):
    """无 key 时不应写缓存 (没有真实回答)."""
    monkeypatch.setattr(ai_assistant.settings, "anthropic_api_key", "")
    exc = _mk_exc(db_session)
    log, ai = ai_assistant.diagnose_exception(db_session, exc.id)
    assert ai is None
    assert db_session.query(AiKnowledge).count() == 0


def test_context_hash_strips_volatile_ids():
    """两条异常 description 里只 ID 不同 — hash 应相同."""
    e1 = DataException(
        source_table="orders", source_pk="x",
        exception_type="dangling_product_code",
        severity="error",
        description="订单 5112861625016010242 引用了不存在的产品编码 PPS888",
    )
    e2 = DataException(
        source_table="orders", source_pk="y",
        exception_type="dangling_product_code",
        severity="error",
        description="订单 5112569342173038640 引用了不存在的产品编码 PPS-1234",
    )
    assert ai_assistant._context_hash(e1) == ai_assistant._context_hash(e2)
