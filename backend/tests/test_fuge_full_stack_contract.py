from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "fuge" / "compose.yml"
ENV_EXAMPLE = ROOT / "deploy" / "fuge" / ".env.example"


def test_fuge_compose_is_full_stack_and_does_not_mount_panse_paths():
    text = COMPOSE.read_text(encoding="utf-8")
    for service in ("db:", "api:", "web:", "backup:"):
        assert service in text
    assert "fuge_erp" in text
    assert "ERP_INSTANCE_ID: fuge" in text
    assert "ERP_BRAND_CODE: FG" in text
    assert "source: ${FUGE_DATA_ROOT" in text
    assert "D:/畔色 产品图库" not in text
    assert "./storage:/app/storage" not in text
    assert "./backend:/app" not in text


def test_fuge_env_requires_dedicated_secrets_and_data_root():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "FUGE_DATA_ROOT=" in text
    assert "FUGE_POSTGRES_PASSWORD=" in text
    assert "FUGE_JWT_SECRET=" in text
    assert "FUGE_DISABLE_SCHEDULER=1" in text


def test_runtime_identity_defaults_to_panse_without_fuge_env(monkeypatch):
    monkeypatch.delenv("ERP_INSTANCE_ID", raising=False)
    monkeypatch.delenv("ERP_BRAND_CODE", raising=False)
    monkeypatch.delenv("ERP_BRAND_NAME", raising=False)
    monkeypatch.delenv("ERP_SHOP_NAME", raising=False)
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.erp_instance_id == "panse"
    assert settings.erp_brand_code == "PS"
    assert settings.erp_brand_name == "畔色"

