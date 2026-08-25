"""企业微信应用/智能机器人回调：接收发货报表密码并续跑订单链路。

群机器人 webhook 只负责 ERP -> 企业微信群的出站通知；群聊入站使用企业微信
智能机器人，单聊入站可继续使用自建应用。此模块严格执行签名校验、AES 解密、
接收方校验和发送人成员白名单，且不会把密码写入日志或回执。
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
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services import settings_service

_log = logging.getLogger("panse.wechat_inbound")

CALLBACK_PATH = "/api/wechat/callback"
AIBOT_CALLBACK_PATH = "/api/wechat/aibot/callback"
MAX_CALLBACK_BYTES = 256 * 1024
MAX_CLOCK_SKEW_SECONDS = 10 * 60
SEEN_MESSAGE_LIMIT = 100

KEY_ENABLED = "wechat_inbound_enabled"
KEY_CORP_ID = "wechat_inbound_corp_id"
KEY_TOKEN = "wechat_inbound_token"
KEY_AES_KEY = "wechat_inbound_aes_key"
KEY_ALLOWED_USERS = "wechat_inbound_allowed_users"
KEY_SEEN_MESSAGES = "wechat_inbound_seen_messages"

KEY_AIBOT_ENABLED = "wechat_aibot_enabled"
KEY_AIBOT_TOKEN = "wechat_aibot_token"
KEY_AIBOT_AES_KEY = "wechat_aibot_aes_key"
KEY_AIBOT_NAME = "wechat_aibot_name"
KEY_AIBOT_SEEN_MESSAGES = "wechat_aibot_seen_messages"

_LABELLED_PASSWORD_RE = re.compile(
    r"(?:^|\s)(?:发货密码|发货口令|密码|口令)\s*(?:[:：=]\s*)?(\S{4,128})\s*$",
    re.IGNORECASE,
)
_BARE_PASSWORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@#%+=~\-]{3,127}$")


class WechatInboundError(ValueError):
    """回调格式、配置或密文无效。"""


class WechatInboundForbidden(PermissionError):
    """签名、企业或成员校验失败。"""


@dataclass(frozen=True)
class InboundCommand:
    message_id: str
    sender: str
    password: str
    response_url: str = ""


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


def get_aibot_config(db: Session) -> dict:
    token = settings_service.get(db, KEY_AIBOT_TOKEN, env_fallback=False) or ""
    aes_key = settings_service.get(db, KEY_AIBOT_AES_KEY, env_fallback=False) or ""
    bot_name = settings_service.get(db, KEY_AIBOT_NAME, env_fallback=False) or ""
    allowed_users = _allowed_users(
        settings_service.get(db, KEY_ALLOWED_USERS, env_fallback=False)
    )
    ready = bool(token and aes_key and bot_name and allowed_users)
    return {
        "enabled": _enabled(
            settings_service.get(db, KEY_AIBOT_ENABLED, env_fallback=False)
        ),
        "token": token,
        "aes_key": aes_key,
        "bot_name": bot_name,
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


def validate_aibot_config(config: dict, *, require_enabled: bool = True) -> None:
    if require_enabled and not config.get("enabled"):
        raise WechatInboundError("企业微信群聊智能机器人尚未启用")
    if not config.get("token"):
        raise WechatInboundError("未配置智能机器人回调 Token")
    validate_aes_key(str(config.get("aes_key") or ""))
    if not str(config.get("bot_name") or "").strip():
        raise WechatInboundError("未配置智能机器人名称")
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


def decrypt_aibot_url_verification(
    db: Session,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echo_str: str,
) -> str:
    config = get_aibot_config(db)
    validate_aibot_config(config)
    _verify_signature(
        config["token"], timestamp, nonce, echo_str, msg_signature,
    )
    # 企业内部智能机器人协议规定 ReceiveId 为空字符串。
    return decrypt_message(echo_str, config["aes_key"], "")


def _seen_ids(db: Session, key: str = KEY_SEEN_MESSAGES) -> list[str]:
    raw = settings_service.get(db, key, env_fallback=False) or "[]"
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if item][-SEEN_MESSAGE_LIMIT:]


def _claim_message(
    db: Session,
    message_id: str,
    *,
    key: str = KEY_SEEN_MESSAGES,
) -> bool:
    seen = _seen_ids(db, key)
    if message_id in seen:
        return False
    seen.append(message_id)
    settings_service.set_value(
        db,
        key,
        json.dumps(seen[-SEEN_MESSAGE_LIMIT:], ensure_ascii=False),
        description="企业微信入站消息去重 ID（不含消息内容）",
    )
    db.commit()
    return True


def extract_shipping_password(
    content: str,
    *,
    allow_bare: bool = False,
    bot_name: str = "",
) -> Optional[str]:
    """提取密码；裸密码仅供已鉴权且被 @ 的智能机器人消息使用。"""
    value = str(content or "").strip()
    if not value:
        return None
    match = _LABELLED_PASSWORD_RE.search(value)
    if match:
        return match.group(1)
    if not allow_bare:
        return None
    name = str(bot_name or "").strip()
    if name and value.startswith(f"@{name}"):
        value = value[len(name) + 1:].strip()
    # 智能机器人群回调只在 @ 机器人后产生；移除精确机器人名后，必须只剩一个
    # ASCII 密码 token，避免把普通群聊语句误当成密码。
    if _BARE_PASSWORD_RE.fullmatch(value):
        return value
    return None


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
    password = extract_shipping_password(content)
    if not password:
        return None
    message_id = _xml_text(inner, "MsgId")
    if not message_id:
        raise WechatInboundError("文本消息缺少 MsgId")
    if not _claim_message(db, message_id):
        return None
    return InboundCommand(
        message_id=message_id,
        sender=sender,
        password=password,
    )


def accept_aibot_callback(
    db: Session,
    *,
    body: bytes,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> Optional[InboundCommand]:
    config = get_aibot_config(db)
    validate_aibot_config(config)
    if len(body) > MAX_CALLBACK_BYTES:
        raise WechatInboundError("回调消息过大")
    try:
        outer = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WechatInboundError("智能机器人回调 JSON 格式无效") from exc
    if not isinstance(outer, dict):
        raise WechatInboundError("智能机器人回调 JSON 格式无效")
    encrypted = str(outer.get("encrypt") or "")
    if not encrypted:
        raise WechatInboundError("智能机器人回调缺少 encrypt")
    _verify_signature(
        config["token"], timestamp, nonce, encrypted, msg_signature,
    )
    plain = decrypt_message(encrypted, config["aes_key"], "")
    try:
        payload = json.loads(plain)
    except json.JSONDecodeError as exc:
        raise WechatInboundError("智能机器人回调明文 JSON 格式无效") from exc
    if not isinstance(payload, dict) or str(payload.get("msgtype") or "").lower() != "text":
        return None
    sender_info = payload.get("from")
    sender = str(sender_info.get("userid") or "") if isinstance(sender_info, dict) else ""
    if sender not in config["allowed_users"]:
        raise WechatInboundForbidden("发送人不在允许成员名单")
    chattype = str(payload.get("chattype") or "").lower()
    if chattype not in {"single", "group"}:
        raise WechatInboundError("智能机器人会话类型无效")
    text_info = payload.get("text")
    content = str(text_info.get("content") or "") if isinstance(text_info, dict) else ""
    password = extract_shipping_password(
        content,
        allow_bare=True,
        bot_name=config["bot_name"],
    )
    if not password:
        return None
    message_id = str(payload.get("msgid") or "")
    if not message_id:
        raise WechatInboundError("文本消息缺少 msgid")
    if not _claim_message(db, message_id, key=KEY_AIBOT_SEEN_MESSAGES):
        return None
    response_url = str(payload.get("response_url") or "")
    if response_url and not _valid_response_url(response_url):
        raise WechatInboundForbidden("智能机器人回复地址无效")
    return InboundCommand(
        message_id=message_id,
        sender=sender,
        password=password,
        response_url=response_url,
    )


def _valid_response_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() == "qyapi.weixin.qq.com"
        and parts.path == "/cgi-bin/aibot/response"
        and bool(parts.query)
    )


def _acknowledge_aibot(response_url: str) -> None:
    if not response_url:
        return
    import requests

    response = requests.post(
        response_url,
        json={
            "msgtype": "markdown",
            "markdown": {"content": "已安全接收发货密码，正在解密并续推订单（密码不回显）。"},
        },
        timeout=10,
    )
    response.raise_for_status()
    if response.content:
        try:
            result = response.json()
        except ValueError:
            result = None
        if isinstance(result, dict) and int(result.get("errcode") or 0) != 0:
            raise RuntimeError("企业微信智能机器人确认回复被平台拒绝")


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


def _process_command(command: InboundCommand) -> None:
    if command.response_url:
        try:
            _acknowledge_aibot(command.response_url)
        except Exception:  # noqa: BLE001
            _log.exception("企业微信智能机器人确认回复失败（回复地址已隐藏）")
    _process_shipping_password(command.password)


def dispatch(command: InboundCommand) -> None:
    """先向企业微信快速应答，再在后台执行耗时的解密、导入和续推。"""
    thread = threading.Thread(
        target=_process_command,
        args=(command,),
        name="wechat-shipping-password",
        daemon=True,
    )
    thread.start()
