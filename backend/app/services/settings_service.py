"""KV 配置存取 (system_settings 表).

业务需求: AI provider (API key / base URL / 模型名) 后台可改, 不需重启。
secrets 用 Fernet 加密 (key 从 jwt_secret 派生)。

读取规则:
    settings_service.get(db, key)
        1) 命中 DB 返回 (自动解密)
        2) 否则查同名环境变量 (fallback)
        3) 否则返回 None
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.settings import SystemSetting

_SECRET_KEYS = {
    "ai_diagnose_api_key",
    "ai_ocr_api_key",
    # webhook URL 含 token, 视为机密
    "notify_webhook",
    # 飞书应用凭证
    "feishu_app_secret",
    # 快递查询凭证 (快递100 / 快递鸟) — 加密落库, 不再明文
    "kuaidi100_key",
    "kdniao_api_key",
    # ChatBI 问数: 云端 LLM key + 只读角色 DSN (含密码) — 加密落库
    "chatbi_cloud_api_key",
    "chatbi_ro_dsn",
}


# 静态加密 (Encryption At Rest) — 用 HMAC-SHA256 做 CTR 流 + HMAC tag, 仅依赖 stdlib.
# 单租户 ERP, DB 已私有, 主要为防 dump 后明文泄露。 jwt_secret 改了之后旧密文失效。
# 历史密文是用旧的默认 jwt_secret 加密的; 固化成常量, 使加密基与"可被改动的
# JWT_SECRET 环境变量"彻底解耦 —— 否则运维为加固登录设置 JWT_SECRET 会连带改掉加密基,
# 令已存的 AI/OCR key 全部无法解密 (静默失效)。
_LEGACY_ENC_BASE = "panse-dev-secret-CHANGE-ME-in-production"


def _enc_base() -> bytes:
    """加密基密钥: SETTINGS_ENCRYPTION_KEY 环境变量优先; 未设置则用历史默认常量
    (向后兼容, 旧密文正是用它加密的, 且不随 JWT_SECRET 变化)。
    注: 一旦设置独立的 SETTINGS_ENCRYPTION_KEY, 需到后台重新录入 AI/OCR key。"""
    explicit = (os.environ.get("SETTINGS_ENCRYPTION_KEY") or "").strip()
    if explicit:
        return explicit.encode("utf-8")
    # 安全告警: 用源码内置默认密钥加密 ≈ 明文 (任何拿到源码者皆可解密)。生产应设独立 key。
    # 只告警一次, 避免刷屏; 不硬崩溃 (否则看门狗会陷入重启循环)。
    if not getattr(_enc_base, "_warned", False):
        _enc_base._warned = True
        logging.getLogger("panse.settings").warning(
            "SETTINGS_ENCRYPTION_KEY 未设置, 机密配置正用源码内置默认密钥加密 (≈明文)。"
            "生产环境请设置该环境变量后到后台重新录入机密 (AI/OCR/快递 key)。"
        )
    return _LEGACY_ENC_BASE.encode("utf-8")


def _keys() -> tuple[bytes, bytes]:
    base = _enc_base()
    enc_key = hashlib.sha256(b"panse-enc|" + base).digest()
    mac_key = hashlib.sha256(b"panse-mac|" + base).digest()
    return enc_key, mac_key


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    """生成 n 字节 keystream: HMAC(key, nonce||ctr)."""
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out.extend(hmac.new(key, nonce + ctr.to_bytes(4, "big"), hashlib.sha256).digest())
        ctr += 1
    return bytes(out[:n])


def encrypt(value: str) -> str:
    enc_key, mac_key = _keys()
    pt = value.encode("utf-8")
    nonce = secrets.token_bytes(16)
    ks = _stream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")


def decrypt(token: str) -> str:
    enc_key, mac_key = _keys()
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception:
        return ""
    if len(raw) < 32:  # 16 nonce + 16 tag minimum
        return ""
    nonce, body, tag = raw[:16], raw[16:-16], raw[-16:]
    expected = hmac.new(mac_key, nonce + body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        return ""
    ks = _stream(enc_key, nonce, len(body))
    return bytes(a ^ b for a, b in zip(body, ks)).decode("utf-8", errors="replace")


def is_secret(key: str) -> bool:
    return key in _SECRET_KEYS


def get(db: Session, key: str, *, env_fallback: bool = True) -> Optional[str]:
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is not None:
        if row.is_secret and row.value_encrypted:
            return decrypt(row.value_encrypted) or None
        if row.value_plain:
            return row.value_plain
    if env_fallback:
        v = os.environ.get(key.upper())
        if v:
            return v
    return None


def set_value(db: Session, key: str, value: str, *, description: Optional[str] = None) -> SystemSetting:
    """写入 / 更新一个设置项。空值视为清除。"""
    secret = is_secret(key)
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=key, is_secret=secret, description=description)
        db.add(row)
    if not value:
        row.value_plain = None
        row.value_encrypted = None
    elif secret:
        row.value_plain = None
        row.value_encrypted = encrypt(value)
    else:
        row.value_plain = value
        row.value_encrypted = None
    if description:
        row.description = description
    db.flush()
    return row


def mask_secret(value: Optional[str]) -> str:
    """sk-abc...xyz98 — 仅前 3 后 4 可见。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


