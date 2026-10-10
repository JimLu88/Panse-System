"""Explicit, prepare-only recovery trial. Never edits discounts or submits a file."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from campaign_official_current import parse, price_status
from campaign_official_template import (_archive, _read, _effective, sheet_path,
                                        fill_selected_rows, money)

PROJECT = Path('D:/AI/畔色ERP系统')
LEDGER = PROJECT/'活动准备/报名状态/recovery-trials'
RECOVERY = 'super_reduce_abnormal_recovery'
DISCOUNT = 'single_discount_amount_fix'
CAMPAIGN = 'legacy/itemApply/3172207691'
MASTER_SHA = 'be5ae804a19ccf987381ec0d2dab76e374ede9e780c1cc338ae19084574e4e83'
MASTER = PROJECT/'outputs/campaign-continuous/master-replacement-20260911/超级立减长期活动-固定官方母版.xlsx'
ACTIVE = {'活动中', '已发布设定', '进行中', '已生效'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def pinned(source):
    raw = Path(source['path']).read_bytes()
    if sha(raw) != source['sha256']:
        raise ValueError('evidence_hash_changed:' + str(source['path']))
    return raw


def route(kind, template_kind):
    expected = {RECOVERY:'super_reduce_activity', DISCOUNT:'single_discount'}
    if kind not in expected or template_kind != expected[kind]:
        raise ValueError('experiment_type_and_template_mismatch')
    return kind


def scoped_rows(raw, item):
    rows, _ = parse(raw)  # physical rows and real merged ranges, not dimension
    selected = [r for r in rows if r['item'] == item and r['state'] != '撤销报名']
    if not selected or len({r['sku'] for r in selected}) != len(selected):
        raise ValueError('current_sku_scope_missing_or_duplicate')
    if len({r['marketing_id'] for r in selected}) != 1:
        raise ValueError('current_marketing_record_not_unique')
    with _archive(raw) as z:
        _, _, cells, merges = _read(z, sheet_path(z, '已报商品列表'))
        if '15144972' not in cells.get(1, {}).get('S', ''):
            raise ValueError('source_not_exact_super_reduce_activity')
        if cells.get(2, {}).get('S') != '让利比例' or cells.get(2, {}).get('T') != '补贴金额':
            raise ValueError('official_rate_columns_missing')
        for row in selected:
            n = row['excel_row']
            if (_effective(cells, merges, n, 'S') not in ('10', '10.0', '10.00')
                    or _effective(cells, merges, n, 'T') not in ('', '0', '0.00')):
                raise ValueError('current_rate_or_subsidy_not_unchanged_ten_percent')
    return selected


def canonical_scope(rows):
    result = {}
    for r in rows:
        key = str(r['item']), str(r['sku'])
        if key in result or any(not re.fullmatch(r'\d{8,20}', v) for v in key):
            raise ValueError('invalid_or_duplicate_authorized_sku')
        value = money(r['activity_price'])
        if value <= 0:
            raise ValueError('nonpositive_original_activity_price')
        result[key] = format(value, '.2f')
    return result


def records(request):
    """One explicit authorized batch; never infer the next 14 items from success."""
    result = request.get('marketing_records')
    if result is None:
        result = {request.get('item'):request.get('original_marketing_id')}
    if (not isinstance(result,dict) or not result
            or any(not isinstance(v,str) or not re.fullmatch(r'\d{8,20}',v)
                   for pair in result.items() for v in pair)):
        raise ValueError('exact_item_marketing_records_required')
    if request.get('marketing_records') is not None and (
            request.get('item') is not None or request.get('original_marketing_id') is not None):
        raise ValueError('mixed_single_and_batch_identity')
    return result


def normalize_authorization(doc):
    if doc.get('operation') == RECOVERY:
        items = doc.get('items', [])
        if (not items or len({r['item'] for r in items}) != len(items)
                or doc.get('activity_prices_unchanged') is not True
                or doc.get('excluded_single_discount_upload') is not True
                or doc.get('single_discount106_untouched') is not True
                or doc.get('platform_write') is not False):
            raise ValueError('batch_delivery_scope_or_price_authorization_incomplete')
        return dict(experiment_type=RECOVERY,user_authorization=doc.get('authorization'),
                    marketing_records={r['item']:r['marketing_id'] for r in items},scope=doc['rows'],
                    official_rate=doc.get('rate'),prices_changed=False,single_discount_changed=False)
    return doc


def validate(request, authorization, raw):
    authorization = normalize_authorization(authorization)
    if request.get('schema') != 'campaign_recovery_trial_request_v1':
        raise ValueError('explicit_trial_request_required')
    kind = route(request.get('experiment_type'), request.get('template_kind'))
    if kind != RECOVERY:
        raise ValueError('single_discount_amount_fix_use_existing_amount_workflow_not_recovery')
    if request.get('campaign') != CAMPAIGN or request.get('official_rate') != '10%':
        raise ValueError('exact_super_reduce_campaign_and_rate_required')
    for key in ('platform_write', 'price_change', 'withdraw', 'single_discount_change', 'automatic_expand'):
        if request.get(key) is not False:
            raise ValueError('prepare_only_no_mutation_required:' + key)
    for key in ('experiment_type', 'item', 'original_marketing_id', 'marketing_records', 'scope'):
        if authorization.get(key) != request.get(key):
            raise ValueError('current_user_authorization_scope_mismatch:' + key)
    if (not authorization.get('user_authorization')
            or authorization.get('official_rate') not in ('10', '10%')
            or authorization.get('prices_changed') is not False
            or authorization.get('single_discount_changed') is not False):
        raise ValueError('current_user_unchanged_price_trial_authorization_required')
    identities = records(request)
    selected = [r for item in identities for r in scoped_rows(raw, item)]
    if any(r['state'] != '异常' for r in selected):
        raise ValueError('active_draft_unknown_scope_must_not_replay')
    if any(r['marketing_id'] != identities[r['item']] for r in selected):
        raise ValueError('original_marketing_identity_changed')
    if any(price_status(r) != '价格满足' for r in selected):
        raise ValueError('not_price_satisfied_abnormal_recovery')
    if canonical_scope(selected) != canonical_scope(request['scope']):
        raise ValueError('whole_product_scope_or_original_price_changed')
    # Discount success protects that phase only; no implicit recovery exception.
    # The above exact current authorization is the only recovery exception.
    return selected


def build(request):
    authorization = json.loads(pinned(request['authorization']).decode('utf-8-sig'))
    raw = pinned(request['source'])
    rows = validate(request, authorization, raw)
    master = pinned(request['master'])
    if sha(master) != MASTER_SHA:
        raise ValueError('wrong_fixed_official_activity_master')
    workbook = fill_selected_rows(master, rows, official_rate=Decimal('.10'))
    return workbook, rows


def same_package(left, right):
    with _archive(left) as a, _archive(right) as b:
        if a.namelist() != b.namelist() or any(a.read(n) != b.read(n) for n in a.namelist()):
            raise ValueError('existing_trial_file_differs_from_authorized_generation')


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def prepare(request_path, *, output_dir=None, workbook_path=None, ledger=LEDGER):
    request = load(request_path)
    data, rows = build(request)
    if (output_dir is None) == (workbook_path is None):
        raise ValueError('choose_prepare_or_adopt_not_both')
    if workbook_path is not None:
        workbook_path = Path(workbook_path).resolve(strict=True)
        same_package(data, workbook_path.read_bytes())
    elif Path(output_dir).exists():
        raise ValueError('output_exists_do_not_overwrite')
    root, initial = reserve(request, sha(Path(request_path).read_bytes()), rows, ledger)
    if workbook_path is None:
        Path(output_dir).mkdir(parents=True)
        workbook_path = Path(output_dir).resolve()/'超级立减活动状态恢复试验.xlsx'
        with workbook_path.open('xb') as stream:
            stream.write(data)
    result = dict(initial, state='prepared_not_submitted', file=str(workbook_path),
                  file_sha256=sha(workbook_path.read_bytes()), trial_dir=str(root),
                  recovery_supported=None, business_complete=False,
                  whole_event_complete=False,
                  single_discount_replay_allowed=False, official_terminal=None,
                  note='User uploads once; official terminal plus same marketing readback required.')
    write_new(root/'prepared.json', result)
    return result


def reserve(request, request_sha256, rows, ledger):
    # Shared hold for prepare, adopt and historical results; no filename bypass.
    items = sorted(records(request))
    key = digest([request['campaign'], RECOVERY, items])
    root = Path(ledger)/key
    root.parent.mkdir(parents=True, exist_ok=True)
    lock = root.parent/'reservation.lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError('trial_reservation_in_progress_or_interrupted') from exc
    try:
        for directory in root.parent.iterdir():
            if directory == lock or not directory.is_dir():
                continue
            old_file = directory/'request.json'
            if not old_file.exists():
                raise ValueError('interrupted_trial_reservation_requires_review')
            old = load(old_file)['request']
            if old['campaign'] == request['campaign'] and set(records(old)) & set(items):
                raise ValueError('trial_already_prepared_or_unknown_do_not_regenerate')
        root.mkdir()
        # A crash leaves a durable hold, not a new-upload opportunity.
        initial = dict(request=request, request_sha256=request_sha256,
                       trial_id=key, original_rows=rows, state='generation_reserved_unknown',
                       platform_write=False, automatic_retry=False, automatic_expand=False,
                       created_at=datetime.now(timezone.utc).isoformat())
        write_new(root/'request.json', initial)
    finally:
        lock.rmdir()
    return root, initial


def readback(request, raw):
    identities = records(request)
    rows = [r for item in identities for r in scoped_rows(raw,item)]
    if (any(r['marketing_id'] != identities[r['item']] for r in rows)
            or canonical_scope(rows) != canonical_scope(request['scope'])):
        raise ValueError('same_marketing_full_scope_unchanged_prices_required')
    if any(r['state'] not in ACTIVE or price_status(r) != '价格满足' for r in rows):
        return False
    return True


def import_terminal(request, raw):
    """Official Z/AA result, never the reference D/K echo or orange warning."""
    with _archive(raw) as z:
        _, _, cells, merges = _read(z, sheet_path(z, '商品SKU导入列表'))
    expected_headers = {'A':'商品ID', 'E':'SKUID', 'N':'活动价', 'X':'让利比例',
                        'Y':'补贴金额', 'Z':'是否成功', 'AA':'失败原因或风险提示'}
    if any(cells.get(2, {}).get(k) != v for k,v in expected_headers.items()):
        raise ValueError('official_terminal_columns_required_not_export_refresh')
    if '15144972' not in cells.get(1, {}).get('X', ''):
        raise ValueError('official_terminal_wrong_super_reduce_activity')
    results = []
    for n,row in sorted(cells.items()):
        if n < 4 or not row.get('E'):
            continue
        item = _effective(cells, merges, n, 'A')
        status = _effective(cells, merges, n, 'Z')
        if (_effective(cells, merges, n, 'X') not in ('10','10.0','10.00')
                or _effective(cells, merges, n, 'Y') not in ('','0','0.00')):
            raise ValueError('official_terminal_rate_or_subsidy_changed')
        results.append(dict(item=item,sku=row['E'],activity_price=row.get('N'),
                            status=status,warning=_effective(cells,merges,n,'AA'),excel_row=n))
    if canonical_scope(results) != canonical_scope(request['scope']):
        raise ValueError('official_terminal_full_scope_or_price_mismatch')
    statuses = {r['status'] for r in results}
    status = ('success' if statuses == {'成功'} else 'failed' if statuses == {'失败'}
              else 'partial' if statuses == {'成功','失败'} else 'unknown')
    for row in results:
        row['failure_kind'] = ('no_sales_this_campaign' if row['status']=='失败'
            and re.search(r'近\s*60\s*天(?:销售件数|销量)\s*0\s*件',row['warning'])
            else 'other_official_failure' if row['status']=='失败' else None)
    return dict(status=status, rows=results, reference_state_ignored=True,
                reference_final_ignored=True, warning_is_failure=False,
                successful_pairs=[[r['item'],r['sku']] for r in results if r['status']=='成功'],
                failed_pairs=[[r['item'],r['sku']] for r in results if r['status']=='失败'],
                unknown_pairs=[[r['item'],r['sku']] for r in results if r['status'] not in ('成功','失败')],
                no_sales_items=sorted({r['item'] for r in results if r['failure_kind']=='no_sales_this_campaign'}),
                no_sales_applies_to='this_campaign_only_not_global_blacklist',
                withdraw_requires_exact_offer_scope_and_receipt=True)


def transition(prepared, previous, event):
    request = prepared['request']
    event_items = event.get('items') if 'items' in event else [event.get('item')]
    if (event.get('experiment_type') != RECOVERY or event.get('campaign') != request['campaign']
            or sorted(event_items) != sorted(records(request))
            or event.get('file_sha256') != prepared['file_sha256']):
        raise ValueError('event_phase_campaign_item_or_file_mismatch')
    raw = pinned(event['source'])
    kind = event.get('kind')
    state = dict(previous)
    if kind in ('risk_warning', 'export_refresh'):
        return state  # observed UI text is neither submission nor recovery
    if kind == 'user_uploaded':
        if previous['state'] != 'prepared_not_submitted' or prepared.get('mode') == 'historical_receipt_only':
            raise ValueError('upload_already_reported_no_retry')
        return dict(state, state='awaiting_official_terminal')
    if kind == 'official_import_terminal':
        if not event.get('operation_reference'):
            raise ValueError('explicit_official_terminal_required')
        parsed = import_terminal(request, raw)
        if event.get('status') is not None and event['status'] != parsed['status']:
            raise ValueError('declared_status_disagrees_with_official_result')
        terminal = dict(parsed, operation_reference=event['operation_reference'], source=event['source'])
        prior = previous.get('official_terminal')
        if prior:
            if prior == terminal:
                return state  # don't regress recovered state on repeated receipt consumption
            if prior['status'] != 'unknown' or prior['operation_reference'] != terminal['operation_reference']:
                raise ValueError('official_terminal_already_recorded_do_not_replace')
        return dict(state, official_terminal=terminal,
                    state={'success':'awaiting_same_marketing_readback','partial':'partially_accepted_no_retry',
                           'failed':'failed_no_retry','unknown':'unknown_no_retry'}[parsed['status']])
    if kind == 'same_marketing_readback':
        terminal = previous.get('official_terminal')
        if not terminal or terminal['status'] not in ('success','partial') or event.get('operation_reference') != terminal['operation_reference']:
            raise ValueError('same_successful_official_operation_required')
        accepted = {tuple(p) for p in terminal['successful_pairs']}
        narrowed = dict(request,scope=[r for r in request['scope'] if (r['item'],r['sku']) in accepted])
        if terminal['status']=='partial':
            # A product-level status is not proof for a partly accepted SKU subset.
            items = {i for i,s in accepted}
            if any((r['item'],r['sku']) not in accepted for r in request['scope'] if r['item'] in items):
                raise ValueError('partial_product_acceptance_requires_specific_sku_readback')
            narrowed.pop('item',None);narrowed.pop('original_marketing_id',None)
            narrowed['marketing_records']={i:m for i,m in records(request).items() if i in items}
        recovered = readback(narrowed, raw)
        return dict(state, state=('recovered_this_trial' if terminal['status']=='success' else 'accepted_scope_recovered_with_failures') if recovered else 'not_recovered_no_retry',
                    recovery_supported=True if recovered else None,
                    accepted_scope_recovered=recovered,
                    business_complete=recovered and terminal['status']=='success', automatic_expand=False)
    raise ValueError('unsupported_event_no_inferred_recheck')


def record(trial_dir, event_path, *, historical_missing_original=False):
    root = Path(trial_dir).resolve(strict=True)
    prepared = load(root/'prepared.json')
    reserved = load(root/'request.json')
    if (prepared['request'] != reserved['request']
            or prepared['request_sha256'] != reserved['request_sha256']
            or canonical_scope(prepared['original_rows']) != canonical_scope(prepared['request']['scope'])):
        raise ValueError('existing_trial_reservation_or_scope_changed')
    original = Path(prepared['file'])
    if original.exists() and sha(original.read_bytes()) != prepared['file_sha256']:
        raise ValueError('prepared_workbook_changed')
    event = load(event_path)
    lock = root/'record.lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError('record_in_progress_do_not_retry_blindly') from exc
    try:
        events = sorted(root.glob('event-*.json'))
        previous = load(events[-1])['result'] if events else prepared
        if not original.exists():
            already_historical = previous.get('mode') == 'historical_receipt_only'
            if not already_historical and not historical_missing_original:
                raise ValueError('prepared_workbook_missing')
            if historical_missing_original and event.get('kind') != 'official_import_terminal':
                raise ValueError('missing_original_override_only_for_matching_official_terminal')
            # Same reserved trial, never a new registration or upload opportunity.
            # transition still verifies official file hash, full SKU/price/rate and operation.
            prepared = dict(prepared,mode='historical_receipt_only')
            previous = dict(previous,mode='historical_receipt_only',original_workbook_missing=True,
                            original_workbook_verified=False,
                            original_file_hash_basis='existing_prepared_receipt_not_reconstructed_workbook')
        result = transition(prepared, previous, event)
        write_new(root/f'event-{len(events)+1:04d}.json', dict(event=event,
                  event_file_sha256=sha(Path(event_path).read_bytes()), result=result))
        return result
    finally:
        lock.rmdir()  # only this empty, exclusively owned local lock


def delivery_request(receipt_path):
    """Read a pinned existing delivery; a receipt alone never proves submission."""
    path = Path(receipt_path).resolve(strict=True)
    doc = load(path)
    auth = normalize_authorization(doc)
    if auth.get('experiment_type') != RECOVERY:
        raise ValueError('delivery_is_not_super_reduce_recovery')
    audit_ref = dict(path=doc.get('current_source', doc.get('source')),
                     sha256=doc.get('current_source_sha256', doc.get('source_sha256')))
    audit = json.loads(pinned(audit_ref).decode('utf-8-sig'))
    source = {k:audit['source'][k] for k in ('path','sha256')}
    request = dict(schema='campaign_recovery_trial_request_v1',experiment_type=RECOVERY,
        template_kind='super_reduce_activity',campaign=CAMPAIGN,official_rate='10%',
        scope=auth['scope'],authorization=dict(path=str(path),sha256=sha(path.read_bytes())),
        source=source,master=dict(path=str(MASTER),sha256=MASTER_SHA),
        platform_write=False,price_change=False,withdraw=False,single_discount_change=False,
        automatic_expand=False)
    for key in ('item','original_marketing_id','marketing_records'):
        if key in auth:
            request[key]=auth[key]
    return path, doc, request


def adopt_delivery(receipt_path, *, ledger=LEDGER):
    """Normalize the existing 03 delivery, then adopt bytes; never remake its file."""
    path, doc, request = delivery_request(receipt_path)
    file = Path(doc['file'])
    if sha(file.read_bytes()) != doc['sha256']:
        raise ValueError('delivered_workbook_hash_changed')
    # Validate before leaving even a normalized request artifact.
    expected,_ = build(request)
    same_package(expected,file.read_bytes())
    normalized = path.parent/('recovery-request-'+digest(request)+'.json')
    if normalized.exists():
        if load(normalized) != request:
            raise ValueError('normalized_request_changed')
    else:
        write_new(normalized,request)
    return prepare(normalized,workbook_path=file,ledger=ledger)


def register_result(receipt_path, result_path, result_sha256, operation_reference, *, ledger=LEDGER):
    """Historical result-only hold: no XLSX generation, even if original is missing."""
    path, doc, request = delivery_request(receipt_path)
    if not re.fullmatch(r'[a-f0-9]{64}',str(doc.get('sha256',''))):
        raise ValueError('original_delivery_hash_required')
    auth = json.loads(pinned(request['authorization']).decode('utf-8-sig'))
    rows = validate(request,auth,pinned(request['source']))
    original = Path(doc['file'])
    present = original.is_file()
    if present and sha(original.read_bytes()) != doc['sha256']:
        raise ValueError('delivered_workbook_hash_changed')
    event = dict(experiment_type=RECOVERY,campaign=CAMPAIGN,items=sorted(records(request)),
                 file_sha256=doc['sha256'],kind='official_import_terminal',
                 source=dict(path=str(Path(result_path).resolve(strict=True)),sha256=result_sha256),
                 operation_reference=operation_reference)
    prepared = dict(request=request,mode='historical_receipt_only',state='awaiting_official_terminal',
                    file=str(original),file_sha256=doc['sha256'],
                    original_workbook_verified=present,original_workbook_missing=not present,
                    original_file_hash_basis='delivery_receipt_not_reconstructed_workbook',
                    recovery_supported=None,business_complete=False,whole_event_complete=False,
                    platform_write=False,automatic_retry=False,automatic_expand=False,
                    single_discount_replay_allowed=False,official_terminal=None)
    # Match exact complete scope, prices, rate and actual Z/AA before any ledger write.
    terminal = transition(prepared,prepared,event)
    root, initial = reserve(request,sha(path.read_bytes()),rows,ledger)
    prepared = dict(initial,**prepared,trial_dir=str(root))
    terminal = dict(initial,**terminal,trial_dir=str(root))
    write_new(root/'prepared.json',prepared)
    write_new(root/'event-0001.json',dict(event=event,result=terminal,
              provenance_note='Original upload file availability is explicit; official report matched to delivery scope.'))
    return terminal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'adopt'):
        p = sub.add_parser(command)
        p.add_argument('--request', type=Path, required=True)
        p.add_argument('--output-dir' if command=='prepare' else '--workbook', type=Path, required=True)
    p = sub.add_parser('record')
    p.add_argument('--trial-dir', type=Path, required=True)
    p.add_argument('--event', type=Path, required=True)
    p.add_argument('--historical-missing-original', action='store_true',
                   help='Record matching official result in the existing trial if original upload XLSX moved; never recreate it.')
    p = sub.add_parser('adopt-delivery')
    p.add_argument('--receipt', type=Path, required=True)
    p = sub.add_parser('register-result')
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--result', type=Path, required=True)
    p.add_argument('--result-sha256', required=True)
    p.add_argument('--operation-reference', required=True)
    args = parser.parse_args(argv)
    if args.command == 'record':
        result = record(args.trial_dir, args.event,historical_missing_original=args.historical_missing_original)
    elif args.command == 'adopt-delivery':
        result = adopt_delivery(args.receipt)
    elif args.command == 'register-result':
        result = register_result(args.receipt,args.result,args.result_sha256,args.operation_reference)
    else:
        result = prepare(args.request, output_dir=getattr(args,'output_dir',None),
                         workbook_path=getattr(args,'workbook',None))
    print(json.dumps({k:result.get(k) for k in ('trial_dir','state','file','file_sha256','platform_write',
                     'recovery_supported','business_complete','automatic_retry','automatic_expand')},ensure_ascii=False))


if __name__ == '__main__':
    main()
