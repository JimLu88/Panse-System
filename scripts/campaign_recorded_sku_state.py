"""Reconcile recorded OFF switches with exact saved export SKU identities.

This is a failed-scope file exclusion, never an ERP/platform SKU deletion.
No absent row, zero stock, name similarity or editable price supplies OFF proof.
"""
import hashlib
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path


def row_matches(fact, row):
    cells=row.get('cells',[])
    values={str(c.get('text','')).strip() for c in cells}
    values.update(str(f.get('value','')).strip() for c in cells for f in c.get('fields',[]))
    attributes=[part.split(':',1) for part in fact.get('attributes','').strip(';').split(';')]
    if not attributes or any(len(p)!=2 or not p[1] or p[1] not in values for p in attributes):return False
    # Codes are independent of names. A blank code cannot silently match a new
    # populated replacement SKU bearing the same visible label.
    codes={v for v in values if re.fullmatch(r'P[A-Z]+[A-Z0-9_-]{8,}',v)}
    code=fact.get('sku_code','')
    if codes != ({code} if code else set()):return False
    for unit,key in (('元','price'),('件','stock')):
        fields=[f for c in cells if c.get('text','').strip()==unit for f in c.get('fields',[])]
        if len(fields)!=1:return False
        try:
            a,b=Decimal(fields[0]['value']),Decimal(fact[key])
            if not a.is_finite() or not b.is_finite() or a!=b:return False
        except (InvalidOperation,KeyError,TypeError):return False
    return True


def disabled_from_rows(scope, records):
    if scope.get('complete') is not True:raise ValueError('recorded_state_complete_export_required')
    facts=[entry['facts'] for entry in scope['sku_facts']];resolved=[]
    counts={}
    for record in records:counts[record.get('item')]=counts.get(record.get('item'),0)+1
    for record in records:
        if record.get('state')!='read' or record.get('platform_write') is not False:continue
        # A second, possibly contradictory observation of the same item must
        # not be resolved by picking its convenient OFF row.
        if counts[record.get('item')]!=1:continue
        item=record['item']; local=[f for f in facts if f['item']==item]
        requested=set(record['requested_skus'])
        for row in record['rows']:
            switches=row.get('switches',[])
            if (row.get('enabled') is not False or len(switches)!=1 or switches[0].get('aria')!='false'
                    or 'next-switch-off' not in switches[0].get('class_name','').split()):continue
            matches=[f for f in local if row_matches(f,row)]
            if len(matches)!=1:continue
            f=matches[0]
            if f['sku'] not in requested:continue
            # Multiple current rows with equal identity are ambiguous even if
            # one is OFF. Never choose the convenient state.
            if len([r for r in record['rows'] if row_matches(f,r)])!=1:continue
            resolved.append(dict(item=item,sku=f['sku'],row_index=row['index']))
    pairs=[(r['item'],r['sku']) for r in resolved]
    if len(pairs)!=len(set(pairs)):raise ValueError('recorded_state_duplicate_item')
    return resolved


def disabled_from_batch(scope, batch):
    """Consume positive per-item facts without promoting the whole batch.

    The immutable job and video are verified by the caller. A sibling timeout
    or video capture gap is not proof against an exact OFF row, and is never
    permission to infer the missing sibling's state.
    """
    if (batch.get('state') not in ('batch_read_complete','batch_read_partial')
            or batch.get('platform_write') is not False
            or batch.get('error') not in (None,'batch_evidence_incomplete')):
        raise ValueError('recorded_state_batch_incomplete')
    return disabled_from_rows(scope,batch.get('records',[]))


