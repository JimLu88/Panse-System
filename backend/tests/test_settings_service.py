"""settings_service: 加密往返 + DB get/set + env fallback + masking."""
from __future__ import annotations

from app.services import settings_service
from app.models.settings import SystemSetting


def test_encrypt_decrypt_roundtrip():
    secret = "sk-ant-api03-very-secret-value-xyz789"
    ct = settings_service.encrypt(secret)
    assert ct != secret
    assert settings_service.decrypt(ct) == secret


def test_decrypt_tampered_returns_empty():
    ct = settings_service.encrypt("payload")
    # 改一个字节
    tampered = ct[:-2] + ("A" if ct[-2] != "A" else "B") + ct[-1]
    assert settings_service.decrypt(tampered) == ""


def test_decrypt_garbage_returns_empty():
    assert settings_service.decrypt("not-base64-!@#$") == ""
    assert settings_service.decrypt("") == ""


def test_set_secret_stores_encrypted(db_session):
    settings_service.set_value(db_session, "ai_diagnose_api_key", "sk-ant-abcdef123456")
    row = db_session.query(SystemSetting).filter_by(key="ai_diagnose_api_key").one()
    assert row.is_secret is True
    assert row.value_plain is None
    assert row.value_encrypted is not None
    assert "sk-ant" not in row.value_encrypted
    # 取出来应该解密回原值
    assert settings_service.get(db_session, "ai_diagnose_api_key") == "sk-ant-abcdef123456"


def test_set_plain_stores_plain(db_session):
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-sonnet-4-6")
    row = db_session.query(SystemSetting).filter_by(key="ai_diagnose_model").one()
    assert row.is_secret is False
    assert row.value_plain == "claude-sonnet-4-6"
    assert row.value_encrypted is None


def test_clear_value_nullifies(db_session):
    settings_service.set_value(db_session, "ai_ocr_api_key", "k1")
    assert settings_service.get(db_session, "ai_ocr_api_key") == "k1"
    settings_service.set_value(db_session, "ai_ocr_api_key", "")
    assert settings_service.get(db_session, "ai_ocr_api_key") is None


def test_env_fallback_when_db_empty(db_session, monkeypatch):
    monkeypatch.setenv("AI_OCR_MODEL", "qwen-vl-max")
    assert settings_service.get(db_session, "ai_ocr_model") == "qwen-vl-max"


def test_db_overrides_env(db_session, monkeypatch):
    monkeypatch.setenv("AI_OCR_MODEL", "from-env")
    settings_service.set_value(db_session, "ai_ocr_model", "from-db")
    assert settings_service.get(db_session, "ai_ocr_model") == "from-db"


def test_mask_secret():
    assert settings_service.mask_secret("sk-ant-abcdef123456") == "sk-***3456"
    assert settings_service.mask_secret("short") == "***"
    assert settings_service.mask_secret("") == ""
    assert settings_service.mask_secret(None) == ""


def test_get_ai_config_diagnose_uses_anthropic_env_fallback(db_session, monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", "fallback-key")
    monkeypatch.setattr(s, "ai_model", "claude-haiku-4-5-20251001")
    cfg = settings_service.get_ai_config(db_session, "diagnose")
    assert cfg["api_key"] == "fallback-key"
    assert cfg["model"] == "claude-haiku-4-5-20251001"
    assert cfg["provider"] == "anthropic"


def test_get_ai_config_db_wins_over_env(db_session):
    settings_service.set_value(db_session, "ai_ocr_provider", "openai")
    settings_service.set_value(db_session, "ai_ocr_api_key", "key1")
    settings_service.set_value(db_session, "ai_ocr_model", "qwen-vl-max")
    settings_service.set_value(db_session, "ai_ocr_base_url",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1")
    cfg = settings_service.get_ai_config(db_session, "ocr")
    assert cfg == {
        "provider": "openai", "api_key": "key1", "model": "qwen-vl-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "user_agent": "",
    }
