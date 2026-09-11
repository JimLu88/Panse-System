from copy import deepcopy
import pytest
from app.services import campaign_official_discovery as d, web_agent_service as wa


def observation():
    return {'ok': True, 'legacy_fallback': False, 'platform_write': False,
            'evidence': {'path': 'official.json', 'sha256': 'a'*64},
            'snapshot': {'expected_shop_verified_on_entry': d.SHOP,
                         'page_url': d.ENTRY, 'visible_content': [{
                             'text': '大促日历\n共2场\n超级立减\n售卖中\n2025.6.21-2028.7.31\n'
                                     '2026年淘宝秋季家装节\n新\n报名中\n2026.9.16-9.27',
                             'truncated': False}]}}


def test_calendar_dates_never_become_exact_price_window():
    result = d.calendar(observation())
    assert result['count'] == 2
    assert result['campaigns'][1]['end'] == '2026-09-27'
    assert result['campaigns'][1]['date_precision'] == 'day_only'
    assert result['exact_activity_identity_verified'] is False


@pytest.mark.parametrize('change', ['count', 'truncated', 'shop', 'url', 'hash', 'year'])
def test_calendar_rejects_incomplete_or_ungrounded(change):
    value = observation(); snapshot = value['snapshot']
    if change == 'count': snapshot['visible_content'][0]['text'] += '\n共3场'
    if change == 'truncated': snapshot['visible_content'][0]['truncated'] = True
    if change == 'shop': snapshot['expected_shop_verified_on_entry'] = 'other'
    if change == 'url': snapshot['page_url'] = 'https://evil.invalid/'
    if change == 'hash': value['evidence']['sha256'] = ''
    if change == 'year': snapshot['visible_content'][0]['text'] = snapshot['visible_content'][0]['text'].replace('2026.9.16-9.27', '2026.12.31-1.1')
    with pytest.raises(ValueError): d.calendar(value)


def test_scheduled_bridge_does_not_call_old_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(wa, 'ensure_online', lambda *a, **k: {'online': True})
    monkeypatch.setattr(wa, '_post', lambda *a, **k: pytest.fail('legacy browser forbidden'))
    monkeypatch.setattr(wa, '_post_raw', lambda db, path, body, **kw: calls.append((path, body)) or observation())
    assert wa.campaign_discover(None)['count'] == 2
    assert calls == [('/api/campaign/continuous/observe', {'stage': 'home', 'expected_shop': d.SHOP})]


def test_detail_requires_target_shop_and_exact_ids(monkeypatch):
    value = observation(); title = '2026年淘宝秋季家装节'
    value['snapshot'].update(selected_activity_title=title, target_shop_verified=True,
        page_url='https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/campaign/index.htm?campaignId=49557&unitedActivityId=49560')
    assert d.detail(value, title)['campaign_title'] == title
    bad = deepcopy(value); bad['snapshot']['target_shop_verified'] = False
    with pytest.raises(ValueError): d.detail(bad, title)
    bad = deepcopy(value); bad['snapshot']['page_url'] += '&campaignId=other'
    with pytest.raises(ValueError): d.detail(bad, title)


def test_offline_does_not_fallback_or_retry(monkeypatch):
    monkeypatch.setattr(wa, 'ensure_online', lambda *a, **k: {'online': False})
    monkeypatch.setattr(wa, '_post_raw', lambda *a, **k: pytest.fail('must stop before browser'))
    assert wa.campaign_discover(None)['legacy_fallback'] is False


def test_calendar_hands_off_same_official_snapshot_without_legacy_plan_scan(db_session,monkeypatch):
    from app.services import campaign_discovery_service as service, campaign_automation_service as auto
    value=observation();value['daily_task_handoff']={'inbox_id':'a'*64,'state':'awaiting_daily_ai_identity'}
    observed=d.calendar(value)
    monkeypatch.setattr(wa,'campaign_discover',lambda db:observed)
    monkeypatch.setattr(service,'due_reminders',lambda *a:[])
    monkeypatch.setattr(auto,'sync_upcoming_plans',lambda *a:pytest.fail('old detail scan forbidden'))
    result=service.run_daily_discovery(db_session)
    assert result['auto_plans']['handoff']==value['daily_task_handoff']
    assert result['auto_plans']['state']=='awaiting_daily_ai_identity'
    assert result['auto_plans']['created']==0
