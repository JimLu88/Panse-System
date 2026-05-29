from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./panse_erp.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # AI 辅助 (plan §7.2)
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"

    # 认证 (plan §10 Phase 6) — JWT HS256, dev 默认值, 生产必须改
    jwt_secret: str = "panse-dev-secret-CHANGE-ME-in-production"
    jwt_ttl_hours: int = 24

    # 审计：哪些路径的写操作要记录
    audit_skip_paths: str = "/api/health,/api/auth/login"

    # DB 连接池 (仅 Postgres 生效; SQLite 用单连接池)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
