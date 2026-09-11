"""On-demand handoff to the receipt-gated continuous Web-Agent controller.

Installation does not imply acceptance. Never fall back to legacy automation.
"""
from app.services import web_agent_service

ENTRY = 'https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/homepage.htm'
RULE_ID = 'user-continuous-web-agent-20260911'


def readiness(db):
    result = web_agent_service._get_raw(db, '/api/campaign/continuous/capabilities', timeout=5)
    # Wake once only for a connection failure. A 401, stale rules, missing
    # acceptance, or failed platform action must not start a retry loop.
    if not result.get('ok') and any(marker in str(result.get('error','')) for marker in (
            'ConnectionError','ConnectTimeout','Connection refused','WinError 10061')):
        awake=web_agent_service.ensure_online(db,reason='continuous_campaign_capability_check',wait_s=15)
        if awake.get('online'):
            result=web_agent_service._get_raw(db,'/api/campaign/continuous/capabilities',timeout=5)
        else:
            return {'ready':False,'platform_write':False,'error':awake.get('error') or 'agent_wake_pending'}
    if not result.get('ok'):
        return {'ready': False, 'platform_write': False, 'error':
                result.get('error') or 'web_agent_continuous_endpoint_unavailable'}
    if result.get('rule_id') != RULE_ID or result.get('official_entry') != ENTRY:
        return {'ready': False, 'platform_write': False, 'error': 'web_agent_rule_identity_mismatch'}
    if result.get('ready') is not True or not result.get('acceptance_receipt_sha256'):
        return {'ready':False,'platform_write':False,'error':'continuous_execution_binding_not_installed','web_agent':result}
    return {'ready':True,'platform_write':False,'web_agent':result}


def dispatch(db):
    # POST once; uncertain response is never resubmitted in this invocation.
    return web_agent_service._post_raw(db,'/api/campaign/continuous/dispatch',{},timeout=10)
