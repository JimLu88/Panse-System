"""HTTP middleware：审计日志 (plan §10 Phase 6 操作日志)."""
from __future__ import annotations

import json
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings
from app.database import SessionLocal
from app.models.auth import AuditLog
from app.services import auth_service

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class AuditMiddleware(BaseHTTPMiddleware):
    """对所有写请求记一条 audit_logs。

    跳过：settings.audit_skip_paths 里列出的 + 任何 /docs / /openapi / /api/health。
    用户身份从 Authorization Bearer token 解出；解不出就匿名记。
    请求体只记前 4KB；解析失败的二进制内容不记。
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        skip = {p.strip() for p in settings.audit_skip_paths.split(",") if p.strip()}
        path = request.url.path
        should_log = request.method in WRITE_METHODS and not (
            path in skip or path.startswith("/docs") or path.startswith("/openapi")
            or path.startswith("/redoc")
        )

        body_snippet: Optional[dict] = None
        if should_log:
            raw = await request.body()
            content_type = (request.headers.get("content-type") or "").lower()
            if raw:
                # Phase 13: multipart 上传只记元数据 (size + filename), 不记二进制
                if "multipart/form-data" in content_type:
                    body_snippet = {
                        "_multipart_upload": True,
                        "_size_bytes": len(raw),
                        "_content_type": content_type[:120],
                    }
                else:
                    try:
                        body_snippet = json.loads(raw)
                        # 脱敏: 密码 / token 字段
                        if isinstance(body_snippet, dict):
                            for k in list(body_snippet.keys()):
                                if k.lower() in ("password", "api_key", "secret", "token",
                                                  "refresh_token", "webhook"):
                                    body_snippet[k] = "***"
                            # 超长正文截断
                            if len(json.dumps(body_snippet, ensure_ascii=False)) > 4096:
                                body_snippet = {"_truncated": True,
                                                "_keys": list(body_snippet.keys())[:50]}
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        body_snippet = {"_raw_bytes_len": len(raw)}

            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}
            request = Request(request.scope, receive)

        response: Response = await call_next(request)

        if not should_log:
            return response

        user_id = None
        username = None
        note = None
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            try:
                payload = auth_service.decode_token(auth.split(" ", 1)[1].strip())
                user_id = int(payload.get("uid") or 0) or None
                username = payload.get("uname")
            except auth_service.InvalidToken:
                pass

        # 失败的写也要记
        try:
            db = SessionLocal()
            if username is None:
                from app.dependencies import machine_identity_for_key

                identity = machine_identity_for_key(
                    request.headers.get("x-api-key"), db, path=path)
                if identity:
                    username = identity
                    note = "authenticated path-scoped machine request"
            db.add(AuditLog(
                user_id=user_id,
                username=username,
                method=request.method,
                path=path,
                status_code=response.status_code,
                ip=request.client.host if request.client else None,
                request_body=body_snippet,
                note=note,
            ))
            db.commit()
            db.close()
        except Exception:  # pragma: no cover  — 审计绝不能阻塞业务
            pass
        return response
