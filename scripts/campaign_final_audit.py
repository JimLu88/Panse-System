"""Mandatory POST-run inventory reconciliation, never a preflight or retry.

Old failures/success receipts are context only. A fresh recorded SKU export is
the only source of current registration. Missing evidence remains unknown.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import CampaignTransport, persist
from campaign_entry_authority import file_sha, load

POLICY = 'campaign-final-sku-audit-20260914-v1'
READ_REVISION = 2  # Read-only guard repair; previous failed audit remains intact.
ACCEPTED = {'已发布设定', '已生效', '活动中', '进行中', '报名成功', '审核通过'}


def target_window(payload):
    if 'time_binding' in payload:
        binding=payload['time_binding']
        from campaign_segmented_time import bind_request
        actual=bind_request(binding['request_path'],binding['segment']['segment_id'])
        if actual!=binding:raise ValueError('final_audit_time_binding_changed')
        return dict(binding['segment']['price_window'])
    return {k:payload['identity'][k] for k in ('start','end')}


def parse_inventory(raw):
    from campaign_official_template import _archive, _read, _effective, sheet_path
    with _archive(raw) as archive:
        _, _, rows, merges = _read(archive, sheet_path(archive, '已报商品列表'))
        headers = [(n, c) for n, c in rows.items()
                   if {'商品ID', 'SKUID', '商品状态', '营销ID'}.issubset(c.values())]
        if len(headers) != 1: raise ValueError('final_export_schema_changed')
        n, header = headers[0]
        columns = {}
        for key, label in [('item','商品ID'), ('sku','SKUID'), ('state','商品状态'), ('marketing_id','营销ID'), ('activity_price','活动价')]:
            found = [c for c, value in header.items() if value == label]
            if len(found) != 1: raise ValueError('final_export_column_ambiguous')
            columns[key] = found[0]
        result = []
        current = {}
        for number, cells in sorted(rows.items()):
            if number <= n: continue
            sku = cells.get(columns['sku'], '').strip()
            if not sku.isdigit(): continue
            explicit_item = _effective(rows, merges, number, columns['item']).strip()
            if explicit_item and explicit_item != current.get('item'): current = {}
            for key, column in columns.items():
                value = _effective(rows, merges, number, column).strip()
                if value or key in ('sku','activity_price'): current[key] = value
            if not all(current.get(k,'').isdigit() for k in ('item','sku','marketing_id')):
                raise ValueError('final_export_identity_missing')
            result.append(dict(current, row=number))
        return result


def reconcile(expected, rows, *, identity, window, observed_at, coverage_complete):
    """Every expected pair gets an explicit result, including absent/ambiguous."""
    by_pair = defaultdict(list)
    for row in rows: by_pair[(row['item'], row['sku'])].append(row)
    now = datetime.fromisoformat(observed_at).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    begin, end = [datetime.fromisoformat(window[k]) for k in ('start','end')]
    details = []
    for target in expected:
        matches = by_pair[(target['item'], target['sku'])]
        # A cancelled historical marketing record cannot conceal a live one.
        live = [r for r in matches if r.get('state') not in ('撤销报名','已撤销','已取消')]
        row = live[0] if len(live) == 1 else None
        state = row.get('state') if row else None
        from campaign_official_template import money
        try: price_present=bool(row and money(row.get('activity_price',''))>0)
        except ValueError: price_present=False
        if not coverage_complete: status = 'unknown_coverage'
        elif not matches: status = 'not_in_current_export'
        elif not live: status = 'cancelled'
        elif len(live) != 1: status = 'ambiguous_marketing_records'
        elif state in ACCEPTED and not price_present: status = 'sku_price_missing'
        elif state in ACCEPTED:
            status = 'registered_pending_window' if now < begin else 'window_ended' if now > end else 'registered_effectiveness_unconfirmed'
            if begin <= now <= end and state in ('已生效','活动中','进行中'): status = 'effective'
        elif state in ('草稿','待提交'): status = 'draft'
        elif state == '暂停': status = 'paused_reason_requires_readback'
        elif state in ('异常','审核不通过','报名失败'): status = 'abnormal'
        else: status = 'unknown_official_state'
        details.append(dict(item=target['item'], sku=target['sku'], status=status,
                            registered=bool(coverage_complete and row and state in ACCEPTED and price_present),
                            official_state=state, marketing_id=row.get('marketing_id') if row else None,
                            source_rows=[r['row'] for r in matches]))
    products = []
    for item in sorted({r['item'] for r in details}):
        own = [r for r in details if r['item'] == item]
        accepted = sum(r['registered'] for r in own)
        unknown=sum(r['status'] in ('not_in_current_export','unknown_coverage','unknown_official_state',
                    'ambiguous_marketing_records') for r in own)
        products.append(dict(item=item, exported_skus=len(own), registered_skus=accepted,
                             sku_scope_unknown=unknown,
                             status='sku_scope_unconfirmed' if unknown else 'registered' if accepted == len(own)
                             else 'partial' if accepted else 'not_verified_registered'))
    return dict(policy=POLICY, identity=identity, price_window=window, observed_at=observed_at,
                coverage_complete=coverage_complete, rows=details, products=products,
                counts=dict(Counter(r['status'] for r in details)),
                all_registered=bool(details) and all(r['registered'] for r in details),
                all_currently_effective=bool(details) and all(r['status']=='effective' for r in details),
                unknown_is_not_failure=True, missing_sku_is_not_proven_on_sale=True,
                sku_scope='all_catalog_export_skus_including_unconfirmed_backups', platform_write=False)


def manifest(root, segment):
    if segment.get('completion_kind')=='continuous_verified_failure_resubmit_v1':
        from campaign_verified_failure_resubmit import audit_manifest
        return audit_manifest(root,segment)
    run_id = segment.get('run_id')
    if not run_id: raise ValueError('final_audit_segment_scope_missing')
    matches=[]
    for path in Path(root).rglob('controller.sqlite3'):
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        try:
            matches.extend(db.execute("SELECT result FROM continuous_campaign_actions WHERE run_id=? AND step='scope' AND status='done'",(run_id,)).fetchall())
        finally: db.close()
    if len(matches)!=1: raise ValueError('final_audit_scope_not_unique')
    scope=json.loads(matches[0][0]); source=Path(scope['evidence'])
    payload=load(source.parent/'request.json')['payload']
    if scope.get('complete') is not True: raise ValueError('final_audit_full_scope_missing')
    items=set(scope['erp_sellable']) & {r['item'] for r in scope['platform_rows'] if r['on_sale'] is True}
    if items != set(segment.get('initial_scope') or []): raise ValueError('final_audit_initial_scope_changed')
    pairs=sorted({(entry['facts']['item'],entry['facts']['sku']) for entry in scope['sku_facts'] if entry['facts']['item'] in items})
    if {i for i,s in pairs} != items: raise ValueError('final_audit_sku_scope_incomplete')
    window=target_window(payload)
    return dict(payload=payload, window=window, pairs=[dict(item=i,sku=s) for i,s in pairs],
                scope_sha256=file_sha(source), scope_path=str(source))


def execution_export_conflicts(result,audit):
    """A standing marketing record is not proof a rejected new batch passed."""
    execution={s.get('segment_id'):s for s in result.get('segments',[])}
    conflicts=[]
    for segment in audit.get('segments',[]):
        original=execution.get(segment.get('segment_id'),{})
        for product in segment.get('products',[]):
            item=product['item'];exceptions=original.get('exceptions',{}).get(item) or []
            registered=product.get('status')=='registered'
            if exceptions and registered:
                conflicts.append(dict(segment_id=segment.get('segment_id'),item=item,
                    reason='standing_registration_does_not_resolve_current_failed_attempt',
                    execution_exceptions=exceptions))
            elif item in original.get('success',{}) and not registered:
                conflicts.append(dict(segment_id=segment.get('segment_id'),item=item,
                    reason='prior_success_not_confirmed_by_current_sku_export'))
    return conflicts


def finalize(request, result, *, root, authority, edge, artifact_roots):
    """Called by every CLI execution branch before publishing its final result.

    Re-entry with unchanged terminal evidence reads the same immutable audit;
    no second export, upload, price edit, or rotation is dispatched.
    """
    root=Path(root)
    raw={k:v for k,v in result.items() if k not in ('final_audit','all_signed_up','all_currently_effective',
        'recordings','recording_errors','full_recording_verified','failure_handling','execution_export_conflicts')}
    revision=3 if any(p.get('campaign_id')=='legacy' for p in request.get('pages',{}).values()) else READ_REVISION
    key=fingerprint([POLICY,revision,raw]); folder=root/'final-audit'/key
    target=folder/'audit-v3.json'  # Reproject the same export against the verified price window.
    if target.exists(): audit=load(target)
    else:
        segments=[]; gaps=[]
        for segment in result.get('segments',[]):
            sid=segment.get('segment_id','unknown')
            try:
                data=manifest(root,segment)
                transport=CampaignTransport(edge,authority,root=folder/sid,request={},artifact_roots=artifact_roots)
                identity=transport.identity(data['payload'])
                request_payload=dict(identity=identity,read_request_id=fingerprint([key,sid]),price_window=data['window'])
                # Never touch a page while an unresolved business action owns it.
                if result.get('status')!='complete': raise ValueError('final_audit_deferred_unsettled_execution')
                job=transport.job('final_inventory',request_payload,folder/sid)
                value=job.get('result') or {}
                # This export covers the whole official activity, not a price
                # segment. Its contextual price_window is not timing evidence;
                # the hashed program time binding above is the target window.
                if (value.get('identity')!=identity
                        or value.get('read_request_id')!=request_payload['read_request_id']
                        or value.get('fresh_navigation') is not True or value.get('settings')!={'dimension':'SKU','status':'全部'}):
                    raise ValueError('final_audit_receipt_identity_mismatch')
                path=Path(value['file']['path']).resolve(strict=True)
                if not any(path.is_relative_to(Path(r).resolve()) for r in artifact_roots): raise ValueError('final_export_outside_roots')
                if file_sha(path)!=value['file']['sha256']: raise ValueError('final_export_changed')
                rows=parse_inventory(path.read_bytes())
                total=value.get('observed_total')
                count_key='marketing_id' if value.get('observed_count_unit')=='marketing_records' else 'item'
                coverage=type(total) is int and total==len({r[count_key] for r in rows})
                checked=reconcile(data['pairs'],rows,identity=identity,window=data['window'],
                                  observed_at=value['observed_at'],coverage_complete=coverage)
                checked.update(segment_id=sid,source_file=value['file'],job_id=job['job_id'],
                               scope_sha256=data['scope_sha256'],scope_path=data['scope_path'],
                               execution_exceptions=segment.get('exceptions',{}))
                try:
                    from campaign_recording_evidence import collect
                    checked['recordings']=collect(job,artifact_roots)
                    checked['recording_verified']=True
                except (ValueError,OSError,KeyError): checked['recording_verified']=False
                segments.append(checked)
            except Exception as exc:
                # Browser/library exceptions may contain URLs or credentials.
                reason=str(exc) if isinstance(exc,ValueError) and __import__('re').fullmatch('[a-z0-9_:]+',str(exc)) else type(exc).__name__
                gaps.append(dict(segment_id=sid,reason=reason,items=segment.get('initial_scope') or
                    sorted(set(segment.get('success',{}))|set(segment.get('exceptions',{}))|set(segment.get('pending') or []))))
        if not result.get('segments'): gaps.append(dict(reason='final_audit_no_segment_scope',items=[]))
        audit=dict(policy=POLICY, checked_at=datetime.now(timezone.utc).isoformat(),segments=segments,gaps=gaps,
                   scope='user_authorized_item_subset' if request.get('authorized_item_scope') else
                       'request_only' if request.get('schema')!='continuous_campaign_request_v1' else 'erp_sellable_and_taobao_onsale',
                   verified=bool(segments) and not gaps and all(s['coverage_complete'] for s in segments),
                   platform_write=False,automatic_retry=False)
        # A normal calendar may have stopped before reaching later segments.
        if request.get('schema')=='continuous_campaign_request_v1':
            from campaign_segmented_time import build_plan
            plan=build_plan(dict(request['calendar'],price_version='audit_scope_only'))
            if len(result.get('segments',[]))!=len(plan['segments']):
                audit['verified']=False; gaps.append(dict(reason='final_audit_unvisited_calendar_segments',items=[]))
        persist(target,audit)
    final=dict(result,final_audit=dict(audit,evidence=str(target),sha256=file_sha(target)))
    conflicts=execution_export_conflicts(result,audit)
    final['execution_export_conflicts']=conflicts
    final['all_signed_up']=bool(audit['verified'] and all(s['all_registered'] for s in audit['segments'])
                                and not result.get('remaining') and not result.get('excluded_items') and not conflicts)
    final['all_currently_effective']=bool(final['all_signed_up'] and all(s['all_currently_effective'] for s in audit['segments']))
    from campaign_continuous_recovery import persist_run_outcome
    persist_run_outcome(root,final)
    return final
