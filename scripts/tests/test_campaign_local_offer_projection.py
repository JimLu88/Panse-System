import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_local_offer_projection as c


def page(item, mode):
    return dict(item=item, mode=mode, platform_write=False, observed=dict(
        filters=[dict(placeholder='商品ID：逗号或空格隔开', value=item),
                 dict(placeholder='活动ID', value=''), dict(placeholder='商品名称', value='')],
        selects=[], radios=[dict(text=mode, checked=True)],
        pagers=[dict(classes='qn-pagination-style', controls=[
            dict(classes='next-current', label='第1页，共1页'),
            dict(classes='next-next', disabled=True), dict(classes='next-prev', disabled=True)])],
        headers=['商品信息优惠级别活动优惠1件预估价（公域）活动信息活动状态活动时间操作'],
        rows=[dict(cells=['没有数据'], text='没有数据', key=None)], empty=['没有数据']))


def payload():
    counts = (10, 63, 7, 6, 4, 7, 2, 3)
    return dict(identity=copy.deepcopy(c.IDENTITY), items=list(c.ITEMS), batch_read=True,
        sku_scope={item: [str(6000000000000 + n * 100 + s) for s in range(count)]
                   for n, (item, count) in enumerate(zip(c.ITEMS, counts))},
        price_window=copy.deepcopy(c.WINDOW), read_request_id=c.READ_ID)


def data(p):
    return dict(schema='discount-batch-read-v2', state='readback', items=p['items'],
        sku_scope=p['sku_scope'], price_window=p['price_window'], read_request_id=p['read_request_id'],
        rows=[dict(item=i, mode=m, pages=[page(i, m)]) for i in c.ITEMS for m in c.MODES],
        platform_write=False, business_acceptance=False, automatic_retry=False, issues=[],
        source_meta=None, amount_scope='overlapping_requested_window_only')


def seal(monkeypatch):
    p = payload()
    d = data(p)
    raw = json.dumps(d).encode()
    source = b'fixture source'
    request = dict(action_id=c.JOB, request_sha=c.fingerprint(p), payload=p)
    rsha = c.fingerprint(dict(operation='discount_item_discovery', request=request))
    row = dict(id=c.JOB, state='finished', operation='discount_item_discovery', updated_at=c.FINISHED_AT,
               request_sha=rsha, request=json.dumps(request), result=json.dumps(d))
    wrapper = dict(stage='terminal', platform_write=False, source=str(c.SOURCE), source_sha256=c.sha(source),
        request=dict(step='discount_item_discovery', payload=p),
        job=dict(job_id=c.JOB, state='finished', operation='discount_item_discovery', result=copy.deepcopy(d)))
    monkeypatch.setattr(c, 'PAYLOAD_SHA', c.fingerprint(p))
    monkeypatch.setattr(c, 'REQUEST_SHA', rsha)
    monkeypatch.setattr(c, 'RESULT_SHA', c.sha(raw))
    monkeypatch.setattr(c, 'SOURCE_SHA', c.sha(source))
    return row, raw, wrapper, source


def projection_args():
    p = payload()
    lists = {(i, m): dict(complete=True, offers=[]) for i in c.ITEMS for m in c.MODES}
    return [p, lists, [], [], []]


def old_offer(item=None, status='success', platform='145761399121', bundle='old'):
    item = item or c.ITEMS[0]
    return dict(offer_id='bundle:' + bundle, platform_offer_id=platform, **c.OLD_WINDOW,
                items=[dict(item=item, status=status)], rows=[dict(item=item, sku='s1')])


def test_bound_terminal_accepts(monkeypatch):
    r, raw, wrapper, source = seal(monkeypatch)
    d, p, durable = c.validate_terminal(r, raw, wrapper, source)
    assert durable == d and sum(map(len, p['sku_scope'].values())) == 102


@pytest.mark.parametrize('key,value', [('state', 'running'), ('state', 'unknown'), ('id', 'other'),
    ('operation', 'discount_readback'), ('updated_at', '2026-09-29T00:00:00+00:00'), ('request_sha', 'x')])
def test_wrong_durable_terminal_rejected(monkeypatch, key, value):
    args = list(seal(monkeypatch)); args[0][key] = value
    with pytest.raises(ValueError): c.validate_terminal(*args)


@pytest.mark.parametrize('part', ['payload', 'request_hash', 'result', 'source', 'wrapper_scope', 'wrapper_job'])
def test_independent_binding_tamper_rejected(monkeypatch, part):
    args = list(seal(monkeypatch))
    if part in ('payload', 'request_hash'):
        req = json.loads(args[0]['request'])
        if part == 'payload': req['payload']['price_window']['end'] = '2026-10-07 23:59:59'
        else: req['request_sha'] = 'changed'
        args[0]['request'] = json.dumps(req)
    elif part == 'result': args[1] += b' '
    elif part == 'source': args[3] += b' '
    elif part == 'wrapper_scope': args[2]['request']['payload']['items'].pop()
    else: args[2]['job']['state'] = 'unknown'
    with pytest.raises(ValueError): c.validate_terminal(*args)


def test_durable_result_mismatch_rejected(monkeypatch):
    args = list(seal(monkeypatch)); args[0]['result'] = '{}'
    with pytest.raises(ValueError, match='saved_result'): c.validate_terminal(*args)


def test_recompute_all_sixteen_empty_lists():
    p = payload()
    assert len(c.checked_lists(data(p), p, c.load_checker())) == 16


@pytest.mark.parametrize('index', range(16))
def test_any_missing_mode_not_released(index):
    p = payload(); d = data(p); d['rows'].pop(index)
    with pytest.raises(ValueError, match='all_16_lists'): c.checked_lists(d, p, c.load_checker())


