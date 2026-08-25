"""企业微信应用/智能机器人回调（公开端点，签名和 AES 密文为安全边界）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import wechat_inbound_service

router = APIRouter(prefix="/api/wechat", tags=["wechat-callback"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, wechat_inbound_service.WechatInboundForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/callback", response_class=PlainTextResponse)
def verify_callback(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        return wechat_inbound_service.decrypt_url_verification(
            db,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echo_str=echostr,
        )
    except (
        wechat_inbound_service.WechatInboundError,
        wechat_inbound_service.WechatInboundForbidden,
    ) as exc:
        raise _http_error(exc) from exc


@router.post("/callback", response_class=PlainTextResponse)
async def receive_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    db: Session = Depends(get_db),
):
    body = await request.body()
    if len(body) > wechat_inbound_service.MAX_CALLBACK_BYTES:
        raise HTTPException(status_code=413, detail="回调消息过大")
    try:
        command = wechat_inbound_service.accept_callback(
            db,
            body=body,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
    except (
        wechat_inbound_service.WechatInboundError,
        wechat_inbound_service.WechatInboundForbidden,
    ) as exc:
        raise _http_error(exc) from exc
    if command is not None:
        wechat_inbound_service.dispatch(command)
    return "success"


@router.get("/aibot/callback", response_class=PlainTextResponse)
def verify_aibot_callback(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        return wechat_inbound_service.decrypt_aibot_url_verification(
            db,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echo_str=echostr,
        )
    except (
        wechat_inbound_service.WechatInboundError,
        wechat_inbound_service.WechatInboundForbidden,
    ) as exc:
        raise _http_error(exc) from exc


@router.post("/aibot/callback", response_class=PlainTextResponse)
async def receive_aibot_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    db: Session = Depends(get_db),
):
    body = await request.body()
    if len(body) > wechat_inbound_service.MAX_CALLBACK_BYTES:
        raise HTTPException(status_code=413, detail="回调消息过大")
    try:
        command = wechat_inbound_service.accept_aibot_callback(
            db,
            body=body,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
    except (
        wechat_inbound_service.WechatInboundError,
        wechat_inbound_service.WechatInboundForbidden,
    ) as exc:
        raise _http_error(exc) from exc
    if command is not None:
        wechat_inbound_service.dispatch(command)
    # 智能机器人协议允许直接回复空包；异步确认使用回调中的 response_url。
    return ""
