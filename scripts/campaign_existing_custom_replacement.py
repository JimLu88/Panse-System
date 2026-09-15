"""Resume an already-created, published custom replacement; never rotate here.

Only the user's exact lift-desk replacement is enabled. Ordinary SKU prices,
custom first-original bases, stock and catalog/order identities are untouched.
"""
from copy import deepcopy

ITEM='793202812082'
OLD='5610459948672'
NEW='6301497267888'
AUTH='user-20260915-furniture-cabinet-lift'
REQUEST={'authorization':AUTH,'item':ITEM,'old_sku':OLD,'new_sku':NEW}
SUPER='legacy/itemApply/3172207691'
AUTUMN='49557/49560/3538210379'
LAST_FAILED='189f1df3a1944076828c1558ce42bceb:'+ITEM
ORDINARY={'6241447059623','6241447059625','6241447059626','6241447059627',
          '6241447059629','6241447059630','6274602787683','6274602787684'}
CUSTOM={'5916518666442','5916518666443','5916518666444',NEW}
READ_JOB='b75e23ef29f89a0e0557e363293216b65f34c35fe41d9c68d47db4e7120fb8e3'
READ_PARENT='1bd5fc2953379e383bc1203c6d36c26541922eccee9561c06c68cf7d0efa97f8'


def validate(value,scope):
    if (value!=REQUEST or not isinstance(scope,dict) or scope.get('authorization')!=AUTH
            or scope.get('items')!=[ITEM]):
        raise ValueError('exact_existing_lift_replacement_scope_required')


def select_active(scope,record):
    from campaign_recorded_sku_state import row_matches
    from campaign_verified_failure_resubmit import switch_state
    if (scope.get('complete') is not True or record.get('item')!=ITEM
            or record.get('state')!='read' or record.get('platform_write') is not False):
        raise ValueError('replacement_complete_recorded_facts_required')
    facts=[e['facts'] for e in scope['sku_facts'] if e['facts']['item']==ITEM]
    wanted=ORDINARY|CUSTOM|{OLD}
    if (len(facts)!=len(wanted) or {r['sku'] for r in facts}!=wanted
            or not (wanted-{OLD}).issubset(set(record.get('requested_skus',[])))):
        raise ValueError('replacement_exact_full_sku_scope_required')
    active=[]
    for fact in facts:
        matches=[r for r in record['rows'] if row_matches(fact,r)]
        if len(matches)!=1:raise ValueError('replacement_export_dom_identity_conflict')
        enabled=switch_state(matches[0])
        if enabled is not (fact['sku']!=OLD):raise ValueError('replacement_published_switches_changed')
        if enabled:active.append(fact['sku'])
    return set(active)


