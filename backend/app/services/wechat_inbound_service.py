"""企业微信自建应用回调：接收发货报表密码并续跑订单链路。

群机器人 webhook 只负责 ERP -> 企业微信群的出站通知；入站消息必须使用
企业微信自建应用的消息回调。此模块严格执行签名校验、AES 解密、企业 ID
校验和发送人成员白名单，且不会把密码写入日志或回执。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services import settings_service

_log = logging.getLogger("panse.wechat_inbound")

CALLBACK_PATH = "/api/wechat/callback"
MAX_CALLBACK_BYTES = 256 * 1024
MAX_CLOCK_SKEW_SECONDS = 10 * 60
SEEN_MESSAGE_LIMIT = 100

KEY_ENABLED = "wechat_inbound_enabled"
KEY_CORP_ID = "wechat_inbound_corp_id"
KEY_TOKEN = "wechat_inbound_token"
KEY_AES_KEY = "wechat_inbound_aes_key"
KEY_ALLOWED_USERS = "wechat_inbound_allowed_users"
KEY_SEEN_MESSAGES = "wechat_inbound_seen_messages"

_PASSWORD_RE = re.compile(r"^\s*发货密码(?:\s*[:：]\s*|\s+)(\S{4,128})\s*$")


class WechatInboundError(ValueError):
    """回调格式、配置或密文无效。"""


class WechatInboundForbidden(PermissionError):
    """签名、企业或成员校验失败。"""


@dataclass(frozen=True)
class InboundCommand:
    message_id: str
    sender: str
    password: str


def _enabled(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _allowed_users(raw: Optional[str]) -> list[str]:
    return sorted({item.strip() for item in str(raw or "").split(",") if item.strip()})


def get_config(db: Session) -> dict:
    corp_id = settings_service.get(db, KEY_CORP_ID, env_fallback=False) or ""
    token = settings_service.get(db, KEY_TOKEN, env_fallback=False) or ""
    aes_key = settings_service.get(db, KEY_AES_KEY, env_fallback=False) or ""
    allowed_users = _allowed_users(
        settings_service.get(db, KEY_ALLOWED_USERS, env_fallback=False)
    )
    ready = bool(corp_id and token and aes_key and allowed_users)
    return {
        "enabled": _enabled(settings_service.get(db, KEY_ENABLED, env_fallback=False)),
        "corp_id": corp_id,
        "token": token,
        "aes_key": aes_key,
        "allowed_users": allowed_users,
        "ready": ready,
    }


def validate_aes_key(aes_key: str) -> bytes:
    value = (aes_key or "").strip()
    if len(value) != 43:
        raise WechatInboundError("EncodingAESKey 必须是 43 位")
    try:
        key = base64.b64decode(value + "=", validate=True)
    except Exception as exc:  # noqa: BLE001
        raise WechatInboundError("EncodingAESKey 格式无效") from exc
    if len(key) != 32:
        raise WechatInboundError("EncodingAESKey 解码后必须是 32 字节")
    return key


def validate_config(config: dict, *, require_enabled: bool = True) -> None:
    if require_enabled and not config.get("enabled"):
        raise WechatInboundError("企业微信密码接收尚未启用")
    if not config.get("corp_id"):
        raise WechatInboundError("未配置企业 ID")
    if not config.get("token"):
        raise WechatInboundError("未配置回调 Token")
    validate_aes_key(str(config.get("aes_key") or ""))
    if not config.get("allowed_users"):
        raise WechatInboundError("必须配置至少一个允许发送密码的成员 UserID")


def signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    parts = sorted([token, timestamp, nonce, encrypted])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()  # noqa: S324 - 企业微信协议


def _verify_signature(
    token: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
    supplied: str,
) -> None:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise WechatInboundForbidden("回调时间戳无效") from exc
    if abs(int(time.time()) - ts) > MAX_CLOCK_SKEW_SECONDS:
        raise WechatInboundForbidden("回调时间戳已过期")
    expected = signature(token, str(timestamp), nonce, encrypted)
    if not hmac.compare_digest(expected, supplied or ""):
        raise WechatInboundForbidden("回调签名不匹配")


def decrypt_message(encrypted: str, aes_key: str, corp_id: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = validate_aes_key(aes_key)
    try:
        ciphertext = base64.b64decode(encrypted, validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
    except Exception as exc:  # noqa: BLE001
        raise WechatInboundForbidden("回调密文无法解密") from exc
    if not padded:
        raise WechatInboundForbidden("回调密文为空")
    pad = padded[-1]
    if pad < 1 or pad > 32 or padded[-pad:] != bytes([pad]) * pad:
        raise WechatInboundForbidden("回调密文填充无效")
    plain = padded[:-pad]
    if len(plain) < 20:
        raise WechatInboundForbidden("回调明文长度无效")
    message_len = struct.unpack("!I", plain[16:20])[0]
    if message_len < 0 or 20 + message_len > len(plain):
        raise WechatInboundForbidden("回调消息长度无效")
    message = plain[20:20 + message_len]
    receiver = plain[20 + message_len:].decode("utf-8", errors="strict")
    if not hmac.compare_digest(receiver, corp_id):
        raise WechatInboundForbidden("回调企业 ID 不匹配")
    try:
        return message.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WechatInboundForbidden("回调消息编码无效") from exc


def encrypt_message(message: str, aes_key: str, corp_id: str) -> str:
    """按企业微信协议加密；主要用于回调协议测试。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = validate_aes_key(aes_key)
    message_bytes = message.encode("utf-8")
    plain = (
        os.urandom(16)
        + struct.pack("!I", len(message_bytes))
        + message_bytes
        + corp_id.encode("utf-8")
    )
    pad = 32 - (len(plain) % 32)
    padded = plain + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _xml_root(xml_text: str) -> ET.Element:
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise WechatInboundError("不接受带 DTD/ENTITY 的 XML")
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WechatInboundError("回调 XML 格式无效") from exc


