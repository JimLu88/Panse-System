"""Use only the verified successful complement of a partial discount import."""
from decimal import Decimal

from campaign_entry_authority import file_sha,load
from campaign_discount_failure_report import verify_failure_rows


def verified_rows(offer,proof):
    if file_sha(proof['path'])!=proof['sha256']:raise ValueError('partial_discount_receipt_changed')
    doc=load(proof['path'])
    if (doc.get('phase')!='discount' or not doc.get('terminal') or not doc.get('partial_items')
            or str(doc.get('batch_id'))!=str(offer.get('platform_offer_id'))
            or any(doc.get(k)!=offer[k] for k in ('start','end'))):
        raise ValueError('exact_partial_discount_receipt_required')
    observation=doc['official_observation']
    if file_sha(observation['path'])!=observation['sha256']:raise ValueError('partial_discount_observation_changed')
    observed=load(observation['path'])
    if (observed.get('state')!='verified_partial_offer_terminal'
            or str(observed.get('offer_id'))!=str(doc['batch_id'])
            or observed.get('readbacks')!=doc['readbacks']):
        raise ValueError('partial_discount_official_readback_changed')
    planned={(r['item'],r['sku']):Decimal(str(r['deduct'])) for r in offer['rows']}
    feedback=doc['feedback']
    failures=verify_failure_rows(feedback['path'],feedback['sha256'],set(planned),observed['failed'])
    if failures!=doc['failure_rows']:raise ValueError('partial_discount_original_failures_changed')
    failed={(r['item'],r['sku']) for r in failures}
    actual={}
    for row in doc['readbacks']:
        if any(row['window'].get(k)!=doc[k] for k in ('start','end')) or str(row['window'].get('offer_id'))!=str(doc['batch_id']):
            raise ValueError('partial_discount_saved_window_changed')
        for sku,amount in row['values'].items():
            pair=row['item'],sku
            if pair in actual:raise ValueError('partial_discount_duplicate_readback')
            actual[pair]=Decimal(str(amount))
    if actual!={k:v for k,v in planned.items() if k not in failed} or len(actual)!=observed['success']:
        raise ValueError('partial_discount_success_complement_not_proven')
    return set(actual)


def invalid_errors(terminal):
    """Only the official exact invalid-ID error; other partial errors stay held."""
    result=[]
    for error in terminal.get('errors',[]):
        if error.get('message','').strip()=='参数错误:skuId不是商品的有效sku':
            result.append(dict(error,kind='mapping',official_invalid_or_disabled=True))
        else:result.append(error)
    return dict(terminal,errors=result)
