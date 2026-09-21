"""Read-only regression: these tests never import or run business executors."""
import pytest
from app.services import business_relationship_service as s


def test_campaign_prepare_is_not_a_platform_success_shortcut():
    data = s.snapshot()
    assert not s.validate()
    assert 'campaign.files>campaign.receipt' not in {e['id'] for e in data['edges']}
    result = s.impact(['campaign.files'], depth=6, data=data)
    assert {'campaign.controller', 'campaign.attempt', 'campaign.receipt',
            'campaign.reconcile', 'sync.campaign_terminal'} <= {n['id'] for n in result['nodes']}
    assert not data['runtime_verified'] and data['read_only']


@pytest.mark.parametrize('root,targets', [
    ('sync.password', {'sync.freshness', 'automation.closeout', 'factory.receipt', 'sync.readback'}),
    ('sync.inbound', {'quantity.reply', 'sync.password', 'sync.remote', 'sync.inspection', 'sync.import'}),
    ('sync.generic', {'sync.conflict', 'sync.mapping', 'governance.change'}),
    ('campaign.failure', {'campaign.product_export', 'campaign.mapping', 'campaign.rotation'}),
])
def test_cross_flow_dependencies_visible(root, targets):
    result = s.impact([root], depth=6)
    assert targets <= {n['id'] for n in result['nodes']}


@pytest.mark.parametrize('edge_id', [
    'sync.campaign_terminal>campaign.receipt', 'sync.campaign_terminal>sync.route',
    'campaign.policy>campaign.rotation', 'sync.inspection>shipping.fulfilled',
    'sync.factory>sync.manual',
])
def test_external_receipts_do_not_grant_other_writes(edge_id):
    edge = next(e for e in s.catalog.EDGES if e['id'] == edge_id)
    assert edge['kind'] == 'protect'
    assert edge['condition'] and edge['check']


def test_all_new_sources_are_real_and_external_controller_is_explicit():
    data = s.snapshot()
    assert not [ref for item in data['nodes'] + data['edges'] for ref in item['sources']
                if ref['state'] in ('missing', 'anchor_missing')]
    controller = next(n for n in data['nodes'] if n['id'] == 'campaign.controller')
    assert '外部程序实时指纹' in controller['note']
    entry = next(n for n in data['nodes'] if n['id'] == 'campaign.entry')
    assert '03' in entry['field']


@pytest.mark.parametrize('path,root', [
    ('backend/app/services/feishu_bot_service.py', 'sync.password'),
    ('backend/app/services/campaign_continuous_runtime.py', 'campaign.controller'),
    ('backend/app/api/web_agent.py', 'sync.campaign_terminal'),
    ('backend/app/services/feishu_sync_service.py', 'sync.conflict'),
])
def test_changes_to_programs_generate_business_checklist(path, root):
    report = s.change_report([path])
    assert not report['unmapped_changes']
    assert root in report['impact']['roots']