# AI 配置一组：方便业务代码直接拿一份 provider config。
def _raw_ai_config(db: Session, kind: str) -> dict:
    return {
        "provider": get(db, f"ai_{kind}_provider"),
        "base_url": get(db, f"ai_{kind}_base_url"),
        "api_key": get(db, f"ai_{kind}_api_key"),
        "model": get(db, f"ai_{kind}_model"),
        "user_agent": get(db, f"ai_{kind}_user_agent"),
    }


def get_ai_config(db: Session, kind: str) -> dict:
    """kind in {'diagnose', 'ocr'}.

    返回 {provider, base_url, api_key, model}。

    单把 key 全局生效: 本槽位没填 api_key 时, 整组回落到另一槽位
    (diagnose ↔ ocr), 再回落到 env ANTHROPIC_API_KEY / ai_model。
    这样用户只在任意一处配 key, AI 助手 + 截图 OCR 都能用。
    """
    assert kind in {"diagnose", "ocr", "ocr_fallback", "custom"}
    settings = get_settings()
    # ocr_fallback: 独立兜底槽位 (如本机 Ollama), 不交叉回落、不借用 anthropic key —
    # 没配就返回空, 让调用方跳过。主 OCR 额度用光/报错时自动切到它, 保持自动化不退人工。
    if kind == "ocr_fallback":
        own = _raw_ai_config(db, "ocr_fallback")
        # 默认指向本机 Ollama 标准端点(OCR 兜底, 视觉模型): 用户 `ollama pull qwen2.5vl` 即自动兜底。
        # (Ollama 没起/没拉模型时, 兜底调用会连接失败 → 调用方安全报异常, 不会写错数据)
        # 注: OCR 主路径走云端(uniapi); qwen3.5/qwen3-vl 思考型不适合 OCR(答案落 thinking), 故 OCR 兜底仍用 qwen2.5vl。
        return {
            "provider": own["provider"] or "openai",   # Ollama 走 OpenAI 兼容协议
            "base_url": own["base_url"] or "http://host.docker.internal:11434/v1",
            "api_key": own["api_key"] or "ollama",      # Ollama 不校验 key, 占位即可
            "model": own["model"] or "qwen2.5vl",       # OCR 兜底用视觉模型 qwen2.5vl(非思考型)
            "user_agent": own.get("user_agent") or "",
        }
    # custom (定制报价分类器): 没配 key 回落到 ocr; diagnose↔ocr 互相回落
    if kind == "custom":
        own = _raw_ai_config(db, "custom")
        src = own if own["api_key"] else _raw_ai_config(db, "ocr")
    else:
        other = "ocr" if kind == "diagnose" else "diagnose"
        own = _raw_ai_config(db, kind)
        src = own if own["api_key"] else _raw_ai_config(db, other)
    return {
        "provider": src["provider"] or "anthropic",
        "base_url": src["base_url"] or "",
        "api_key": src["api_key"] or settings.anthropic_api_key,
        "model": src["model"] or settings.ai_model,
        "user_agent": src.get("user_agent") or "",
    }
