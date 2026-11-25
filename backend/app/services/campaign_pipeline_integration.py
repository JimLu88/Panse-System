"""ERP data and durable boundary for the fifth-review Web-Agent pipeline.

Export is read-only. A claim is consumed before any platform write and never
automatically released, including failed/unknown adapter results.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import secrets
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from app.models.campaign import CampaignPlan, CampaignExecutionAttempt
from app.services import campaign_service as cs
from app.services import campaign_policy_service, campaign_sku_slot_service

ADAPTER = 'campaign-pipeline-round5-20261126'
SHOP = '畔色木作'
SHOP_ID = '431991266'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def _iso(value):
    if value is None:
        return ''
    return value.replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat() if value.tzinfo is None else value.isoformat()


def campaign_key(plan):
    # Legacy plans store identity in their official URL/remark, not the new column.
    return str(cs.campaign_identity(plan).get('campaign_id') or plan.platform_campaign_id or f'plan:{plan.id}')


def protected_attempts(db, plan):
    # Protect the same platform campaign even if another ERP plan was created.
    key = campaign_key(plan)
    ids = [p.id for p in db.execute(select(CampaignPlan)).scalars() if campaign_key(p) == key]
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.plan_id.in_(ids),
        CampaignExecutionAttempt.write_claimed.is_(True),
    )).scalars().all()


def lock_campaign_plans(db, plan):
    # A duplicate ERP plan must not provide a second writer for one campaign.
    if db.get_bind().dialect.name == 'postgresql':
        key = int.from_bytes(hashlib.sha256(campaign_key(plan).encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':key})
    db.execute(select(CampaignPlan).where(CampaignPlan.id == plan.id).with_for_update()).scalar_one()


def export_bundle(db, plan):
    identity = cs.campaign_identity(plan)
    if not identity['ok'] or not identity.get('campaign_id'):
        raise ValueError('活动身份或窗口不完整：' + ','.join(identity.get('missing', [])))
    if identity.get('platform_activity_mode') != 'fixed_window':
        raise ValueError('新入口当前仅接固定档期活动；长期活动继续使用原入口')
    discount_ids = set(filter(None, [cs._plan_single_discount_activity_id(plan)]))
    for reader in (cs._plan_single_discount_activity_ids,
                   cs._plan_single_discount_sku_activity_ids,
                   cs._plan_single_discount_refreshed_activity_ids):
        if reader(plan):
            raise ValueError('本场使用商品/SKU级单品立减绑定，新入口暂不支持；保留原入口处理')
    if len(discount_ids) > 1:
        raise ValueError('本场关联多个单品立减活动，新入口暂不支持；保留原入口处理')
    rows, stats = cs.build_signup_rows(db, plan, seed_baselines=False)
    prices = cs.target_prices(db, plan)
    policy = campaign_policy_service.require_policy()
    mode = 'medium_target' if cs.plan_tier(plan) == 'mid' else 'big_target'
    payload_rows = []
    seen = set()
    for row in rows:
        iid, sid = str(row['taobao_item_id']), str(row['taobao_sku_id'])
        if (iid, sid) in seen:
            raise ValueError('ERP报名集合存在重复商品SKU')
        seen.add((iid, sid))
        price = prices.get(sid)
        if not price or price.get('sku_code') != row['sku_code']:
            raise ValueError('ERP价格映射不唯一或缺失：' + sid)
        custom = bool(row.get('is_placeholder'))
        item = {'item': iid, 'sku': sid, 'custom': custom,
                'daily': price.get('daily'), mode: price.get('target'),
                'outer_id': row['sku_code'], 'on_sale': True}
        if custom:
            base = campaign_sku_slot_service.immutable_baseline(db,
                sku_code=row['sku_code'], item_id=iid, taobao_sku_id=sid)
            if base is None:
                raise ValueError('缺首次定制基线：' + sid)
            item.update(custom_first_orig_price=str(base),
                baseline_id=f"erp-slot:{iid}:{sid}", approved_custom_price=str(row['price']))
        elif price.get('daily') is None or price.get('target') is None:
            raise ValueError('缺普通SKU日常价或目标价：' + sid)
        payload_rows.append(item)
    blocked = protected_attempts(db, plan)
    data = {'schema': 'panse.campaign.pipeline-bundle.v1', 'adapter': ADAPTER,
        'plan_id': plan.id, 'shop': SHOP, 'shop_id': SHOP_ID,
        'campaign_id': identity['campaign_id'],
        'united_activity_id': identity.get('united_activity_id') or '',
        'sign_record_id': identity.get('sign_record_id') or '',
        'campaign_title': identity['campaign_title'],
        'campaign_start': _iso(plan.start_at), 'campaign_end': _iso(plan.end_at),
        'campaign_tier': mode, 'discount_rate': str(cs.TIER_LEVERAGE[cs.plan_tier(plan)]),
        'single_discount_activity_id': next(iter(discount_ids), ''),
        'all_erp_rows': payload_rows,
        'allowed_skus': [{'item_id': i, 'sku_id': s} for i, s in sorted(seen)],
        'policy_sha256': policy['_sha256'],
        'exclusions': stats,
        'legacy_protection': [{'attempt_id': a.id, 'state': a.state,
            'plan_id': a.plan_id} for a in blocked]}
    data['price_version'] = digest(data)
    data['exported_at'] = datetime.now(timezone.utc).isoformat()
    data['execution_ready'] = bool(payload_rows) and not blocked and plan.status == 'draft'
    data['plan_status'] = plan.status
    data['live_enabled'] = os.environ.get('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED') == '1'
    data['platform_write'] = False
    return data


def claim(db, plan_id, price_version):
    if os.environ.get('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED') != '1':
        raise ValueError('新入口已接数据，真实提交尚未启用；需完成获准活动实盘验收安排')
    # Serialize against the old claim path on the same plan row.
    plan = db.get(CampaignPlan, plan_id)
    if not plan:
        raise ValueError('活动计划不存在')
    lock_campaign_plans(db, plan)
    bundle = export_bundle(db, plan)
    if not bundle['execution_ready']:
        raise ValueError('无可提交范围，或计划不是新草稿/已有占位；仅可核对原任务')
    if bundle['price_version'] != price_version:
        raise ValueError('ERP身份、价格、范围或原占位已变化，请重新读取本场数据')
    request_id = secrets.token_hex(16)
    attempt = CampaignExecutionAttempt(id=secrets.token_hex(12), plan_id=plan.id,
        workflow_key=plan.workflow_key or f'campaign:legacy-plan:{plan.id}',
        operation='signup', scope_sha256=price_version, state='write_claimed',
        write_claimed=True, write_claimed_at=datetime.now(timezone.utc),
        automatic_retry_allowed=False, request_id=request_id,
        last_step='pipeline_bridge_claim', result_summary={'adapter': ADAPTER,
            'price_version':price_version, 'bundle':bundle})
    db.add(attempt)
    db.commit()
    return {'attempt_id':attempt.id, 'request_id':request_id, 'bundle':bundle,
        'automatic_retry_allowed':False}


def original_claim(db, plan_id, attempt_id):
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    summary = (attempt.result_summary or {}) if attempt else {}
    if not attempt or attempt.plan_id != plan_id or not attempt.write_claimed or summary.get('adapter') != ADAPTER:
        raise ValueError('不是本入口的有效原占位')
    return {'attempt_id': attempt.id, 'request_id': attempt.request_id,
            'bundle': summary['bundle'], 'receipt': summary.get('receipt')}


def protect_legacy_discount(db, plan, *, committing):
    """Reserve legacy ownership without changing its own retry/result policy."""
    lock_campaign_plans(db, plan)
    attempts = protected_attempts(db, plan)
    if any((a.result_summary or {}).get('adapter') == ADAPTER for a in attempts):
        raise ValueError('campaign_pipeline_already_claimed_no_retry')
    if committing and not any(a.operation == 'legacy_discount' for a in attempts):
        db.add(CampaignExecutionAttempt(id=secrets.token_hex(12),plan_id=plan.id,
            workflow_key=plan.workflow_key or f'campaign:legacy-plan:{plan.id}',
            operation='legacy_discount',scope_sha256=digest({'campaign':campaign_key(plan)}),
            state='write_claimed',write_claimed=True,write_claimed_at=datetime.now(timezone.utc),
            automatic_retry_allowed=False,last_step='legacy_discount_owner',
            result_summary={'owner':'legacy_discount','business_complete':False}))
        db.commit()


def record_receipt(db, plan_id, attempt_id, receipt):
    attempt = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == attempt_id,
        CampaignExecutionAttempt.plan_id == plan_id).with_for_update()).scalar_one_or_none()
    if not attempt or not attempt.write_claimed or (attempt.result_summary or {}).get('adapter') != ADAPTER:
        raise ValueError('不是本入口的有效原占位')
    if receipt.get('state') not in {'done','partial_done','failed','unknown_pending_verify','needs_manual'}:
        raise ValueError('不接受非终态回执')
    prior = (attempt.result_summary or {}).get('receipt')
    if prior and digest(prior) != digest(receipt):
        if prior['state'] != 'unknown_pending_verify':
            raise ValueError('已有明确终态，不能覆盖原回执')
        if prior.get('attempt_id') and prior['attempt_id'] != receipt.get('attempt_id'):
            raise ValueError('恢复回执不是同一个浏览器任务')
    if not prior or digest(prior) != digest(receipt):
        summary = dict(attempt.result_summary or {})
        history = list(summary.get('receipt_history') or [])
        history.append({'received_at': datetime.now(timezone.utc).isoformat(),
                        'receipt': receipt, 'sha256': digest(receipt)})
        summary['receipt_history'] = history
        summary['receipt'] = receipt
        summary['receipt_sha256'] = digest(receipt)
        attempt.result_summary = summary
        # Adapter completion is not authoritative ERP full-scope reconciliation.
        attempt.state = 'unknown_no_retry' if receipt['state']=='unknown_pending_verify' else 'platform_terminal'
        attempt.last_step = 'pipeline_receipt_received'
        db.commit()
    return {'recorded':True, 'attempt_id':attempt.id, 'state':attempt.state,
        'business_complete':False, 'automatic_retry_allowed':False}
