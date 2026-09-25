"""Customer-feedback digest -> Feishu ALERT group only, durable at-most-once.

Receipt unknown is retained for human reconciliation, never retried implicitly.
This service does not touch orders, review-material tasks or factory tables.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.settings import SystemSetting
from app.services import feishu_client, notify_service

PREFIX = 'feedback_notice:'


def capability(db):
    cfg = notify_service.get_config(db)
    return {'protocol':'customer-feedback-feishu-v1',
            'ready':cfg['route_mode']=='feishu_split' and bool(cfg['alert_chat_id']),
            'destination':'feishu_alert','wechat_fallback':False}


def send_digest(db, notice_id, text):
    if os.environ.get('PANSE_DISABLE_NOTIFY'):
        return {'ok':False,'delivered':False,'reason':'notifications_disabled'}
    if (not isinstance(text,str) or not text.startswith('【店铺口碑巡检】')
            or len(text.encode('utf-8')) > 28000
            or notice_id != hashlib.sha256(text.encode('utf-8')).hexdigest()):
        raise ValueError('invalid_feedback_digest')
    key = PREFIX+notice_id
    prior = db.scalar(select(SystemSetting).where(SystemSetting.key==key))
    if prior:
        result = json.loads(prior.value_plain)
        return dict(result, duplicate=True)
    if not capability(db)['ready']:
        return {'ok':False,'delivered':False,'reason':'feishu_alert_not_configured'}
    cfg = notify_service.get_config(db)
    reserved = {'ok':False,'delivered':False,'status':'unknown','channel':'feishu_alert',
                'attempted_at':datetime.now(timezone.utc).isoformat()}
    entry = SystemSetting(key=key,value_plain=json.dumps(reserved),is_secret=False,
                          description='店铺口碑通知回执；未知发送禁止自动重试')
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        prior = db.scalar(select(SystemSetting).where(SystemSetting.key==key))
        return dict(json.loads(prior.value_plain),duplicate=True)
    # The reservation is committed before I/O, including process-crash windows.
    try:
        result = feishu_client.send_text(db,cfg['alert_chat_id'],text)
        message_id = result.get('message_id')
        receipt = dict(reserved, ok=bool(message_id), delivered=bool(message_id),
                       status='sent' if message_id else 'unknown',message_id=message_id)
    except Exception as exc:
        receipt = dict(reserved,reason=type(exc).__name__)
    entry.value_plain = json.dumps(receipt,ensure_ascii=False)
    db.commit()
    return receipt
