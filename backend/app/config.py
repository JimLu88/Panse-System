from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./panse_erp.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # AI 辅助 (plan §7.2)
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
