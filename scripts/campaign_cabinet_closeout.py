"""Exact authorized cabinet publication -> fresh export -> mapping -> enrollment.

Called inside the existing continuous controller, never by AI page actions.
Existing successful/unknown activities stay protected. No ERP price edits.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from campaign_entry_authority import load,file_sha
from campaign_continuous_transport import persist

ITEM='793052650673'
AUTH='user-20260915-three-decisions-approved'
AUTUMN='49557/49560/3538210379'
LAST='9a8937e16632498895d5d00f3f91c890:'+ITEM
PAIRS=(('6112006437836','PPS2435001041011','6134178749284','PPS2435001041011B1','6285'),
       ('6274590435214','PPS2435001041012','6134178749285','PPS2435001041012B1','6667.50'))


def validate(request):
    from campaign_approved_shipping import SOURCE,SHA
    if (request.get('approved_cabinet_rotation')!=AUTH
            or request.get('authorized_item_scope',{}).get('items')!=[ITEM]
            or request.get('existing_product_export') or request.get('existing_custom_replacement')
            or AUTUMN not in request.get('pages',{}) or file_sha(SOURCE)!=SHA):
        raise ValueError('exact_current_cabinet_authorization_required')


def prepare(transport,folder):
    validate(transport.request)
    latest=transport.authority.db.execute("SELECT id,status FROM attempts WHERE item=? AND campaign=? AND phase='signup' ORDER BY rowid DESC LIMIT 1",(ITEM,AUTUMN)).fetchone()
    if not latest or tuple(latest)!=(LAST,'failed'):
        raise ValueError('cabinet_original_failed_attempt_changed')
    page=transport.request['pages'][AUTUMN]
    job=transport.job('existing_reserve_publish',{'identity':transport.identity({'identity':page}),
        'authorization':AUTH},folder/'cabinet-publication')
    result=job.get('result') or {}
    if result.get('state')!='published_verified' or result.get('authorization')!=AUTH or result.get('item')!=ITEM:
        raise ValueError('cabinet_publication_not_verified_no_signup')
    rec=result['recording']
    if not rec.get('frames') or rec.get('active') is not False or file_sha(rec['video'])!=rec.get('video_sha256'):
        raise ValueError('cabinet_publication_recording_not_verified')
    persist(transport.root/'cabinet-publication.json',job)
    return job


def bind_export(transport,scope,snapshot,folder):
    """New official export independently verifies published physical identities."""
    from campaign_recorded_sku_state import row_matches
    from campaign_verified_failure_resubmit import switch_state
    path=transport.root/'cabinet-publication.json';job=load(path);result=job['result']
    if job.get('state')!='finished' or result.get('state')!='published_verified' or result.get('authorization')!=AUTH:
        raise ValueError('cabinet_publish_receipt_required')
    facts=[e['facts'] for e in scope['sku_facts'] if e['facts']['item']==ITEM]
    if len(facts)!=7 or len(result.get('after_rows',[]))!=7:
        raise ValueError('cabinet_complete_seven_sku_export_required')
    active=[];restored=[]
    for fact in facts:
        rows=[r for r in result['after_rows'] if row_matches(fact,r)]
        if len(rows)!=1:raise ValueError('cabinet_saved_export_identity_conflict')
        enabled=switch_state(rows[0])
        if enabled is None:raise ValueError('cabinet_saved_switch_unknown')
        if enabled:active.append(fact['sku'])
    old={p[0] for p in PAIRS};new={p[2] for p in PAIRS}
    if old.intersection(active) or not new.issubset(active) or len(active)!=5:
        raise ValueError('cabinet_active_scope_not_verified')
    for oldsku,code,newsku,newcode,daily in PAIRS:
        fact=next((r for r in facts if r['sku']==newsku),{})
        rows=[r for r in snapshot['all_erp_rows'] if r['code']==code and str(r.get('item'))==ITEM]
        if fact.get('sku_code')!=newcode or len(rows)!=1 or rows[0].get('custom') is not False or Decimal(str(rows[0]['daily']))!=Decimal(daily):
            raise ValueError('cabinet_erp_price_or_mapping_changed')
        restored.append(dict(item=ITEM,sku=newsku,erp_code=code,official_sku_code=newcode))
    doc=dict(status='verified_partial_mapping_restored',restored=restored,
        official_export_evidence=scope['page_evidence'],
        published_source={'path':str(path),'sha256':file_sha(path)},database_write=False,
        authorization=AUTH,active_skus=sorted(active),old_off_skus=sorted(old))
    ref=persist(folder/'cabinet-mapping.json',doc)
    transport.authority.register_source(ref,'mapping',file_sha(ref))
    persist(transport.root/'cabinet-active.json',doc)
    return transport.authority.resolve_snapshot(snapshot)


def apply(transport,scope,payload,folder):
    key='/'.join(str(payload['identity'][k]) for k in ('campaign_id','phase_id','sign_record_id'))
    proof=load(transport.root/'cabinet-active.json');active=set(proof['active_skus'])
    result=deepcopy(scope)
    result['sku_facts']=[e for e in result['sku_facts'] if e['facts']['item']!=ITEM or e['facts']['sku'] in active]
    p=payload['identity'];prior=transport.authority.blocked(key,'signup',p['start'],p['end'])
    if key==AUTUMN and ITEM not in prior:
        latest=transport.authority.db.execute("SELECT id,status FROM attempts WHERE item=? AND campaign=? AND phase='signup' ORDER BY rowid DESC LIMIT 1",(ITEM,key)).fetchone()
        if not latest or tuple(latest)!=(LAST,'failed'):raise ValueError('cabinet_failure_predecessor_changed')
    if payload.get('time_binding'):
        include_missing(transport,payload,folder)
    ref=persist(folder/'cabinet-prior.json',dict(campaign=key,protected=prior,
        changed_physical_scope=True,publication=proof['published_source'],claims_unchanged=True))
    return dict(result,prior_outcomes={ITEM:prior[ITEM]} if ITEM in prior else {},prior_outcomes_evidence=ref)


def include_missing(transport,payload,folder):
    """Only add the two newly published ordinary SKUs to a proven old offer."""
    import importlib.util
    from campaign_continuous_policy import fingerprint
    from campaign_discount_include import prepare as prepare_include,record
    segment=payload['time_binding']['segment'];window=segment['price_window']
    offers=[o for o in transport.authority.discount_offers()
        if (o['start'],o['end'])==(window['start'],window['end'])
        and any(r['item']==ITEM for r in o['items'])]
    if not offers:return  # Original generator owns creation if there is no prior offer.
    if len(offers)!=1:raise ValueError('cabinet_discount_parent_not_unique')
    offer=offers[0];new={p[2] for p in PAIRS}
    present={r['sku'] for r in offer['rows'] if r['item']==ITEM}
    if new.issubset(present):return  # Already recorded additions are never replayed.
    if new.intersection(present):raise ValueError('cabinet_partial_new_offer_requires_exact_reconciliation')
    identity=transport.identity(payload);oid=offer.get('platform_offer_id',offer['offer_id'])
    rid=fingerprint([AUTH,ITEM,window,'include-new-reserves'])
    coverage_job=transport.job('discount_item_discovery',dict(identity=identity,items=[ITEM],read_request_id=rid),folder/'offer-coverage')
    files=[r['evidence_path'] for r in coverage_job['result']['rows']]
    parser_path=Path('D:/AI/畔色ERP系统/Web-Agent程序/app/engine/campaign_discount_item_coverage.py')
    spec=importlib.util.spec_from_file_location('cabinet_offer_coverage',parser_path)
    parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
    coverage=persist(folder/'offer-coverage.json',parser.analyze(files,**window))
    absence=transport.job('discount_readback',dict(identity=identity,read_request_id=rid,price_window=window,
        offers=[dict(offer_id=oid,item=ITEM,sku_ids=sorted(new))]),folder/'missing-discount')
    absent=persist(folder/'missing-discount.json',absence)
    snapshot=transport.root/'resolved-snapshot.json'
    request=dict(schema='campaign_missing_discount_include_v1',campaign=segment['campaign'],shop=identity['shop_name'],
        start=window['start'],end=window['end'],rate=segment['official_rate'],offer_id=oid,
        snapshot={'path':str(snapshot),'sha256':file_sha(snapshot)},price_version=load(snapshot)['resolved_price_version_sha256'],
        coverage={'path':coverage,'sha256':file_sha(coverage)},missing_read={'path':absent,'sha256':file_sha(absent)})
    claim_path=folder/'include-claim.json'
    if claim_path.exists():claim=load(claim_path)
    else:claim=prepare_include(transport.authority,request);persist(claim_path,claim)
    if {(r['item'],r['sku']) for r in claim['rows']}!={(ITEM,s) for s in new}:
        raise ValueError('cabinet_discount_include_scope_changed')
    job=transport.job('discount_include',dict(identity=identity,claim_id=claim['claim_id']),folder/'include')
    ref=persist(folder/'include-terminal.json',job)
    record(transport.authority,claim['claim_id'],ref)
