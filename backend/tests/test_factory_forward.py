import random
from datetime import date
import pytest
from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.models.factory_forward import FactoryForward
from app.services import factory_forward_service as f
from app.services import factory_dispatch_feishu_service as table


def fixture(db):
    order = Order(platform='taobao', order_no='3316520247104008964', status='paid', qty=2,
                  order_date=date.today(), paid_amount=1000)
    db.add(order)
    db.flush()
    lines = [OrderDetail(order_no=order.order_no, sub_order_no=str(3316520247104009000+i),
             source='import', product_name='餐边柜', factory_no=621+i, qty=1,
             factory_delivery_required=True, factory_delivery_state='sent',
             factory_delivery_message_id=f'image{i}') for i in range(3)]
    db.add_all(lines)
    db.commit()
    return order, lines


@pytest.mark.parametrize('seed', [1039, 5279, 8363, 12007, 19937, 29881, 40507, 65537, 104729, 130363])
def test_random_state_machine(db_session, monkeypatch, seed):
    db = db_session
    order, lines = fixture(db)
    rng = random.Random(seed)
    expected = {x.id: ['pending', 0, 0] for x in lines}
    monkeypatch.setattr(f, 'sync_table', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(f.feishu_client, 'patch_card', lambda *a, **k: {})
    for step in range(120):
        line = rng.choice(lines)
        state, version, history = expected[line.id]
        key = f.key_for(order.id, line.id)
        target = rng.choice(list(f.LABELS))
        stale = rng.random() < .25
        if stale:
            with pytest.raises(ValueError, match='更新'):
                f.change(db, key, target, version-1, 'ERP:1:测试')
            db.rollback()
        else:
            f.change(db, key, target, version, f'ERP:{rng.randrange(1,5)}:测试')
            if target != state or not db.get(FactoryForward, key).history[:-1] and history == 0:
                # First explicit pending acknowledgement is also auditable.
                if target != state or history == 0:
                    expected[line.id] = [target, version+1, history+1]
        if rng.random() < .3:
            f.sync_pending(db)
        for x in lines:
            p = f.projection(db, order.id, x.id)
            assert [p['state'], p['revision'], len(p['history'])] == expected[x.id], (seed, step)
            assert f.table_fields(db, order.id, x.id)['微信转发状态'] == p['label']
            assert x.factory_delivery_state == 'sent' and x.qty == 1
        assert order.status == 'paid' and order.qty == 2
    assert len(f.query(db, '哪些没转发')) == sum(v[0] != 'forwarded' for v in expected.values())


def test_card_identity_replay_and_child_isolation(db_session, monkeypatch):
    db = db_session
    order, lines = fixture(db)
    sends = []
    monkeypatch.setattr(f.feishu_client, 'reply_card', lambda *args: sends.append(args[1]) or {'message_id': 'card1'})
    f.attach_card(db, lines[0].id)
    f.attach_card(db, lines[0].id)
    assert sends == ['image0']
    event = {'_factory_forward_trusted': True, 'operator': {'open_id': 'real-user'}, 'context': {'open_message_id': 'wrong'},
             'action': {'value': {'key': f.key_for(order.id, lines[0].id), 'state': 'forwarded', 'revision': 0}}}
    with pytest.raises(ValueError):
        f.handle_card(db, event)
    event['context']['open_message_id'] = 'card1'
    f.handle_card(db, event)
    with pytest.raises(ValueError):
        f.handle_card(db, event)
    db.rollback()
    assert f.projection(db, order.id, lines[0].id)['actor'] == '飞书:real-user'
    assert f.projection(db, order.id, lines[1].id)['state'] == 'pending'


def test_unknown_card_never_resends(db_session, monkeypatch):
    order, lines = fixture(db_session)
    calls = []
    def fail(*a):
        calls.append(1)
        raise TimeoutError()
    monkeypatch.setattr(f.feishu_client, 'reply_card', fail)
    f.attach_card(db_session, lines[0].id)
    f.attach_card(db_session, lines[0].id)
    assert len(calls) == 1
    assert db_session.get(FactoryForward, f.key_for(order.id, lines[0].id)).card_state == 'unknown'
    assert lines[0].factory_delivery_state == 'sent'


def test_sync_failure_bounded_and_recovery(db_session, monkeypatch):
    db = db_session
    order, lines = fixture(db)
    key = f.key_for(order.id, lines[0].id)
    f.change(db, key, 'deferred', 0, 'ERP:1:测试')
    calls = []
    monkeypatch.setattr(f, 'sync_table', lambda *a, **k: calls.append(1) or {'ok': False})
    for _ in range(8):
        f.sync_pending(db)
    row = db.get(FactoryForward, key)
    assert len(calls) == 5 and row.sync_pending and row.state == 'deferred'
    f.change(db, key, 'forwarded', 1, 'ERP:1:测试')
    monkeypatch.setattr(f, 'sync_table', lambda *a, **k: {'ok': True})
    f.sync_pending(db)
    db.refresh(row)
    assert not row.sync_pending and row.state == 'forwarded'


def test_cancel_refund_legacy_and_queries(db_session):
    db = db_session
    order, lines = fixture(db)
    key = f.key_for(order.id, lines[0].id)
    f.change(db, key, 'deferred', 0, 'ERP:1:测试')
    assert len(f.query(db, '暂缓下单的有哪些')) == 1
    assert len(f.query(db, '查621单转发状态')) == 1
    assert len(f.query(db, '今天已经转发了哪些')) == 0
    lines[0].refund_status = '退款成功'
    db.commit()
    with pytest.raises(ValueError):
        f.change(db, key, 'forwarded', 1, 'ERP:1:测试')
    db.rollback()
    order.status = 'cancelled'
    db.commit()
    assert f.query(db, '哪些没转发') == []
    old = Order(platform='taobao', order_no='legacy', status='paid', factory_no=800, qty=1)
    db.add(old)
    db.commit()
    f.change(db, f.key_for(old.id), 'forwarded', 0, 'ERP:1:测试')
    assert f.items_for_order(db, old)[0]['state'] == 'forwarded'


def test_all_results_paginated(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(f.feishu_client, 'reply_text', lambda *a: calls.append(a[2]))
    rows = [{'factory_no': i, 'label': '待转发', 'product_name': '柜', 'sub_order_no': str(i), 'order_no': 'p'} for i in range(63)]
    f.reply_query(db_session, 'message', rows)
    assert len(calls) == 3
    assert sum(s.count('· 子单') for s in calls) == 63


def test_new_image_resets_and_old_card_rejected(db_session, monkeypatch):
    db = db_session
    order, lines = fixture(db)
    ids = iter(['card-old', 'card-new'])
    monkeypatch.setattr(f.feishu_client, 'reply_card', lambda *a: {'message_id': next(ids)})
    f.attach_card(db, lines[0].id)
    key = f.key_for(order.id, lines[0].id)
    f.change(db, key, 'forwarded', 0, 'ERP:1:测试')
    lines[0].factory_delivery_message_id = 'new-image'
    db.commit()
    f.attach_card(db, lines[0].id)
    row = db.get(FactoryForward, key)
    assert row.state == 'pending' and row.revision == 2 and len(row.history) == 2
    event = {'_factory_forward_trusted': True, 'operator': {'open_id': 'real'},
             'context': {'open_message_id': 'card-old'},
             'action': {'value': {'key': key, 'state': 'forwarded', 'revision': 2}}}
    with pytest.raises(ValueError, match='身份'):
        f.handle_card(db, event)
    event['context']['open_message_id'] = 'card-new'
    event['_factory_forward_trusted'] = False
    with pytest.raises(ValueError, match='来源'):
        f.handle_card(db, event)


def test_real_projection_preserves_sibling_and_manual_fields(db_session, monkeypatch):
    from tests.test_factory_dispatch_field_contract import mock_remote
    db = db_session
    order, lines = fixture(db)
    key = f.key_for(order.id, lines[0].id)
    f.change(db, key, 'forwarded', 0, 'ERP:1:测试')
    records = []
    mock_remote(monkeypatch, records)
    result = table.sync(db, include_images=False)
    assert result['ok'], result
    assert sum(r['fields'].get('微信转发状态') == '已转发' for r in records) == 1
    assert sum(r['fields'].get('微信转发状态') == '待转发' for r in records) == 2
    for r in records:
        r['fields']['工厂自填进度'] = '留着'
    f.change(db, key, 'deferred', 1, 'ERP:1:测试')
    result = table.sync(db, include_images=False)
    assert result['ok'], result
    assert all(r['fields']['工厂自填进度'] == '留着' for r in records)
    f.change(db, key, 'pending', 2, 'ERP:1:测试')
    result = f.sync_table(db, keys={key})
    assert result['ok'] and result['readback_verified'], result
    assert all(r['fields']['工厂自填进度'] == '留着' for r in records)
    assert sum(r['fields']['微信转发状态'] == '待转发' for r in records) == 3


def test_postgres_concurrent_clicks():
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    url = os.environ.get('FORWARD_TEST_DATABASE_URL')
    if not url:
        pytest.skip('isolated PostgreSQL URL required')
    import uuid
    from sqlalchemy import text
    schema = 'forward_test_' + uuid.uuid4().hex
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        order, lines = fixture(db)
        key = f.key_for(order.id, lines[0].id)
    barrier = threading.Barrier(2)
    def click(state):
        with sessions() as db:
            barrier.wait(timeout=10)
            try:
                f.change(db, key, state, 0, 'ERP:1:测试')
                return 'saved'
            except ValueError:
                db.rollback()
                return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(click, ['forwarded', 'deferred']))
    assert sorted(outcomes) == ['conflict', 'saved']
    with sessions() as db:
        row = db.get(FactoryForward, key)
        assert row.revision == 1 and len(row.history) == 1
    engine.dispose()
