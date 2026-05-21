"""OCR service: JSON 解析 / 容错 / 调用 provider."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services import ocr_service, settings_service
from app.services.ai_provider import AiResponse, AiUnavailable


SAMPLE_PAYLOAD = {
    "note_no": "5480798",
    "delivery_date": "2026-05-14",
    "lines": [
        {"item_name": "电视柜", "spec": "1800×850",
         "unit": "套", "qty": 1, "unit_price": 580, "amount": 580,
         "raw_text": "电视柜 1800×850 1套 580"},
        {"item_name": "斗柜", "spec": "800×900",
         "unit": "套", "qty": 1, "unit_price": 320, "amount": 320,
         "raw_text": "斗柜 800×900 1套 320"},
    ],
    "total_amount": 900,
    "warnings": [],
}


def test_parse_ocr_response_happy_path():
    parsed = ocr_service.parse_ocr_response(SAMPLE_PAYLOAD, model="m", raw="<raw>")
    assert parsed.note_no == "5480798"
    assert parsed.delivery_date.isoformat() == "2026-05-14"
    assert len(parsed.lines) == 2
    assert parsed.lines[0].item_name == "电视柜"
    assert parsed.lines[0].qty == Decimal("1")
    assert parsed.lines[0].amount == Decimal("580")
    assert parsed.total_amount == Decimal("900")
    assert parsed.confidence == Decimal("100")  # 无 warning
    assert parsed.warnings == []


def test_parse_handles_chinese_date_format():
    p = ocr_service.parse_ocr_response(
        {**SAMPLE_PAYLOAD, "delivery_date": "2026年5月14日"},
        model="m", raw="",
    )
    assert p.delivery_date.isoformat() == "2026-05-14"


def test_parse_handles_slash_date_format():
    p = ocr_service.parse_ocr_response(
        {**SAMPLE_PAYLOAD, "delivery_date": "2026/05/14"},
        model="m", raw="",
    )
    assert p.delivery_date.isoformat() == "2026-05-14"


def test_parse_strips_currency_symbols():
    payload = {
        **SAMPLE_PAYLOAD,
        "lines": [{"item_name": "x", "spec": "", "unit": "件", "qty": 2,
                   "unit_price": "¥1,200.50", "amount": "2,401.00", "raw_text": ""}],
        "total_amount": "¥2,401",
    }
    p = ocr_service.parse_ocr_response(payload, model="m", raw="")
    assert p.lines[0].unit_price == Decimal("1200.50")
    assert p.lines[0].amount == Decimal("2401.00")
    assert p.total_amount == Decimal("2401")


def test_parse_auto_fills_amount_when_missing():
    payload = {**SAMPLE_PAYLOAD,
               "lines": [{"item_name": "x", "spec": "", "unit": "件", "qty": 3,
                          "unit_price": 100, "amount": None, "raw_text": ""}],
               "total_amount": None}
    p = ocr_service.parse_ocr_response(payload, model="m", raw="")
    assert p.lines[0].amount == Decimal("300.00")
    # 没合计 → 用行金额求和
    assert p.total_amount == Decimal("300.00")


def test_parse_records_invalid_number_warnings():
    payload = {**SAMPLE_PAYLOAD,
               "lines": [{"item_name": "x", "spec": "", "unit": "件", "qty": 1,
                          "unit_price": "abc", "amount": None, "raw_text": ""}]}
    p = ocr_service.parse_ocr_response(payload, model="m", raw="")
    assert any("不是数字" in w for w in p.warnings)
    assert p.confidence < Decimal("100")


def test_parse_warnings_lower_confidence():
    payload = {**SAMPLE_PAYLOAD, "warnings": ["w1", "w2", "w3", "w4"]}
    p = ocr_service.parse_ocr_response(payload, model="m", raw="")
    # 100 - 4*5 = 80
    assert p.confidence == Decimal("80")


def test_parse_confidence_floor_30():
    payload = {**SAMPLE_PAYLOAD, "warnings": ["w"] * 50}
    p = ocr_service.parse_ocr_response(payload, model="m", raw="")
    assert p.confidence == Decimal("30")


def test_extract_json_strips_markdown_fences():
    text = "```json\n" + '{"note_no": "X1", "lines": [], "total_amount": 0, "warnings": []}' + "\n```"
    parsed = ocr_service._extract_json(text)
    assert parsed["note_no"] == "X1"


def test_extract_json_finds_embedded_object():
    text = "好的, 这是结果:\n{\"note_no\": \"Y2\", \"lines\": [], \"total_amount\": 1}\n end"
    parsed = ocr_service._extract_json(text)
    assert parsed["note_no"] == "Y2"


def test_extract_json_raises_on_garbage():
    with pytest.raises(ocr_service.OcrParseError):
        ocr_service._extract_json("not json at all")


def test_ocr_delivery_note_uses_ocr_config(db_session):
    settings_service.set_value(db_session, "ai_ocr_provider", "anthropic")
    settings_service.set_value(db_session, "ai_ocr_api_key", "k")
    settings_service.set_value(db_session, "ai_ocr_model", "claude-opus-4-7")

    fake_provider = MagicMock()
    fake_provider.chat_with_image.return_value = AiResponse(
        text='{"note_no":"X","delivery_date":"2026-05-14","lines":[],"total_amount":0,"warnings":[]}',
        model="claude-opus-4-7",
    )
    with patch.object(ocr_service, "build_provider", return_value=fake_provider) as bp:
        result = ocr_service.ocr_delivery_note(
            db_session, image_bytes=b"fakejpg", mime="image/jpeg",
            supplier_name="木作工厂", supplier_type="woodwork",
        )

    cfg = bp.call_args.args[0]
    assert cfg["model"] == "claude-opus-4-7"
    assert result.note_no == "X"
    fake_provider.chat_with_image.assert_called_once()
    call = fake_provider.chat_with_image.call_args.kwargs
    assert "木作工厂" in call["user"]
    assert "woodwork" in call["user"]
    assert call["image_bytes"] == b"fakejpg"


def test_ocr_delivery_note_raises_when_unconfigured(db_session, monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: type("S", (), {
        "anthropic_api_key": "", "ai_model": "", "jwt_secret": "x",
    })())
    with pytest.raises(ocr_service.OcrUnavailable):
        ocr_service.ocr_delivery_note(db_session, image_bytes=b"x")


def test_ocr_delivery_note_propagates_provider_failure(db_session):
    settings_service.set_value(db_session, "ai_ocr_provider", "anthropic")
    settings_service.set_value(db_session, "ai_ocr_api_key", "k")
    settings_service.set_value(db_session, "ai_ocr_model", "claude-x")
    fake = MagicMock()
    fake.chat_with_image.side_effect = AiUnavailable("api dead")
    with patch.object(ocr_service, "build_provider", return_value=fake):
        with pytest.raises(ocr_service.OcrUnavailable) as ei:
            ocr_service.ocr_delivery_note(db_session, image_bytes=b"x")
    assert "api dead" in str(ei.value)
