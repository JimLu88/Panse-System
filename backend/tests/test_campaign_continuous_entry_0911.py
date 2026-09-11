from datetime import datetime, timedelta, timezone

from app.services import campaign_automation_service as automation
from app.services import campaign_continuous_runtime as runtime
from app.services import campaign_discovery_service as discovery
from app.services import campaign_service, settings_service, web_agent_service


def test_new_entry_never_falls_back_to_legacy_preflight(db_session, monkeypatch):
    settings_service.set_value(db_session, 'campaign_auto_enabled', 'true')
    db_session.commit()
    for name in ('group_by_sales', 'preflight', 'refresh_floor_evidence_from_current_activity',
                 'push_discount', 'push_signup'):
        def forbidden(*args, **kwargs):
            raise AssertionError('retired preflight or upload called')
        monkeypatch.setattr(campaign_service, name, forbidden)
    monkeypatch.setattr(runtime, 'readiness', lambda db: {'ready': False, 'error': 'adapter_missing'})
    monkeypatch.setattr(automation, '_notify_once', lambda *a, **kw: {'deduped': True})
    result = automation.run_auto_execute(db_session)
    assert result['blocked'] == 1
    assert result['platform_write'] is False
    assert result['legacy_fallback'] is False


def test_disabled_is_quiet_no_probe(db_session, monkeypatch):
    monkeypatch.setattr(runtime, 'readiness', lambda db: (_ for _ in ()).throw(AssertionError()))
    assert automation.run_auto_execute(db_session)['skipped'] == 'campaign_auto_disabled'


def test_http_capability_success_is_not_business_readiness(monkeypatch):
    monkeypatch.setattr(web_agent_service, '_get_raw', lambda *a, **kw: {
        'ok': True, 'ready': True, 'rule_id': runtime.RULE_ID, 'official_entry': runtime.ENTRY})
    assert runtime.readiness(None)['ready'] is False
    assert runtime.readiness(None)['platform_write'] is False


def test_periodic_discovery_72_hours_persistent_after_failure(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, 'run_daily_discovery',
                        lambda db: calls.append('read') or {'ok': False, 'error': 'offline'})
    now = datetime(2026, 9, 11, 1, tzinfo=timezone.utc)
    assert discovery.run_periodic_discovery(db_session, now=now)['ok'] is False
    assert discovery.run_periodic_discovery(db_session, now=now + timedelta(hours=71))['skipped']
    discovery.run_periodic_discovery(db_session, now=now + timedelta(hours=72))
    assert calls == ['read', 'read']


def test_bad_checkpoint_does_not_start_browser(db_session, monkeypatch):
    settings_service.set_value(db_session, 'campaign_continuous_last_discovery_attempt', 'bad-date')
    db_session.commit()
    monkeypatch.setattr(discovery, 'run_daily_discovery', lambda db: (_ for _ in ()).throw(AssertionError()))
    assert discovery.run_periodic_discovery(db_session)['error'] == 'campaign_discovery_checkpoint_invalid'


def test_known_campaign_end_triggers_once_before_72_hours(db_session, monkeypatch):
    from app.models.campaign import CampaignPlan
    calls=[]
    monkeypatch.setattr(discovery,'run_daily_discovery',lambda db:calls.append('read') or {'ok':True})
    now=datetime(2026,9,11,1,tzinfo=timezone.utc)
    db_session.add(CampaignPlan(name='Exact known campaign',campaign_type='big88',tier='big',
        platform_campaign_id='123',platform_united_activity_id='456',
        end_at=datetime(2026,9,11,23,59,59)))
    db_session.commit()
    discovery.run_periodic_discovery(db_session,now=now)
    result=discovery.run_periodic_discovery(db_session,now=datetime(2026,9,11,15,59,59,tzinfo=timezone.utc))
    assert result['skipped']=='discovery_not_due'
    discovery.run_periodic_discovery(db_session,now=datetime(2026,9,11,16,tzinfo=timezone.utc))
    result=discovery.run_periodic_discovery(db_session,now=datetime(2026,9,11,17,tzinfo=timezone.utc))
    assert result['skipped']=='discovery_not_due'
    assert calls==['read','read']