def recorded_batch(source):
    import json,sqlite3
    from pathlib import Path
    from campaign_entry_authority import load,file_sha
    from campaign_continuous_policy import fingerprint
    path=source.WA/READ_JOB/'sku-batch-read.json';batch=load(path)
    with sqlite3.connect((source.WA/'jobs.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT operation,state,result,request FROM campaign_transfer_jobs WHERE id=?',(READ_JOB,)).fetchone()
    if not row or row[:2]!=('product_final_audit_read','finished'):
        raise ValueError('replacement_recorded_job_not_finished')
    result,request=json.loads(row[2]),json.loads(row[3]);payload=request['payload']
    if (fingerprint(['product_final_audit_read',payload])!=READ_JOB
            or fingerprint(payload)!=request['request_sha'] or batch['source']!=payload
            or payload['parent_request_id']!=READ_PARENT
            or {k:v for k,v in result.items() if k!='evidence_path'}!=batch
            or Path(result['evidence_path']).resolve()!=path.resolve()
            or batch.get('state')!='batch_read_complete' or batch.get('platform_write') is not False
            or batch.get('error') or batch.get('unread_items')):
        raise ValueError('replacement_recorded_job_evidence_changed')
    rec=batch['recording'];video=Path(rec['video']).resolve(strict=True)
    if (not video.is_relative_to(path.parent.resolve()) or not rec.get('frames')
            or rec.get('active') is not False or file_sha(video)!=rec['video_sha256']):
        raise ValueError('replacement_recording_not_verified')
    return path,batch


def apply(transport,scope,payload,folder):
    from campaign_continuous_transport import persist
    from campaign_entry_authority import load,file_sha
    from campaign_failure_remediation import mapped_erp_rows
    from campaign_product_scope import from_edge_job
    import campaign_verified_failure_resubmit as source
    from decimal import Decimal
    value=transport.request['existing_custom_replacement']
    validate(value,transport.request.get('authorized_item_scope'))
    # Validate the original full official export and fixed recorded batch. No
    # new read/download and no use of stock as an ON/OFF signal.
    original=load(source.SCOPE)
    observation=load(source.SCOPE.parent/'product-export-observation.json')
    verified=from_edge_job(observation,
        expected_request_id='2f24afabf910c86f9be065882c2a108b3bc4cbe3bc0c6b9e65d4527eaf4de9a3',
        expected_shop='畔色木作',roots=[source.WA])
    if verified!=original:raise ValueError('replacement_official_export_changed')
    read_path,batch=recorded_batch(source)
    records=[r for r in batch['records'] if r['item']==ITEM]
    if len(records)!=1:raise ValueError('replacement_record_not_unique')
    active=select_active(original,records[0])
    oldfacts=[e for e in original['sku_facts'] if e['facts']['item']==ITEM]
    current=[e for e in scope['sku_facts'] if e['facts']['item']==ITEM]
    if current not in (oldfacts,[e for e in oldfacts if e['facts']['sku'] in active]):
        raise ValueError('replacement_current_export_differs_from_recording')
    snapshot=load(transport.root/'resolved-snapshot.json')
    mapped=mapped_erp_rows(snapshot)
    for sku in active:
        matches=[r for r in mapped if str(r.get('item'))==ITEM and sku in
                 {str(r.get('sku')),*map(str,r.get('alt') or [])}]
        if len(matches)!=1 or matches[0].get('custom') is not (sku in CUSTOM):
            raise ValueError('replacement_erp_mapping_not_unique')
        if sku==NEW and (matches[0]['code']!='PPS2441004051399' or Decimal(str(matches[0]['daily']))!=Decimal('2000')):
            raise ValueError('replacement_new_custom_daily_changed')
    identity=payload['identity'];campaign='/'.join(str(identity[k]) for k in ('campaign_id','phase_id','sign_record_id'))
    if campaign not in (SUPER,AUTUMN):raise ValueError('replacement_campaign_changed')
    protected=transport.authority.blocked(campaign,'signup',identity['start'],identity['end'])
    latest=transport.authority.db.execute("SELECT id,status FROM attempts WHERE item=? AND campaign=? AND phase='signup' ORDER BY rowid DESC LIMIT 1",(ITEM,campaign)).fetchone()
    if ITEM not in protected and ((campaign==SUPER and (not latest or tuple(latest)!=(LAST_FAILED,'failed')))
            or (campaign==AUTUMN and latest is not None)):
        raise ValueError('replacement_original_attempt_changed')
    proof=persist(folder/'existing-custom-replacement.json',dict(authorization=value,
        source_export={'path':str(source.SCOPE),'sha256':file_sha(source.SCOPE)},
        recorded_state={'path':str(read_path),'sha256':file_sha(read_path)},
        old_off_sku=OLD,new_on_sku=NEW,active_skus=sorted(active),
        campaign=campaign,previous_attempt=list(latest) if latest else None,
        protected_prior=protected.get(ITEM),
        original_authority_unchanged=True,price_changed=False,stock_changed=False,rotation_performed=False))
    result=deepcopy(scope)
    result['sku_facts']=[e for e in result['sku_facts'] if e['facts']['item']!=ITEM or e['facts']['sku'] in active]
    result.update(prior_outcomes={ITEM:protected[ITEM]} if ITEM in protected else {},
                  prior_outcomes_evidence=proof,changed_existing_sku_scope=True)
    return result
