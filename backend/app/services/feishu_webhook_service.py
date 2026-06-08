"""飞书事件回调 (Webhook) 处理 — 近实时同步 (用户需求: 飞书改完不用等 30 分钟轮询)。

飞书「事件订阅」会 POST 到本服务:
  1. URL 验证: {"type":"url_verification","challenge":"..","token":".."} → 回 {"challenge":..}
  2. 事件推送: 多维表记录变更 (drive.file.bitable_record_changed_v1) → 触发该绑定立即同步。

安全:
  - Verification Token (system_settings.feishu_verification_token) 校验来源。
  - 若飞书后台开了 Encrypt Key, body 为 {"encrypt":".."} (AES-256-CBC), 用
    system_settings.feishu_encrypt_key 解密。
两者都未配置时退化为"不校验/不解密"(便于自建场景), 但生产建议都配上。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import feishu_sync_service, settings_service

_log = logging.getLogger("panse.feishu_webhook")


def decrypt(encrypt_b64: str, key: str) -> str:
    """飞书 Encrypt Key 解密 (AES-256-CBC, key=sha256(encrypt_key), 前16字节为IV, PKCS7)。"""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    digest = hashlib.sha256(key.encode("utf-8")).digest()
    data = base64.b64decode(encrypt_b64)
    iv, ct = data[:16], data[16:]
    decryptor = Cipher(algorithms.AES(digest), modes.CBC(iv), backend=default_backend()).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    pad = padded[-1]
    return padded[:-pad].decode("utf-8")


def handle(db: Session, body: dict[str, Any]) -> dict:
    """处理一次飞书回调, 返回应答 (challenge 或 {})。"""
    encrypt_key = settings_service.get(db, "feishu_encrypt_key", env_fallback=False)
    if isinstance(body, dict) and "encrypt" in body:
        if not encrypt_key:
            raise ValueError("收到加密事件, 但未配置 feishu_encrypt_key")
        body = json.loads(decrypt(body["encrypt"], encrypt_key))

    expected = settings_service.get(db, "feishu_verification_token", env_fallback=False)
    token = body.get("token") or (body.get("header") or {}).get("token")
    if expected and token and token != expected:
        raise PermissionError("verification token 不匹配")

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    header = body.get("header") or {}
    event = body.get("event") or {}
    event_type = header.get("event_type") or event.get("type") or body.get("type")
    _maybe_trigger_sync(db, event_type, event)
    _maybe_bot(db, event_type, event)
    return {}


def _maybe_bot(db: Session, event_type: str | None, event: dict) -> None:
    """机器人: 收图消息(im.message.receive_v1) / 卡片按钮(card.action.trigger)
    → 分发到 feishu_bot_service。尽力而为, 出错只记日志不让 webhook 500。"""
    if not event_type:
        return
    try:
        from app.services import feishu_bot_service
        if "im.message.receive" in event_type:
            feishu_bot_service.on_message_event(db, event)
            db.commit()
        elif "card.action.trigger" in event_type:
            feishu_bot_service.on_card_action(db, event)
            db.commit()
    except Exception as e:  # pragma: no cover
        db.rollback()
        _log.error("飞书机器人事件处理失败: %s", e)


def _maybe_trigger_sync(db: Session, event_type: str | None, event: dict) -> None:
    """多维表记录变更 → 找到对应绑定立即同步 (尽力而为, 不抛错)。"""
    if not event_type or "bitable_record" not in event_type:
        return
    from app.models.feishu_sync import FeishuTableBinding

    table_id = event.get("table_id")
    q = select(FeishuTableBinding).where(FeishuTableBinding.enabled.is_(True))
    if table_id:
        q = q.where(FeishuTableBinding.feishu_table_id == table_id)
    for b in db.execute(q).scalars().all():
        try:
            feishu_sync_service.sync_binding(db, b)
            db.commit()
            _log.info("webhook 触发同步 %s", b.system_table)
        except Exception as e:  # pragma: no cover
            db.rollback()
            _log.error("webhook 同步 %s 失败: %s", b.system_table, e)
