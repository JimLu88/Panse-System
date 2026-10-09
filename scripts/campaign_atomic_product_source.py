"""Validate native atomic product-export provenance without fabricating legacy jobs."""
import hashlib
import json
from pathlib import Path
import re
SurfaceError = ValueError
from campaign_continuous_policy import fingerprint


def from_atomic_export(job, request, *, identity, snapshot, root, parse_export, complete_scope):
    def validate(tool, payload):
        return fingerprint(['atomic', tool, {k:v for k,v in payload.items() if k!='request_id'}])
    payload=request.get('payload',{})
    result=job.get('result') or {}
    if (job.get('operation')!='campaign_atomic' or job.get('state')!='finished'
            or request.get('tool_id')!='sellable_product_export'
            or result.get('tool_id')!='sellable_product_export' or result.get('state')!='succeeded'
            or payload.get('identity')!=identity or result.get('identity')!=identity
            or payload.get('snapshot_version')!=snapshot
            or request.get('request_sha')!=fingerprint(payload)
            or job.get('job_id')!=validate('sellable_product_export',payload)):
        raise SurfaceError('atomic_product_export_original_identity_mismatch')
    base=(Path(root)/job['job_id']).resolve(strict=True)
    if not base.is_relative_to(Path(root).resolve(strict=True)):
        raise SurfaceError('atomic_product_export_root_mismatch')
    def read_file(value,suffix):
        path=Path(value).resolve(strict=True)
        if not path.is_relative_to(base) or not path.is_file() or path.suffix!=suffix:
            raise SurfaceError('atomic_product_export_artifact_outside_original_job')
        if path.stat().st_size>30_000_000:
            raise SurfaceError('atomic_product_export_artifact_too_large')
        return path.read_bytes()
    saved=read_file(base/'atomic-result.json','.json')
    if json.loads(saved)!=result:
        raise SurfaceError('atomic_product_export_saved_result_changed')
    data=result.get('result') or {}
    pages=data.get('page_count');total=data.get('observed_total');ids=data.get('observed_item_ids',[])
    files=data.get('files',[])
    if (data.get('state')!='downloaded' or type(pages) is not int or pages<1
            or type(total) is not int or total<1 or len(files)!=pages
            or not isinstance(ids,list) or len(ids)!=total or len(set(ids))!=total
            or any(not isinstance(i,str) or not re.fullmatch(r'[0-9]{1,20}',i) for i in ids)):
        raise SurfaceError('atomic_product_export_full_page_scope_invalid')
    seen=set();records=set();raw_files=[]
    for number,entry in enumerate(files,1):
        scope=entry['scope'];record=entry['record'];page_ids=scope['item_ids'];record_id=str(record['id'])
        if (scope.get('on_sale') is not True or scope.get('page')!=number
                or scope.get('page_count')!=pages or scope.get('total')!=total
                or not page_ids or len(set(page_ids))!=len(page_ids) or seen.intersection(page_ids)
                or not re.fullmatch(r'[0-9]{1,20}',record_id) or record_id in records
                or record.get('rowCount')!=len(page_ids)):
            raise SurfaceError('atomic_product_export_page_record_mismatch')
        original=json.loads(read_file(base/f'page-{number}-request.json','.json'))
        downloaded=json.loads(read_file(base/f'page-{number}-downloaded.json','.json'))
        if (original.get('scope')!=scope or not isinstance(original.get('before_ids'),list)
                or record_id in {str(v) for v in original['before_ids']} or downloaded!=entry):
            raise SurfaceError('atomic_product_export_causal_page_receipt_mismatch')
        raw=read_file(entry['path'],'.xlsx')
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise SurfaceError('atomic_product_export_workbook_changed')
        if {row['item'] for row in parse_export(raw)}!=set(page_ids):
            raise SurfaceError('atomic_product_export_workbook_page_scope_mismatch')
        raw_files.append((raw,entry['sha256']));seen.update(page_ids);records.add(record_id)
    if seen!=set(ids):raise SurfaceError('atomic_product_export_combined_scope_mismatch')
    return complete_scope(raw_files,observed_item_ids=ids,observed_page_count=pages,observed_total=total,
        page_evidence={'operation':'campaign_atomic','tool_id':'sellable_product_export','job_id':job['job_id'],
                       'snapshot_version':snapshot,'path':str(base/'atomic-result.json'),
                       'sha256':hashlib.sha256(saved).hexdigest()})


def validate_reference(value):
    if not isinstance(value,dict):
        raise ValueError('exact_existing_product_export_reference_required')
    native=value.get('kind')=='atomic'
    keys={'job_id','snapshot_request_id','kind'} if native else {'job_id','snapshot_request_id'}
    pattern=r'[a-zA-Z0-9_-]{1,80}' if native else r'[0-9a-f]{64}'
    if (set(value)!=keys or not re.fullmatch(r'[0-9a-f]{64}',str(value.get('job_id','')))
            or not re.fullmatch(pattern,str(value.get('snapshot_request_id','')))):
        raise ValueError('exact_existing_product_export_reference_required')
    return native


def resolve(job, *, reference, identity, roots, allowed_identities=None):
    """Read the original local ledger, then validate every official page/file."""
    import sqlite3
    from campaign_product_scope import parse_export,complete_scope
    if not validate_reference(reference) or job.get('job_id')!=reference['job_id']:
        raise ValueError('native_product_export_reference_mismatch')
    locations=set()
    for value in roots:
        base=Path(value).resolve(strict=True)
        for root in (base,base/'campaign-transfers'):
            folder=(root/reference['job_id']).resolve()
            if folder.is_relative_to(base) and (folder/'atomic-result.json').is_file() and (root/'jobs.sqlite').is_file():
                locations.add(root.resolve())
    if len(locations)!=1:
        raise ValueError('native_product_export_source_not_unique')
    root=locations.pop()
    with sqlite3.connect((root/'jobs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(reference['job_id'],)).fetchone()
    if row is None:
        raise ValueError('native_product_export_original_missing')
    request=json.loads(row['request']);result=json.loads(row['result'])
    if (row['request_sha']!=fingerprint({'operation':'campaign_atomic','request':request})
            or any(job.get(k)!=row[k] for k in ('operation','state'))
            or job.get('result')!=result):
        raise ValueError('native_product_export_ledger_changed')
    source_identity=request.get('payload',{}).get('identity')
    allowed=allowed_identities if allowed_identities is not None else [identity]
    if source_identity not in allowed or source_identity.get('shop_name')!=identity.get('shop_name'):
        raise ValueError('native_product_export_campaign_not_in_request')
    return from_atomic_export(job,request,identity=source_identity,
        snapshot=reference['snapshot_request_id'],root=root,
        parse_export=parse_export,complete_scope=complete_scope)