@pytest.mark.parametrize('fault', ['duplicate', 'foreign', 'no_pages', 'filter', 'pager', 'empty_flags', 'write'])
def test_complete_flags_do_not_replace_page_evidence(fault):
    p = payload(); d = data(p); d['coverage_verified'] = True
    row = d['rows'][0]; row['list_coverage_verified'] = True
    if fault == 'duplicate': d['rows'].append(copy.deepcopy(row))
    elif fault == 'foreign': row['item'] = '999999999999'
    elif fault == 'no_pages': row['pages'] = []
    elif fault == 'filter': row['pages'][0]['observed']['filters'][1]['value'] = 'some_offer'
    elif fault == 'pager': row['pages'][0]['observed']['pagers'][0]['controls'][1]['disabled'] = False
    elif fault == 'empty_flags': row['pages'][0]['observed']['rows'] = []
    else: row['pages'][0]['platform_write'] = True
    with pytest.raises(ValueError): c.checked_lists(d, p, c.load_checker())


def test_unknown_with_known_absent_offer_remains_held():
    args = projection_args(); args[2].append(old_offer(status='unknown'))
    args[3].append(dict(id='claim-1', bundle_id='old', item=c.ITEMS[0], status='unknown'))
    before = copy.deepcopy(args)
    r = c.project(*args); h = r['rows'][0]['history'][0]
    assert h['member_absent_in_bound_snapshot'] and h['unresolved_history_hold']
    assert h['historical_status'] == 'unknown' and h['original_claims'] == ['claim-1']
    assert not r['rows'][0]['local_overlap_review_clear'] and args == before


def test_absent_known_success_local_review_only_not_replay_or_deletion():
    args = projection_args(); args[2].append(old_offer())
    r = c.project(*args); h = r['rows'][0]['history'][0]
    assert r['rows'][0]['local_overlap_review_clear'] and h['history_unchanged']
    assert h['historical_status'] == 'success' and not h['global_deleted']
    assert not h['original_submission_replay_allowed'] and not r['upload_ready']


@pytest.mark.parametrize('kind', ['unbound_unknown', 'foreign_offer', 'other_window', 'still_present'])
def test_other_scope_cannot_inherit_local_absence(kind):
    args = projection_args(); offer = old_offer(); args[2].append(offer)
    if kind == 'unbound_unknown': offer['platform_offer_id'] = None; offer['items'][0]['status'] = 'unknown'
    elif kind == 'foreign_offer': offer['platform_offer_id'] = '147717819883'
    elif kind == 'other_window': offer['end'] = c.WINDOW['end']
    else: args[1][c.ITEMS[0], 'SKU级']['offers'].append(dict(offer_id='145761399121', **c.OLD_WINDOW))
    r = c.project(*args)
    assert not r['rows'][0]['local_overlap_review_clear']
    assert r['rows'][0]['history'][0]['unresolved_history_hold']


def test_disjoint_next_activity_not_blocked_boundary_overlap_is_blocked():
    args = projection_args()
    args[1][c.ITEMS[0], '商品级']['offers'].append(dict(offer_id='147633129042', start='2026-10-07 20:00:00', end='2026-10-11 23:59:59'))
    r = c.project(*args); assert r['rows'][0]['snapshot_no_overlap']
    args[1][c.ITEMS[0], '商品级']['offers'][0]['start'] = c.WINDOW['end']
    r = c.project(*args); assert not r['rows'][0]['snapshot_no_overlap']


def test_existing_327_pair_not_released():
    args = projection_args(); sku = args[0]['sku_scope'][c.ITEMS[0]][0]
    args[4].append((c.ITEMS[0], sku)); r = c.project(*args)
    assert r['rows'][0]['protected_327_skus'] == [sku]
    assert not r['rows'][0]['local_overlap_review_clear']


def test_delisted_bed_exclusion_not_asserted_as_independent_fact():
    r = c.project(*projection_args()); excluded = r['excluded_items'][0]
    assert excluded['item'] == '1035582527998' and excluded['item'] not in r['items']
    assert not excluded['independently_verified'] and not excluded['restore_listing_allowed']
    assert not excluded['reenroll_allowed'] and r['protected_other_scope']['two_tables_9_unchanged']


def test_no_prices_files_eligibility_or_authority_offer_set_exported():
    r = c.project(*projection_args())
    assert not any(r[k] for k in ('upload_ready', 'workbook_generated', 'platform_write', 'database_write',
                                 'automatic_retry', 'availability_ttl_changed', 'business_acceptance'))
    assert 'effective_offers' not in r and 'availability_refs' not in r
    assert all(not row['upload_ready'] and not row['price_composition_verified'] for row in r['rows'])


def test_existing_output_rejected_before_build(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'build', lambda: pytest.fail('must reject before consuming inputs'))
    with pytest.raises(ValueError, match='output_exists'): c.main(['--output-dir', str(tmp_path)])


def test_does_not_consume_or_extend_generic_availability(monkeypatch):
    import campaign_discount_availability
    monkeypatch.setattr(campaign_discount_availability, 'inactive_for_window',
                        lambda *a, **k: pytest.fail('no generic stale-receipt bypass'))
    r = c.project(*projection_args())
    assert not r['availability_ttl_changed']


@pytest.mark.parametrize('fault', ['window', 'scope', 'missing', 'incomplete'])
def test_projection_contract_guards(fault):
    args = projection_args()
    if fault == 'window': args[0]['price_window']['end'] = '2026-10-07 23:59:59'
    elif fault == 'scope': args[0]['items'].append(c.EXCLUDED_ITEM)
    elif fault == 'missing': args[1].pop((c.ITEMS[0], '商品级'))
    else: args[1][c.ITEMS[0], '商品级']['complete'] = False
    with pytest.raises(ValueError): c.project(*args)
