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
}


# 静态加密 (Encryption At Rest) — 用 HMAC-SHA256 做 CTR 流 + HMAC tag, 仅依赖 stdlib.
# 单租户 ERP, DB 已私有, 主要为防 dump 后明文泄露。 jwt_secret 改了之后旧密文失效。
def _enc_base() -> bytes:
    """加密基密钥: SETTINGS_ENCRYPTION_KEY 环境变量优先 (与 JWT 签名密钥彻底分离);
    未设置则退回 jwt_secret —— 向后兼容, 旧密文正是用它加密的。
    注: 一旦设置独立的 SETTINGS_ENCRYPTION_KEY, 需到后台重新录入 AI/OCR key。"""
    explicit = (os.environ.get("SETTINGS_ENCRYPTION_KEY") or "").strip()
    if explicit:
        return explicit.encode("utf-8")
    return get_settings().jwt_secret.encode("utf-8")


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
    }


def get_ai_config(db: Session, kind: str) -> dict:
    """kind in {'diagnose', 'ocr'}.

    返回 {provider, base_url, api_key, model}。

    单把 key 全局生效: 本槽位没填 api_key 时, 整组回落到另一槽位
    (diagnose ↔ ocr), 再回落到 env ANTHROPIC_API_KEY / ai_model。
    这样用户只在任意一处配 key, AI 助手 + 截图 OCR 都能用。
    """
    assert kind in {"diagnose", "ocr"}
    settings = get_settings()
    other = "ocr" if kind == "diagnose" else "diagnose"
    own = _raw_ai_config(db, kind)
    src = own if own["api_key"] else _raw_ai_config(db, other)
    return {
        "provider": src["provider"] or "anthropic",
        "base_url": src["base_url"] or "",
        "api_key": src["api_key"] or settings.anthropic_api_key,
        "model": src["model"] or settings.ai_model,
    }
