"""NPD P2b: 知识库笔记 CRUD/检索 + AI 建议(AI 不可用降级给本库检索)。"""
from __future__ import annotations

from decimal import Decimal

from app.models.material import Material
from app.services import npd_service


def test_knowledge_note_crud_search(db_session):
    npd_service.add_knowledge_note(db_session, title="樱桃木防氧化", category="木材",
                                   material="樱桃木", body="白胚阶段先封闭防氧化")
    notes = npd_service.list_knowledge_notes(db_session, q="樱桃木")
    assert len(notes) == 1 and notes[0].title == "樱桃木防氧化"
    assert npd_service.list_knowledge_notes(db_session, category="木材")


def test_ai_suggest_retrieves_sources_and_degrades(db_session):
    db_session.add(Material(code="AC-7001", name="樱桃木实木板", category="木材", price=Decimal("80")))
    db_session.commit()
    npd_service.add_knowledge_note(db_session, title="樱桃木变色", category="木材",
                                   material="樱桃木", body="遇光氧化变深, 需提前防护")
    r = npd_service.ai_design_suggest(db_session, question="樱桃木怎么防变色",
                                      category="木材", material="樱桃木")
    assert "ai_available" in r and "sources" in r
    types = {s["type"] for s in r["sources"]}
    assert "material" in types or "note" in types       # 检索到本库数据
    if not r["ai_available"]:                            # AI 未配 → 降级提示
        assert r["note"]