def _xml_text(root: ET.Element, tag: str) -> str:
    node = root.find(tag)
    return (node.text or "").strip() if node is not None else ""


def decrypt_url_verification(
    db: Session,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echo_str: str,
) -> str:
    config = get_config(db)
    validate_config(config)
    _verify_signature(
        config["token"], timestamp, nonce, echo_str, msg_signature,
    )
    return decrypt_message(echo_str, config["aes_key"], config["corp_id"])


def _seen_ids(db: Session) -> list[str]:
    raw = settings_service.get(db, KEY_SEEN_MESSAGES, env_fallback=False) or "[]"
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if item][-SEEN_MESSAGE_LIMIT:]


def _claim_message(db: Session, message_id: str) -> bool:
    seen = _seen_ids(db)
    if message_id in seen:
        return False
    seen.append(message_id)
    settings_service.set_value(
        db,
        KEY_SEEN_MESSAGES,
        json.dumps(seen[-SEEN_MESSAGE_LIMIT:], ensure_ascii=False),
        description="企业微信入站消息去重 ID（不含消息内容）",
    )
    db.commit()
    return True


def accept_callback(
    db: Session,
    *,
    body: bytes,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> Optional[InboundCommand]:
    config = get_config(db)
    validate_config(config)
    if len(body) > MAX_CALLBACK_BYTES:
        raise WechatInboundError("回调消息过大")
    try:
        outer_text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WechatInboundError("回调 XML 编码无效") from exc
    outer = _xml_root(outer_text)
    encrypted = _xml_text(outer, "Encrypt")
    if not encrypted:
        raise WechatInboundError("回调缺少 Encrypt")
    _verify_signature(
        config["token"], timestamp, nonce, encrypted, msg_signature,
    )
    inner = _xml_root(decrypt_message(encrypted, config["aes_key"], config["corp_id"]))
    if _xml_text(inner, "MsgType").lower() != "text":
        return None
    sender = _xml_text(inner, "FromUserName")
    if sender not in config["allowed_users"]:
        raise WechatInboundForbidden("发送人不在允许成员名单")
    content = _xml_text(inner, "Content")
    match = _PASSWORD_RE.match(content)
    if not match:
        return None
    message_id = _xml_text(inner, "MsgId")
    if not message_id:
        raise WechatInboundError("文本消息缺少 MsgId")
    if not _claim_message(db, message_id):
        return None
    return InboundCommand(
        message_id=message_id,
        sender=sender,
        password=match.group(1),
    )


def _process_shipping_password(password: str) -> None:
    db = SessionLocal()
    try:
        from app.services import feishu_bot_service

        result = feishu_bot_service.apply_shipping_password(db, password)
        if result.get("imported"):
            return  # 核心链路已发送企业微信结果通知
        from app.services import notify_service

        if result.get("failure_reason"):
            text = "收到的发货密码与当前待处理报表不匹配，请重新获取最新密码。"
            level = "warn"
        else:
            text = "发货密码已安全保存；当前没有待解密报表，后续导入时会自动使用。"
            level = "info"
        notify_service.notify(
            db,
            text,
            level=level,
            title="畔色 ERP | 微信密码接收结果",
            wechat_allowed=True,
        )
    except Exception:  # noqa: BLE001
        db.rollback()
        _log.exception("企业微信发货密码处理失败（密码内容已隐藏）")
    finally:
        db.close()


def dispatch(command: InboundCommand) -> None:
    """先向企业微信快速应答，再在后台执行耗时的解密、导入和续推。"""
    thread = threading.Thread(
        target=_process_shipping_password,
        args=(command.password,),
        name="wechat-shipping-password",
        daemon=True,
    )
    thread.start()
