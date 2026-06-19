"""飞书开放平台 HTTP 客户端 (多维表格 Bitable).

封装:
    - tenant_access_token 获取 + 进程内缓存 (~2h 有效)
    - Bitable 记录 CRUD (list / create / update / delete)

凭证 (app_id / app_secret) 存 system_settings, 后台可改。
失败统一抛 FeishuError, 上层 (feishu_sync_service) 捕获并写冲突/告警。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.feishu")

_BASE = "https://open.feishu.cn/open-apis"
_TIMEOUT = 15

# 进程内 token 缓存: app_id -> (token, expire_ts)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class FeishuError(RuntimeError):
    """飞书 API 调用失败 (凭证错 / 网络错 / 业务码非 0)."""

    def __init__(self, message: str, *, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def get_credentials(db: Session) -> tuple[str, str]:
    app_id = settings_service.get(db, "feishu_app_id", env_fallback=False)
    app_secret = settings_service.get(db, "feishu_app_secret", env_fallback=False)
    if not app_id or not app_secret:
        raise FeishuError("飞书未配置: 请到 管理 → 飞书 填 app_id / app_secret")
    return app_id, app_secret


def get_tenant_access_token(db: Session, *, force: bool = False) -> str:
    app_id, app_secret = get_credentials(db)
    now = time.time()
    cached = _TOKEN_CACHE.get(app_id)
    if cached and not force and cached[1] > now + 60:
        return cached[0]
    try:
        r = httpx.post(
            f"{_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise FeishuError(f"获取飞书 token 网络失败: {e}") from e
    data = _json(r)
    if data.get("code") != 0:
        hint = "，请到 管理→飞书 核对 app_id / app_secret" if data.get("code") in (10003, 10014) else ""
        raise FeishuError(f"获取飞书 token 失败: {data.get('msg')} (code={data.get('code')}){hint}")
    token = data["tenant_access_token"]
    _TOKEN_CACHE[app_id] = (token, now + int(data.get("expire", 7200)))
    return token


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except ValueError as e:
        raise FeishuError(f"飞书返回非 JSON (HTTP {r.status_code}): {r.text[:200]}") from e


def _headers(db: Session) -> dict:
    return {"Authorization": f"Bearer {get_tenant_access_token(db)}",
            "Content-Type": "application/json; charset=utf-8"}


def _req(db: Session, method: str, url: str, **kwargs) -> dict:
    try:
        r = httpx.request(method, url, headers=_headers(db), timeout=_TIMEOUT, **kwargs)
    except httpx.HTTPError as e:
        raise FeishuError(f"飞书请求网络失败: {e}") from e
    data = _json(r)
    if data.get("code") != 0:
        raise FeishuError(
            f"飞书 API 错误: {data.get('msg')} (code={data.get('code')})",
            code=data.get("code"),
        )
    return data.get("data", {})


# ── 机器人: 消息图片下载 + 回交互卡片 ──────────────────────────
def download_message_resource(db: Session, message_id: str, file_key: str,
                              *, type_: str = "image") -> bytes:
    """下载消息里的图片/文件原始字节 (im/v1/messages/{id}/resources/{key})。"""
    url = f"{_BASE}/im/v1/messages/{message_id}/resources/{file_key}?type={type_}"
    try:
        r = httpx.get(
            url, headers={"Authorization": f"Bearer {get_tenant_access_token(db)}"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise FeishuError(f"飞书下载图片网络失败: {e}") from e
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or ctype.startswith("application/json"):
        raise FeishuError(f"飞书下载图片失败: HTTP {r.status_code} {r.text[:120]}")
    return r.content


def reply_card(db: Session, message_id: str, card: dict) -> dict:
    """回复一条消息, 内容为交互卡片 (im/v1/messages/{id}/reply)。"""
    url = f"{_BASE}/im/v1/messages/{message_id}/reply"
    body = {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}
    return _req(db, "POST", url, json=body)


def reply_text(db: Session, message_id: str, text: str) -> dict:
    """回复一条纯文本消息。"""
    url = f"{_BASE}/im/v1/messages/{message_id}/reply"
    body = {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
    return _req(db, "POST", url, json=body)


def patch_card(db: Session, message_id: str, card: dict) -> dict:
    """更新一条已发送的卡片消息 (im/v1/messages/{id} PATCH) — 卡片回调延时更新(30分钟内)用。"""
    url = f"{_BASE}/im/v1/messages/{message_id}"
    body = {"content": json.dumps(card, ensure_ascii=False)}
    return _req(db, "PATCH", url, json=body)


# ── 主动外发: 给指定会话发文本/图片 (二维码/文件推送, 2026-06-12) ────────────
def upload_image(db: Session, png_bytes: bytes) -> str:
    """上传图片到飞书, 返回 image_key (im/v1/images, image_type=message)。"""
    url = f"{_BASE}/im/v1/images"
    # 按字节签名识别格式 (下单图改用 JPEG 体积小10倍, 二维码仍 PNG); content-type 与字节一致, 防飞书拒收。
    _fmt = "jpeg" if png_bytes[:3] == b"\xff\xd8\xff" else "png"
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {get_tenant_access_token(db)}"},
            files={"image": (f"img.{_fmt}", png_bytes, f"image/{_fmt}")},
            data={"image_type": "message"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise FeishuError(f"飞书上传图片网络失败: {e}") from e
    data = _json(r)
    if data.get("code") != 0:
        raise FeishuError(f"飞书上传图片失败: {data.get('msg')} (code={data.get('code')})")
    return (data.get("data") or {}).get("image_key", "")


def send_text(db: Session, receive_id: str, text: str,
              *, id_type: str = "chat_id") -> dict:
    """主动给指定会话/用户发纯文本 (im/v1/messages)。"""
    url = f"{_BASE}/im/v1/messages?receive_id_type={id_type}"
    body = {"receive_id": receive_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False)}
    return _req(db, "POST", url, json=body)


def send_image(db: Session, receive_id: str, image_key: str,
               *, id_type: str = "chat_id") -> dict:
    """主动给指定会话/用户发图片 (im/v1/messages, msg_type=image)。"""
    url = f"{_BASE}/im/v1/messages?receive_id_type={id_type}"
    body = {"receive_id": receive_id, "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False)}
    return _req(db, "POST", url, json=body)


def upload_file(db: Session, file_bytes: bytes, file_name: str, *, file_type: str = "stream") -> str:
    """上传文件到飞书, 返回 file_key (im/v1/files)。zip/excel 等非图片用 file_type=stream。"""
    url = f"{_BASE}/im/v1/files"
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {get_tenant_access_token(db)}"},
            files={"file": (file_name, file_bytes, "application/octet-stream")},
            data={"file_type": file_type, "file_name": file_name},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise FeishuError(f"飞书上传文件网络失败: {e}") from e
    data = _json(r)
    if data.get("code") != 0:
        raise FeishuError(f"飞书上传文件失败: {data.get('msg')} (code={data.get('code')})")
    return (data.get("data") or {}).get("file_key", "")


def send_file(db: Session, receive_id: str, file_key: str, *, id_type: str = "chat_id") -> dict:
    """主动给指定会话发文件 (im/v1/messages, msg_type=file)。"""
    url = f"{_BASE}/im/v1/messages?receive_id_type={id_type}"
    body = {"receive_id": receive_id, "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False)}
    return _req(db, "POST", url, json=body)


def list_records(db: Session, app_token: str, table_id: str,
                 *, page_size: int = 500) -> list[dict]:
    """拉取一张 Bitable 表的全部记录 (自动翻页).

    返回 [{record_id, fields, last_modified_time?}, ...]。
    """
    out: list[dict] = []
    page_token: Optional[str] = None
    base = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    while True:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        data = _req(db, "GET", base, params=params)
        for item in data.get("items", []) or []:
            out.append({
                "record_id": item.get("record_id"),
                "fields": item.get("fields", {}),
                "last_modified_time": item.get("last_modified_time"),
            })
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return out


def create_record(db: Session, app_token: str, table_id: str, fields: dict) -> str:
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    data = _req(db, "POST", url, json={"fields": fields})
    return (data.get("record") or {}).get("record_id", "")


def update_record(db: Session, app_token: str, table_id: str,
                  record_id: str, fields: dict) -> None:
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    _req(db, "PUT", url, json={"fields": fields})


def delete_record(db: Session, app_token: str, table_id: str, record_id: str) -> None:
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    _req(db, "DELETE", url)


# 飞书业务错误码
ERR_RECORD_NOT_FOUND = 1254043   # RecordIdNotFound: 要删的记录已不存在
ERR_TABLE_NOT_FOUND = 1254041    # TableIdNotFound: 表已不存在 (绑定失效)


def batch_delete_records(db: Session, app_token: str, table_id: str,
                         record_ids: list[str]) -> int:
    """批量删除记录 (每次最多 500 条, 自动分批). 返回删除条数。

    调用: POST /bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete
    body: {"records": ["recXXX", ...]}

    容错: 整批遇 RecordIdNotFound (部分记录已不存在) 时降级逐条删除,
    逐条仍遇 NotFound 则跳过 (记录不存在即达成删除目标, 幂等)。
    """
    ids = [r for r in record_ids if r]
    if not ids:
        return 0
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    deleted = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        try:
            _req(db, "POST", url, json={"records": chunk})
            deleted += len(chunk)
        except FeishuError as e:
            if e.code != ERR_RECORD_NOT_FOUND:
                raise
            # 整批含已不存在记录: 降级逐条删除, 忽略 NotFound
            for rid in chunk:
                try:
                    delete_record(db, app_token, table_id, rid)
                    deleted += 1
                except FeishuError as e2:
                    if e2.code == ERR_RECORD_NOT_FOUND:
                        continue
                    raise
    return deleted


def resolve_wiki_app_token(db: Session, wiki_token: str) -> str:
    """解析 Wiki 节点 token → Bitable App Token (obj_token).

    调用: GET /wiki/v2/spaces/get_node?token={wiki_token}&obj_type=wiki
    返回 data.node.obj_token。
    """
    url = f"{_BASE}/wiki/v2/spaces/get_node"
    data = _req(db, "GET", url, params={"token": wiki_token, "obj_type": "wiki"})
    obj_token = (data.get("node") or {}).get("obj_token")
    if not obj_token:
        raise FeishuError(f"无法解析 wiki_token={wiki_token} 对应的 obj_token，请检查 app 是否有 wiki 权限")
    return obj_token


def list_table_fields(db: Session, app_token: str, table_id: str) -> list[dict]:
    """获取 Bitable 表的字段列表.

    调用: GET /bitable/v1/apps/{app_token}/tables/{table_id}/fields
    返回 [{field_name, type, ...}, ...]。
    """
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    data = _req(db, "GET", url)
    return data.get("items") or []


def create_field(db: Session, app_token: str, table_id: str,
                 field_name: str, field_type: int = 1) -> str:
    """在 Bitable 表里新建字段.

    调用: POST /bitable/v1/apps/{app_token}/tables/{table_id}/fields
    field_type: 1=多行文本 2=数字 5=日期 (默认文本, 兼容性最好)。
    返回新建字段的 field_id。
    """
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    data = _req(db, "POST", url, json={"field_name": field_name, "type": field_type})
    return (data.get("field") or {}).get("field_id", "")


def delete_field(db: Session, app_token: str, table_id: str, field_id: str) -> None:
    """删除 Bitable 表里的字段 (不可逆, 该列数据一并丢失).

    调用: DELETE /bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}
    """
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}"
    _req(db, "DELETE", url)


def test_connection(db: Session) -> dict:
    """后台"测试连接"按钮用: 拿一次 token 即视为通。"""
    try:
        get_tenant_access_token(db, force=True)
        return {"ok": True}
    except FeishuError as e:
        return {"ok": False, "error": str(e)}


# 进程内 open_id -> 姓名 缓存 (姓名极少变, 避免每张图都打一次通讯录 API)
_NAME_CACHE: dict[str, str] = {}


def get_user_name(db: Session, user_id: str, *, id_type: str = "open_id") -> Optional[str]:
    """按 open_id(默认) 查飞书用户姓名 —— 需「contact:user.base:readonly」通讯录权限。

    调用: GET /contact/v3/users/{user_id}?user_id_type=open_id  → data.user.name
    带进程缓存; 失败/无权限返回 None (上层回退用 id, 不影响主流程)。
    """
    if not user_id:
        return None
    cached = _NAME_CACHE.get(user_id)
    if cached:
        return cached
    try:
        url = f"{_BASE}/contact/v3/users/{user_id}"
        data = _req(db, "GET", url, params={"user_id_type": id_type})
        name = (data.get("user") or {}).get("name")
        if name:
            _NAME_CACHE[user_id] = name
        return name
    except Exception as e:  # pragma: no cover - 无权限/网络错 → 回退 id
        _logger.info("查飞书用户名失败(忽略, 回退id): %s", e)
        return None
