import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.import_file import ImportedFile
from app.services import agent_ingest_service as ai
from app.services import automation_pipeline_service as pipeline
from app.services import feishu_bot_service as bot
from app.services import order_delivery_completion_service as closeout
from app.services import order_sheet_archive_service as sheets
from tests.test_order_job_adoption import manifest
from tests.test_order_line_factory_delivery_0812 import _feishu


CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 3, 14, 0, tzinfo=CN)


def adopted(db, monkeypatch, manifest):
    root, path, receipt, args = manifest
    receipt['started_at'] = '2026-10-03T02:30:00+08:00'
    path.write_text(json.dumps(receipt), encoding='utf-8')
    args['receipt_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(ai, 'OUTPUT_DIR', root)
    ai._save_json(db, 'order_job_adoption_' + args['source_job_id'], {
        'identity': {'order_batch_id': receipt['order_batch_id'],
                     'order_attempt_id': args['attempt'],
                     'receipt_sha256': args['receipt_sha256'], 'business_date': '2026-10-02'},
        'status': 'completed',
    })
    for item in receipt['artifacts']:
        stamp = datetime(2026, 10, 3, 5, 27, tzinfo=timezone.utc)
        db.add(ImportedFile(kind='taobao', original_filename=item['path'].split('/')[-1],
            stored_path=str(root/item['path']), file_hash=item['sha256'],
            row_summary={'agent_status': 'imported', 'agent_report_role': item['role'],
                         'automation_batch_id': receipt['order_batch_id']},
            created_at=stamp, updated_at=stamp))
    ai._save_json(db, ai.KEY_STATE, {'taobao_report': '2026-10-03T13:28:00',
        'taobao_orders_complete_batch_id': 'today-already-complete',
        'taobao_orders_complete_business_date': '2026-10-03'})
    # Today's timeout must neither block the resolved prior batch nor be closed by it.
    ai._save_json(db, ai.KEY_ORCH_STATE, {'started_at': '2026-10-03T13:45:00',
        'order_batch_id': 'orders-20261003-'+'d'*32, 'order_business_date': '2026-10-03',
        'tasks': [{'task': 'taobao_orders', 'status': 'timeout'}]})
    db.commit()
    return receipt


def test_adopted_previous_day_closes_from_resolved_file_without_ingest(db_session, monkeypatch, manifest):
    receipt = adopted(db_session, monkeypatch, manifest)
    monkeypatch.setattr(ai, 'run_ingest', lambda *a, **k: pytest.fail('reimport'))
    before = ai._load_json(db_session, ai.KEY_STATE)
    result = ai.finalize_order_pull_after_shipping_password(db_session, now=NOW,
        resolved_artifacts=['shipping.xlsx'])
    assert result['completed'] and result['order_business_date'] == '2026-10-02'
    assert result['order_batch_id'] == receipt['order_batch_id']
    assert result['current_day_state_updated'] is False
    assert ai._load_json(db_session, ai.KEY_STATE) == before
    assert result['imported_at']['shipping.xlsx'].startswith('2026-10-03T05:27:00')
    assert ai._load_json(db_session, 'order_pull_completion_'+receipt['order_batch_id'])['imported_at'] == result['imported_at']
    repeat = ai.finalize_order_pull_after_shipping_password(db_session, now=NOW,
        resolved_artifacts=['shipping.xlsx'])
    assert repeat['completed'] and ai._load_json(db_session, ai.KEY_STATE) == before


@pytest.mark.parametrize('fault,reason', [
    ('missing', 'order_pull_artifacts_not_imported'),
    ('wrong_batch', 'order_pull_artifacts_not_imported'),
    ('wrong_role', 'order_pull_artifact_roles_invalid'),
    ('early', 'batch_import_evidence_invalid'),
    ('future', 'batch_import_evidence_invalid'),
    ('archive_hash', 'batch_import_evidence_invalid'),
    ('file_hash', 'invalid_adopted_order_receipt'),
])
def test_exact_batch_import_guards(db_session, monkeypatch, manifest, fault, reason):
    adopted(db_session, monkeypatch, manifest)
    row = db_session.query(ImportedFile).filter_by(original_filename='orders.xlsx').one()
    if fault == 'missing': db_session.delete(row)
    if fault == 'wrong_batch': row.row_summary = {**row.row_summary, 'automation_batch_id': 'other'}
    if fault == 'wrong_role': row.row_summary = {**row.row_summary, 'agent_report_role': 'shipping'}
    if fault == 'early': row.created_at = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    if fault == 'future': row.created_at = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
    if fault == 'archive_hash': row.file_hash = '0'*64
    if fault == 'file_hash': manifest[0].joinpath('2026-10-02/taobao/orders.xlsx').write_bytes(b'changed')
    db_session.commit()
    result = ai.finalize_order_pull_after_shipping_password(db_session, now=NOW,
        resolved_artifacts=['shipping.xlsx'])
    assert result['completed'] is False and result['reason'] == reason


def test_historical_password_callback_keeps_today_pipeline_and_uses_existing_completion(db_session, monkeypatch, manifest):
    receipt = adopted(db_session, monkeypatch, manifest)
    pipeline.pause_for_input(db_session, 'order_delivery', 'today needs another file')
    before = pipeline.get_pipeline(db_session, 'order_delivery')
    monkeypatch.setattr(ai, 'reingest_pending_shipping', lambda db: {
        'imported': 1, 'files': [{'file': 'shipping.xlsx', 'status': 'imported'}]})
    real_finalize = ai.finalize_order_pull_after_shipping_password
    monkeypatch.setattr(ai, 'finalize_order_pull_after_shipping_password',
        lambda db, **kw: real_finalize(db, now=NOW, **kw))
    monkeypatch.setattr(sheets, 'repush_after_address_fill', lambda *a, **k: pytest.fail('global repush'))
    calls = []
    monkeypatch.setattr(closeout, 'complete_recovered_order_delivery', lambda db, **kw:
        calls.append(kw) or {'delivery': {'line_images_pushed': 1}})
    result = bot.apply_shipping_password(db_session, 'fixture-only')
    assert result['order_pull_completion']['completed']
    assert calls[0]['order_batch_id'] == receipt['order_batch_id']
    assert calls[0]['order_business_date'] == '2026-10-02'
    assert pipeline.get_pipeline(db_session, 'order_delivery') == before


def test_explicit_recovery_scope_never_calls_global_delivery(db_session, monkeypatch):
    pipeline.pause_for_input(db_session, 'order_delivery', 'today still waiting')
    before = pipeline.get_pipeline(db_session, 'order_delivery')
    monkeypatch.setattr(sheets, 'reconcile_pending_delivery', lambda *a, **k: pytest.fail('global'))
    calls = []
    monkeypatch.setattr(sheets, 'reconcile_order_line_delivery', lambda db, **kw:
        calls.append(kw) or {'pushed': 2, 'failed': 0})
    monkeypatch.setattr('app.services.factory_dispatch_feishu_service.sync_if_enabled', lambda db: {'ok': True})
    result = closeout.complete_recovered_order_delivery(db_session, source='operator',
        manifest=['orders.xlsx', 'sales_detail.xlsx', 'shipping.xlsx'],
        order_business_date='2026-10-02', order_batch_id='original', only_sub_order_nos={'sub1', 'sub2'})
    assert calls == [{'limit': 500, 'only_sub_order_nos': {'sub1', 'sub2'}}]
    assert result['delivery']['line_images_pushed'] == 2
    assert pipeline.get_pipeline(db_session, 'order_delivery') == before


def test_historical_automatic_scope_is_original_business_day(db_session, monkeypatch):
    from tests.test_order_line_factory_delivery_0812 import _order, _line
    for suffix, day in [('old', date(2026, 10, 2)), ('today', date(2026, 10, 3))]:
        order = _order(db_session, suffix); order.order_date = day
        line = _line(db_session, suffix, 'sub-'+suffix, '床', 'SKU')
        line.factory_delivery_required = True
    db_session.commit()
    calls = []
    monkeypatch.setattr(sheets, 'reconcile_order_line_delivery', lambda db, **kw: calls.append(kw) or {'pushed': 0})
    monkeypatch.setattr('app.services.factory_dispatch_feishu_service.sync_if_enabled', lambda db: {'ok': True})
    closeout.complete_recovered_order_delivery(db_session, source='shipping_password',
        manifest=[], order_business_date='2026-10-02')
    assert calls[0]['only_sub_order_nos'] == {'sub-old'}


def test_skeleton_waits_without_factory_number_or_send(db_session, monkeypatch):
    from tests.test_order_line_factory_delivery_0812 import _order, _line
    from app.services import settings_service
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY', raising=False)
    settings_service.set_value(db_session, 'feishu_push_chat_id', 'test')
    order = _order(db_session, 'parent')
    line = _line(db_session, order.order_no, 'skeleton', '', '')
    line.sku_name = ''; line.factory_delivery_required = True
    db_session.commit()
    monkeypatch.setattr(sheets.factory_sheet, 'build_for_order_line', lambda *a, **k: pytest.fail('empty sheet'))
    result = sheets.reconcile_order_line_delivery(db_session, only_sub_order_nos={'skeleton'})
    assert result['pushed'] == 0 and result['failures'][0]['deferred'] == 'sku_missing'
    assert line.factory_no is None and line.factory_delivery_state is None


def test_scoped_delivery_preserves_warning_rules_and_never_repeats_sent_or_unknown(db_session, monkeypatch, _feishu):
    from tests.test_order_line_factory_delivery_0812 import _order, _line
    from app.services import settings_service
    settings_service.set_value(db_session, 'feishu_push_chat_id', 'test')
    order = _order(db_session, 'scope-parent')
    ready = _line(db_session, order.order_no, 'ready', '定制餐桌', 'PPS2421007090199')
    unknown = _line(db_session, order.order_no, 'unknown', '床', 'PPS2633007032018')
    outside = _line(db_session, order.order_no, 'outside', '床', 'PPS2633007032018')
    for line in (ready, unknown, outside): line.factory_delivery_required = True
    unknown.factory_delivery_state = 'uncertain'
    db_session.commit()
    monkeypatch.setattr('app.services.factory_dispatch_feishu_service.sync_if_enabled', lambda db: {'ok': True})
    kwargs = dict(source='operator', manifest=[], order_business_date='2026-10-02',
                  only_sub_order_nos={'ready', 'unknown'})
    first = closeout.complete_recovered_order_delivery(db_session, **kwargs)
    assert first['_run_status'] == 'fail'
    assert first['delivery']['line_images_pushed'] == 1
    assert first['delivery']['receipts'][0]['message_id'] == 'img'
    assert first['delivery']['unresolved_sub_order_nos'] == ['unknown']
    second = closeout.complete_recovered_order_delivery(db_session, **kwargs)
    assert second['delivery']['line_images_pushed'] == 0 and len(_feishu) == 1
    assert outside.factory_delivery_state is None and outside.factory_no is None
