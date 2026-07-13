# -*- coding: utf-8 -*-
"""OCR 坏 JSON 自动重调一次 (2026-07-13)。

案发: 打包手写本整页 17+ 行, OCR 槽主/兜底都是本机 7B 小模型, 长 JSON 中途缺冒号 →
"AI 返回无法解析: Expecting ':' delimiter" 直接打断人工识别流程。
修: _ocr_json 第一次解析失败带着语法错误重调一次, 仍坏才抛 AiUnavailable。
"""
from types import SimpleNamespace

import pytest

from app.services import vision_ocr_service as v
from app.services.ai_provider import AiUnavailable

BAD = '{"rows": [{"customer_name" "张三"}]}'   # 缺冒号 → Expecting ':' delimiter
GOOD = '{"rows": [{"customer_name": "张三", "packing_fee": 100}], "declared_total": 100, "ocr_warnings": []}'


def _patch_responses(monkeypatch, texts):
    """依次返回 texts 里的回包; 记录调用的 user 消息供断言。"""
    calls = []

    def fake_resp(db, *, system, user, image_bytes, mime, max_tokens):
        calls.append(user)
        return SimpleNamespace(text=texts[len(calls) - 1])

    monkeypatch.setattr(v, "_ocr_image_resp", fake_resp)
    return calls


def test_bad_json_then_good_recovers(db_session, monkeypatch):
    """首回坏 JSON → 自动重调(带语法错误提示), 第二回好 JSON → 正常返回, 不打断流程。"""
    calls = _patch_responses(monkeypatch, [BAD, GOOD])
    data = v.parse_packing_bill(db_session, b"img")
    assert data["rows"][0]["customer_name"] == "张三"
    assert data["declared_total"] == 100
    assert len(calls) == 2
    assert "语法错误" in calls[1]          # 重调时把报错喂了回去


def test_bad_json_twice_raises(db_session, monkeypatch):
    """重调一次仍坏 → 抛 AiUnavailable(前端可见), 不无限重试。"""
    calls = _patch_responses(monkeypatch, [BAD, BAD])
    with pytest.raises(AiUnavailable):
        v.parse_packing_bill(db_session, b"img")
    assert len(calls) == 2


def test_good_json_single_call(db_session, monkeypatch):
    """好 JSON 一次过, 不多调。"""
    calls = _patch_responses(monkeypatch, [GOOD])
    data = v.parse_packing_bill(db_session, b"img")
    assert data["rows"] and len(calls) == 1
