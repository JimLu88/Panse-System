"""配置中心（简化版）：环境变量 + 家具品牌种子规则。

对应设计稿 02-cross-cutting/config-rule-center.md。
易变项（敏感词分级 / 平台折叠线 / 产品关键词）集中在这里，便于后续做热更新。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./marketing.db"

    llm_provider: str = "mock"  # mock / anthropic / openai
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-fable-5"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"

    erp_base_url: str = ""
    erp_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---- 家具品牌敏感词分级（config-rule-center: S 熔断 / A 强制人工 / B 预警）----
# 家具文案高频踩的雷词，与美妆不同。
BANNED_WORDS = {
    "S": ["甲醛超标", "致癌", "国家免检"],  # 直接拒绝
    "A": ["最", "第一", "全网最低", "纯实木", "零甲醛", "100%", "绝对", "顶级"],  # 强制人工改
    "B": ["超好用", "强烈推荐", "yyds", "巨划算"],  # 仅预警
}

# ---- 平台折叠线（platforms.md：小红书 标题20 + 正文前50）----
PLATFORM_FOLD = {
    "xhs": {"title_chars": 20, "body_first_chars": 50, "tag_count": [5, 10], "cover_ratio": "3:4"},
    "zhihu": {"title_chars": 30, "body_first_chars": 100, "tag_count": [3, 6], "cover_ratio": "16:9"},
}

# ---- 产品线关键词（实际应来自 ERP products 表；MVP 用种子）----
PRODUCT_KEYWORDS = {
    "餐桌": ["实木餐桌", "榉木餐桌", "岩板餐桌", "小户型餐桌", "餐桌椅组合"],
    "餐椅": ["实木餐椅", "靠背椅", "餐椅推荐"],
    "茶几": ["实木茶几", "岩板茶几", "小户型茶几"],
    "柜类": ["电视柜", "餐边柜", "玄关柜", "储物柜"],
    "床": ["实木床", "原木床", "主卧大床"],
}