def verified_disabled(doc):
    """Revalidate immutable original files and the real finished fixed job."""
    from campaign_entry_authority import load,file_sha
    from campaign_continuous_policy import fingerprint
    ref=doc.get('recorded_state')
    if not ref:return set()
    sources={r['path']:r['sha256'] for r in doc['sources']}
    if sources.get(ref['path'])!=ref['sha256'] or file_sha(ref['path'])!=ref['sha256']:
        raise ValueError('recorded_state_source_changed')
    path=Path(ref['path']).resolve(strict=True);batch=load(path)
    payload=batch['source'];jid=fingerprint(['product_sku_batch_read',payload])
    expected=Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers').resolve()
    if path != expected/jid/'sku-batch-read.json':raise ValueError('recorded_state_job_path_mismatch')
    db=sqlite3.connect((expected/'jobs.sqlite').as_uri()+'?mode=ro',uri=True)
    try:
        row=db.execute('SELECT operation,state,result,request FROM campaign_transfer_jobs WHERE id=?',(jid,)).fetchone()
    finally:db.close()
    if not row or row[0]!='product_sku_batch_read' or row[1]!='finished':
        raise ValueError('recorded_state_job_not_finished')
    result=json.loads(row[2]);request=json.loads(row[3])
    if (request.get('payload')!=payload or request.get('request_sha')!=fingerprint(payload)
            or result!={**batch,'evidence_path':str(path)}):
        # Windows separators can differ in the saved path; content cannot.
        if (request.get('payload')!=payload or request.get('request_sha')!=fingerprint(payload)
                or Path(result.get('evidence_path','')).resolve()!=path
                or {k:v for k,v in result.items() if k!='evidence_path'}!=batch):
            raise ValueError('recorded_state_job_evidence_changed')
    recording=batch['recording'];video=Path(recording['video']).resolve(strict=True)
    if (not video.is_relative_to(path.parent) or not recording.get('frames') or recording.get('active') is not False
            or file_sha(video)!=recording.get('video_sha256')):raise ValueError('recorded_state_video_not_verified')
    # Capture gaps never erase a verified DOM result, just as they cannot erase
    # an official upload terminal. Preserve the gap; no full-recording claim.
    complete=not bool(recording.get('error') or recording.get('capture_errors') or recording.get('duration_limit_reached'))
    if ref.get('recording_complete') is not complete:raise ValueError('recorded_state_capture_gap_misreported')
    computed=disabled_from_batch(load(doc['official_scope_path'])['scope'],batch)
    expected_rows=ref['disabled']
    if computed!=expected_rows:raise ValueError('recorded_state_disabled_scope_changed')
    return {(r['item'],r['sku']) for r in computed}


def register_recorded_catalog(authority, source_catalog, batch_path, output):
    """Add a scoped supplement; keep every original receipt and price intact."""
    from pathlib import Path
    from campaign_entry_authority import load,file_sha
    from campaign_catalog_repair import documents
    source_catalog=Path(source_catalog).resolve();batch_path=Path(batch_path).resolve()
    registered=[s for s in authority.sources() if s['kind']=='catalog' and Path(s['path']).resolve()==source_catalog]
    if len(registered)!=1:raise ValueError('recorded_catalog_registered_parent_required')
    parent=load(source_catalog)
    snapshot=dict(captured_at=parent['snapshot_captured_at'],resolved_price_version_sha256=parent['price_version'],
                  catalog_repair_sources=[dict(path=str(source_catalog),sha256=file_sha(source_catalog))])
    list(documents(snapshot))
    batch=load(batch_path);recording=batch['recording']
    disabled=disabled_from_batch(load(parent['official_scope_path'])['scope'],batch)
    if not disabled:raise ValueError('recorded_catalog_no_exact_off_facts')
    complete=not bool(recording.get('error') or recording.get('capture_errors') or recording.get('duration_limit_reached'))
    doc={k:parent[k] for k in ('schema','snapshot_captured_at','price_version','registry_path','official_scope_path')}
    paths=list(dict.fromkeys([str(source_catalog),parent['registry_path'],parent['official_scope_path'],str(batch_path)]))
    doc.update(sources=[dict(path=p,sha256=file_sha(p)) for p in paths],retired=[],restored=[],
               recorded_state=dict(path=str(batch_path),sha256=file_sha(batch_path),recording_complete=complete,disabled=disabled),
               scope='Exact recorded OFF rows only; sibling gaps preserved. No SKU, stock, price or historical claim changes.')
    verified_disabled(doc)  # Includes immutable original job and video verification.
    output=Path(output)
    encoded=json.dumps(doc,ensure_ascii=False,indent=2)
    if output.exists():
        if load(output)!=doc:raise ValueError('recorded_catalog_output_immutable')
    else:
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as f:f.write(encoded)
    authority.register_source(output,'catalog',file_sha(output))
    return dict(path=str(output),sha256=file_sha(output),disabled_skus=len(disabled),
                items=sorted({r['item'] for r in disabled}),recording_complete=complete,platform_write=False)
