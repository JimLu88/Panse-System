from datetime import datetime, timedelta, timezone
import json
import pytest
from app.services import automation_pipeline_service as pipeline
from app.services import factory_production_evidence as facts

@pytest.mark.parametrize('identifier', ['5127421538135037339', '3316400502098056', 'SKU429123'])
def test_identifiers_are_not_http_errors(identifier):
    assert pipeline.classify_failure(identifier)['reason'] == 'unclassified_error'

@pytest.mark.parametrize('error', ['HTTP 503', 'status:502', '429 Too Many Requests', 'TimeoutError'])
def test_transient_errors_still_retry(error):
    assert pipeline.classify_failure(error)['retry_policy'] == 'scheduled_retry'

@pytest.mark.parametrize('error', ['本SKU专属尺寸未核实', '主订单汇总被作为子订单发送:5127421538135037339'])
def test_evidence_blockers_stop_hourly_retries(error):
    assert pipeline.classify_failure(error)['retry_policy'] == 'wait_for_input'

def test_full_error_classified_and_only_one_notification(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, '_send_feishu', lambda db, text: (sent.append(text) is None, 'sent'))
    now = datetime(2026, 9, 25, 18, tzinfo=timezone(timedelta(hours=8)))
    error = 'x' * 501 + '本SKU专属尺寸未核实'
    result = pipeline.record_failure(db_session, 'order_delivery', error, retry_slots=[now + timedelta(hours=1)], now=now)
    assert result['final'] and result['next_retry_at'] is None
    assert pipeline._load(db_session)['pipelines']['order_delivery']['waiting_input']
    again = pipeline.record_failure(db_session, 'order_delivery', error, retry_slots=[], now=now)
    assert again['ignored'] == 'already_closed'
    assert len(sent) == 1
    assert not pipeline._load(db_session)['pipelines']['order_delivery']['success']

def test_program_fault_not_hidden_by_evidence_issue():
    assert pipeline.classify_failure('TypeError 本SKU专属尺寸未核实')['owner'] == 'program_maintenance'

def test_exact_dimension_registration():
    data = json.loads(facts.DIMENSION_REGISTRY.read_text('utf-8'))
    record = data['PPS2415003051315']
    assert record['product_code'] == 'PPS24150030513'
    assert record['dimensions_mm'] == [720, 380, 360]
    assert record['sha256'] == '2257328fe156e30c194e0baccfb23c3b71e3615579a2d23f32f22f29fe4c18d8'
