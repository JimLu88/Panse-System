"""Validate saved Web-Agent readback against exact expected offer/SKU amounts.

No price edits. A readable page is not proof that the price matches; all scope,
time, amounts and the immutable observed receipt must agree.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from campaign_continuous_policy import fingerprint


def verify(job, *, read_request_id, shop, start, end, expected_rows, roots):
    if (job.get('operation')!='discount_readback' or job.get('state')!='finished'
            or job.get('job_id')!=fingerprint(['discount_readback',shop,read_request_id])):
        raise ValueError('discount_readback_job_not_finished_or_bound')
    result=job.get('result') or {}
    if (result.get('state')!='readback' or result.get('read_request_id')!=read_request_id
            or result.get('shop_name')!=shop or result.get('platform_write') is not False
            or result.get('price_window')!={'start':start,'end':end}):
        raise ValueError('discount_readback_request_or_window_changed')
    path=Path(result['evidence_path']).resolve(strict=True)
    if path.suffix.lower()!='.json' or not any(path.is_relative_to(Path(r).resolve(strict=True)) for r in roots):
        raise ValueError('discount_readback_evidence_outside_configured_roots')
    raw=path.read_bytes();saved=json.loads(raw)
    for key in ('state','rows','shop_name','read_request_id','price_window','platform_write'):
        if saved.get(key)!=result.get(key):raise ValueError('discount_readback_observation_changed')
    expected={}
    def amount(value):
        try:value=Decimal(str(value))
        except InvalidOperation:raise ValueError('discount_readback_amount_invalid')
        if not value.is_finite() or value<0 or value!=value.quantize(Decimal('.01')):
            raise ValueError('discount_readback_amount_invalid')
        return value
    for row in expected_rows:
        key=(str(row['offer_id']),str(row['item']),str(row['sku']))
        if not all(k.isdigit() for k in key) or key in expected:
            raise ValueError('discount_readback_expected_scope_invalid')
        expected[key]=amount(row['deduct'])
    if not expected:raise ValueError('discount_readback_expected_scope_empty')
    actual={}
    for row in result['rows']:
        window=row['window']
        if window.get('start')!=start or window.get('end')!=end:
            raise ValueError('actual_discount_readback_window_mismatch')
        for sku,value in row['values'].items():
            key=(str(window['offer_id']),str(row['item']),str(sku))
            if key in actual:raise ValueError('duplicate_discount_readback_pair')
            actual[key]=amount(value)
    if set(actual)!=set(expected):raise ValueError('discount_readback_sku_or_offer_scope_mismatch')
    differences=[dict(offer_id=k[0],item=k[1],sku=k[2],expected=str(expected[k]),actual=str(actual[k]))
                 for k in sorted(expected) if actual[k]!=expected[k]]
    return dict(all_correct=not differences,start=start,end=end,
        items=sorted({k[1] for k in expected}),differences=differences,platform_write=False,
        evidence={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'job_id':job['job_id']})
