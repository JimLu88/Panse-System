"""活动系统云端 LLM (DeepSeek/千问) 设置 + 兜底钩子 (2026-07-17)。

锁五件事:
① campaign_ai_api_key 走既有加密机制往返 (密文落库, value_plain 为空, 密文不含明文)
② settings_status / 设置读取视图绝不回明文 — 只 api_key_set + 尾4位
③ 未配置 AI 时零行为变化: 发现日期兜底不触网不改结果; classify_failure_reason 返回 None
④ get_campaign_ai 构造: none/缺key → None; deepseek/qwen → OpenAI 兼容 provider,
   base_url 与默认模型正确, 模型可覆盖
⑤ AI 兜底路径: 严格 JSON → 补日期/补标题(规则标题优先); 坏输出 → 静默降级原行为
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.models.campaign import CampaignCalendar
from app.models.settings import SystemSetting
from app.services import campaign_ai_service as cas
from app.services import campaign_discovery_service as cds
from app.services import campaign_recon_service as crs
from app.services import settings_service
from app.services.ai_provider import AiResponse, OpenAICompatibleProvider

KEY = "sk-deepseek-test-key-abc1234"


def _configure(db, provider="deepseek", model=None, key=KEY):
    settings_service.set_value(db, "campaign_ai_provider", provider)
    if model:
        settings_service.set_value(db, "campaign_ai_model", model)
    if key:
        settings_service.set_value(db, "campaign_ai_api_key", key)


class FakeProvider:
    """离线假 provider: 记录调用并回固定文本 (绝不触网)。"""
    name = "openai"
    model = "fake"

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def chat(self, *, system: str, user: str, max_tokens: int = 1024, **kw) -> AiResponse:
        self.calls.append({"system": system, "user": user})
        return AiResponse(text=self.text, model=self.model)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """本文件所有测试都不许真发 HTTP (兜底路径必须离线可证)。"""
    import httpx

    def _boom(*a, **kw):
        raise AssertionError("测试不许触网: httpx.post 被调用了")

    monkeypatch.setattr(httpx, "post", _boom)


# ── ① key 加密往返 ────────────────────────────────────────────────────────────

def test_campaign_key_stored_encrypted_roundtrip(db_session):
    settings_service.set_value(db_session, "campaign_ai_api_key", KEY)
    row = db_session.query(SystemSetting).filter_by(key="campaign_ai_api_key").one()
    assert row.is_secret is True
    assert row.value_plain is None
    assert row.value_encrypted and KEY not in row.value_encrypted
    assert settings_service.get(db_session, "campaign_ai_api_key") == KEY


def test_campaign_key_clear(db_session):
    settings_service.set_value(db_session, "campaign_ai_api_key", KEY)
    settings_service.set_value(db_session, "campaign_ai_api_key", "")
    assert settings_service.get(db_session, "campaign_ai_api_key") is None
    st = cas.settings_status(db_session)
    assert st["api_key_set"] is False and st["api_key_tail"] == ""


# ── ② 读取视图绝不回明文 ─────────────────────────────────────────────────────

def test_settings_status_never_returns_plaintext(db_session):
    _configure(db_session)
    st = cas.settings_status(db_session)
    assert st["provider"] == "deepseek"
    assert st["model"] == "deepseek-chat"          # 未填模型 → provider 默认
    assert st["api_key_set"] is True
    assert st["api_key_tail"] == KEY[-4:]
    assert KEY not in json.dumps(st)               # 整个视图任何角落都不含明文


def test_settings_status_short_key_gives_no_tail(db_session):
    _configure(db_session, key="short12")          # <9 位 → 尾4位≈整key, 不给
    st = cas.settings_status(db_session)
    assert st["api_key_set"] is True and st["api_key_tail"] == ""


# ── ④ get_campaign_ai 构造 ───────────────────────────────────────────────────

def test_get_campaign_ai_none_when_unconfigured(db_session):
    assert cas.get_campaign_ai(db_session) is None                       # 全空
    settings_service.set_value(db_session, "campaign_ai_provider", "none")
    settings_service.set_value(db_session, "campaign_ai_api_key", KEY)
    assert cas.get_campaign_ai(db_session) is None                       # 显式关闭
    settings_service.set_value(db_session, "campaign_ai_provider", "deepseek")
    settings_service.set_value(db_session, "campaign_ai_api_key", "")
    assert cas.get_campaign_ai(db_session) is None                       # 有 provider 没 key


def test_get_campaign_ai_deepseek_defaults(db_session):
    _configure(db_session, provider="deepseek")
    p = cas.get_campaign_ai(db_session)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url == "https://api.deepseek.com"
    assert p.model == "deepseek-chat"
    assert p.api_key == KEY


def test_get_campaign_ai_qwen_defaults_and_model_override(db_session):
    _configure(db_session, provider="qwen", model="qwen-max")
    p = cas.get_campaign_ai(db_session)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert p.model == "qwen-max"                   # 手填覆盖默认 qwen-plus


# ── ③ 未配置时两个兜底钩子零行为变化 ─────────────────────────────────────────

def test_discovery_unparsable_date_unchanged_without_ai(db_session):
    """老行为: 日期解析不出 → start_at 留 None, 行照常入库; 不触网 (autouse 断网兜底证明)。"""
    r = cds.upsert_calendar(db_session, [
        {"title": "88VIP大促", "start": "7月17日晚8点", "end": None,
         "status": "报名中", "raw": "88VIP大促 7月17日20:00-7月19日23:59 报名中"},
    ])
    assert r == {"inserted": 1, "updated": 0, "skipped": 0}
    row = db_session.query(CampaignCalendar).one()
    assert row.title == "88VIP大促" and row.start_at is None and row.end_at is None


def test_discovery_titleless_still_skipped_without_ai(db_session):
    r = cds.upsert_calendar(db_session, [
        {"title": "", "start": None, "end": None, "raw": "一段抓来的原始文本"},
    ])
    assert r == {"inserted": 0, "updated": 0, "skipped": 1}


def test_classify_failure_reason_none_without_ai(db_session):
    assert crs.classify_failure_reason(db_session, "商品价格低于最低标价线, 报名被拒") is None
    assert crs.classify_failure_reason(db_session, "") is None
    assert crs.classify_failure_reason(db_session, None) is None


# ── ⑤ AI 兜底路径 (假 provider, 离线) ────────────────────────────────────────

def test_discovery_ai_rescues_dates_and_title(db_session, monkeypatch):
    _configure(db_session)
    fake = FakeProvider(json.dumps({
        "title": "88VIP会员日", "start": "2026-07-17 20:00:00", "end": "2026-07-19 23:59:59",
    }))
    monkeypatch.setattr(cas, "get_campaign_ai", lambda db: fake)

    r = cds.upsert_calendar(db_session, [
        # 规则日期解析不出 → AI 补日期; 规则标题存在 → 保留规则标题 (AI 只补空)
        {"title": "88VIP大促", "start": "7月17日晚8点", "end": "7月19日",
         "raw": "88VIP大促 7月17日20点~7月19日"},
        # 没标题 → AI 连标题一起救回来
        {"title": "", "start": None, "end": None,
         "raw": "88VIP会员日 7月17日20:00:00 至 7月19日23:59:59"},
    ])
    assert r["inserted"] == 2 and r["skipped"] == 0
    rows = {row.title: row for row in db_session.query(CampaignCalendar).all()}
    assert rows["88VIP大促"].start_at == datetime(2026, 7, 17, 20, 0, 0)
    assert rows["88VIP大促"].end_at == datetime(2026, 7, 19, 23, 59, 59)
    assert rows["88VIP会员日"].start_at == datetime(2026, 7, 17, 20, 0, 0)
    assert len(fake.calls) == 2
    assert "严格 JSON" in fake.calls[0]["system"]


def test_discovery_ai_not_called_when_rules_succeed(db_session, monkeypatch):
    """规则解析成功 → 绝不烧 token。"""
    _configure(db_session)
    fake = FakeProvider("{}")
    monkeypatch.setattr(cas, "get_campaign_ai", lambda db: fake)
    cds.upsert_calendar(db_session, [
        {"title": "正常活动", "start": "2026-07-20 20:00:00", "end": None, "raw": "x"},
    ])
    assert fake.calls == []


def test_discovery_ai_bad_json_degrades_silently(db_session, monkeypatch):
    _configure(db_session)
    fake = FakeProvider("抱歉, 我不能确定活动时间。")
    monkeypatch.setattr(cas, "get_campaign_ai", lambda db: fake)
    r = cds.upsert_calendar(db_session, [
        {"title": "88VIP大促", "start": "7月17日晚8点", "raw": "raw text"},
    ])
    assert r["inserted"] == 1
    row = db_session.query(CampaignCalendar).one()
    assert row.start_at is None                    # 与未配置 AI 的原行为一致


def test_classify_failure_reason_with_ai(db_session, monkeypatch):
    _configure(db_session)
    fake = FakeProvider('{"reason": "低于最低标价线"}')
    monkeypatch.setattr(cas, "get_campaign_ai", lambda db: fake)
    assert crs.classify_failure_reason(db_session, "价格低于活动最低标价要求") == "低于最低标价线"


def test_classify_failure_reason_bad_output_none(db_session, monkeypatch):
    _configure(db_session)
    fake = FakeProvider("不是JSON")
    monkeypatch.setattr(cas, "get_campaign_ai", lambda db: fake)
    assert crs.classify_failure_reason(db_session, "某失败原因") is None


# ── 严格 JSON 解析器边界 ─────────────────────────────────────────────────────

def test_strict_json_variants():
    assert cas._strict_json('{"a": 1}') == {"a": 1}
    assert cas._strict_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert cas._strict_json('前缀说明 {"a": 1} 后缀') == {"a": 1}
    assert cas._strict_json("[1,2]") is None       # 非 dict 拒绝
    assert cas._strict_json("") is None
    assert cas._strict_json("纯文本") is None
