"""Read-only Web-Agent capability handoff. No browser fallback or write retries.

This is deliberately NOT a ready execution adapter. The continuous controller
is tested locally; production binding and real Edge transfers remain unverified.
"""
from app.services import web_agent_service

ENTRY = 'https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/homepage.htm'
RULE_ID = 'user-continuous-web-agent-20260911'


def readiness(db):
    result = web_agent_service._get_raw(db, '/api/campaign/continuous/capabilities', timeout=5)
    if not result.get('ok'):
        return {'ready': False, 'platform_write': False, 'error':
                result.get('error') or 'web_agent_continuous_endpoint_unavailable'}
    if result.get('rule_id') != RULE_ID or result.get('official_entry') != ENTRY:
        return {'ready': False, 'platform_write': False, 'error': 'web_agent_rule_identity_mismatch'}
    return {'ready': False, 'platform_write': False,
            'error': 'continuous_execution_binding_not_installed',
            'web_agent': result}
