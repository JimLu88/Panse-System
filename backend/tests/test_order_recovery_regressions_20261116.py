import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql
from app.models.order import Order, OrderDetail, FactoryOrder
from app.services import platform_order_resume_service as resume
from app.services import automation_pipeline_service as pipeline, scheduler
from app.services import agent_ingest_service as ingest, alert_service
from app.services.order_import import import_orders_from_csv


def test_full_long_title_import_and_reimport(db_session):
    title = '定制榉木餐桌与洞石岩板桌面；' * 60
    csv = '订单编号,产品名称\nlong-title-test,' + title + '\n'
    result = import_orders_from_csv(db_session, csv)
    assert result.inserted == 1
    db_session.commit()
    assert db_session.query(Order).filter_by(order_no='long-title-test').one().product_name == title
    import_orders_from_csv(db_session, csv)
    db_session.flush()
    assert db_session.query(Order).filter_by(order_no='long-title-test').count() == 1
    for model in (Order, OrderDetail, FactoryOrder):
        column = model.__table__.c.product_name
        assert isinstance(column.type, Text)
        assert column.type.compile(dialect=postgresql.dialect()) == 'TEXT'


@pytest.mark.parametrize('stage,allowed', [
    ('2026-11-16T10:59:00+00:00', True),
    ('2026-11-16T11:01:00+00:00', False),
    ('2026-11-16T19:00:00', False),
    ('bad-timestamp', False),
])
def test_login_wait_uses_timestamp_and_real_run_identity(monkeypatch, stage, allowed):
    monkeypatch.setattr(pipeline, 'get_pipeline', lambda *a: {
        'waiting_input': True, 'last_error': 'session_expired',
        'last_attempt_at': '2026-11-16T19:00:00+08:00',
        'active_run_key': 'actual-run', 'last_stage': {'at': stage},
    })
    result = resume.pending_order(None, 'shop')
    assert bool(result) == allowed
    if allowed:
        assert result['active_run_key'] == 'actual-run'


def test_running_order_does_not_replace_pipeline(db_session, monkeypatch):
    monkeypatch.setattr(ingest, 'is_running', lambda: True)
    monkeypatch.setattr(pipeline, 'begin_run', lambda *a, **k: pytest.fail('must preserve active run'))
    assert scheduler._job_web_agent_daily(db_session)['_run_status'] == 'skipped'


def test_new_same_day_run_replaces_old_terminal_login_error(db_session, monkeypatch):
    monkeypatch.setenv('PANSE_DISABLE_NOTIFY', '1')
    monkeypatch.setattr(pipeline, '_send_feishu', lambda *a: (True, 'test'))
    pipeline.begin_run(db_session, 'order_delivery', run_key='old-run')
    pipeline.record_failure(db_session, 'order_delivery', 'session_expired', retry_slots=[])
    state = pipeline._load(db_session)
    state['pipelines']['order_delivery']['final'] = True
    pipeline._save(db_session, state)
    db_session.commit()
    monkeypatch.setattr(ingest, 'is_running', lambda: False)
    monkeypatch.setattr(ingest, 'orchestrate', lambda *a, **k: {
        'tasks': [{'task': 'taobao_orders', 'status': 'error', 'error': 'report_identity_ambiguous'}],
    })
    monkeypatch.setattr(alert_service, 'upsert', lambda *a, **k: None)
    result = scheduler._job_web_agent_daily(db_session)
    actual = pipeline.get_pipeline(db_session, 'order_delivery')
    assert result['_run_status'] == 'fail'
    assert actual['active_run_key'] != 'old-run'
    assert 'report_identity_ambiguous' in actual['last_error']
    assert 'session_expired' not in actual['last_error']


def test_migration_refuses_lossy_downgrade(monkeypatch):
    path = Path(__file__).parents[1] / 'alembic/versions/0157_order_product_names_text.py'
    spec = importlib.util.spec_from_file_location('long_title_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    class Result:
        def scalar(self): return 1
    class Connection:
        def execute(self, statement): return Result()
    monkeypatch.setattr(migration.op, 'get_bind', lambda: Connection())
    monkeypatch.setattr(migration.op, 'alter_column', lambda *a, **k: pytest.fail('must not truncate'))
    with pytest.raises(RuntimeError, match='Cannot narrow'):
        migration.downgrade()


def test_postgres_upgrade_ddl(monkeypatch):
    from io import StringIO
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / 'alembic/versions/0157_order_product_names_text.py'
    spec = importlib.util.spec_from_file_location('long_title_upgrade', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    monkeypatch.setattr(migration, 'op', Operations(context))
    migration.upgrade()
    for table in ('orders', 'order_details', 'factory_orders'):
        assert f'ALTER TABLE {table} ALTER COLUMN product_name TYPE TEXT' in output.getvalue()
