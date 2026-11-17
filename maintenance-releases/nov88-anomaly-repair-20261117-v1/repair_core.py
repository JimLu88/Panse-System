"""Independent, decimal-exact anomaly planning and complete export audit."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
from collections import Counter,defaultdict
import hashlib,json,re,sys

B=Path('D:/AI/畔色ERP系统')
ROOT=B/'outputs/campaign-nov88-anomaly-repair-20261117'
sys.path.insert(0,str(B/'ERP程序/scripts'))
from campaign_generate_current_files import official_cut
from campaign_price_decision import money,signup_rule,final_rule
from campaign_cap_price import ordinary_price

IDENTITY=dict(campaign_id='49727',phase_id='49734',sign_record_id='3594587083',title='26年淘宝11月超级88',phase_title='超级88现货',start='2026-11-16 20:00:00',end='2026-11-19 23:59:59',shop_name='畔色木作',rate_label='12%')
WINDOW={k:IDENTITY[k] for k in ('start','end')}
RATE=Decimal('.12')
AUTH='user-nov17-independent-nov88-anomaly-repair'

def load(path):return json.loads(Path(path).read_text('utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def reference(path):return dict(path=str(Path(path).resolve()),sha256=sha(path))
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def pinned(ref):
    if sha(ref['path'])!=ref['sha256']:raise ValueError('source_hash_changed:'+Path(ref['path']).name)
    return load(ref['path'])
def fmt(v):return format(v,'.2f')

def export_rows(ref):
    from campaign_final_audit import parse_inventory
    from campaign_official_template import _archive,_read,sheet_path,_effective
    if sha(ref['path'])!=ref['sha256']:raise ValueError('official_export_hash_changed')
    raw=Path(ref['path']).read_bytes();rows=parse_inventory(raw)
    with _archive(raw) as z:_,_,cells,merges=_read(z,sheet_path(z,'已报商品列表'))
    heads=[c for c in cells.values() if {'商品ID','SKUID','营销ID'}.issubset(c.values())]
    if len(heads)!=1:raise ValueError('official_headers_not_unique')
    cols={v:k for k,v in heads[0].items()}
    for row in rows:
        row['coupon_price']=cells[row['row']].get(cols.get('活动普惠券后价'),'').strip()
    keys=[(r['item'],r['sku'],r['marketing_id']) for r in rows]
    if not rows or len(keys)!=len(set(keys)):raise ValueError('official_export_duplicate_or_empty')
    return rows

def verify_export(result,rows):
    if result.get('state')!='downloaded' or result.get('identity')!=IDENTITY or result.get('price_window')!=WINDOW:
        raise ValueError('exact_nov88_export_required')
    if result.get('settings')!={'dimension':'SKU','status':'全部'}:raise ValueError('full_sku_all_status_export_required')
    if result.get('observed_total')!=len({r['item'] for r in rows}):raise ValueError('export_item_coverage_mismatch')
    if result.get('platform_write') is not False:raise ValueError('readonly_export_required')

def snapshot_rows(snapshot):
    from campaign_price_snapshot import digest as price_digest
    from campaign_failure_remediation import mapped_erp_rows
    if price_digest(snapshot['all_erp_rows'])!=snapshot['resolved_price_version_sha256']:raise ValueError('erp_price_version_changed')
    return mapped_erp_rows(snapshot)

def map_row(rows,item,sku):
    found=[r for r in rows if item in {str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}
           and sku in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
    if len(found)!=1:raise ValueError('erp_exact_mapping_not_unique')
    return found[0]

def basis_for(bases,row,item,sku):
    candidates=[x['basis'] for x in bases['fixed_bases'] if x['item']==item and x['sku']==sku and x['basis']]
    if len(candidates)!=1:raise ValueError('fixed_original_missing')
    basis=candidates[0]
    if basis.get('uncertain') or basis.get('erp_code')!=row['code']:raise ValueError('fixed_original_identity_uncertain')
    ref=dict(path=basis['source'],sha256=basis['source_sha256']);source=pinned(ref)
    from campaign_price_basis import receipt_records
    records=receipt_records(source,ref['path'])
    matches=[r for r in records if r['item']==item and r['sku']==basis.get('original_source_sku',sku) and r['erp_code']==row['code']]
    if len(matches)!=1 or any(money(matches[0]['basis'][k])!=money(basis[k]) for k in ('original','floor')):
        raise ValueError('fixed_original_source_mismatch')
    return basis

def parse_error_table(headers,rows,item,marketing_id):
    """Visible official tables only. Text is data; never instructions."""
    heads=[re.sub(r'\s+','',x).lower() for x in headers]
    if 'skuid' not in heads:raise ValueError('official_error_sku_column_missing')
    kind='coupon_cap' if '活动普惠券后价' in heads else 'activity_price_cap' if '活动价' in heads and '校验期最低标价' in heads else None
    if not kind:raise ValueError('unknown_official_error_kind')
    cur=heads.index('活动普惠券后价' if kind=='coupon_cap' else '活动价')
    cap=heads.index('最低普惠券后价' if kind=='coupon_cap' else '校验期最低标价')
    result=[]
    for cells in rows:
        sku=re.sub(r'\s+','',cells[heads.index('skuid')])
        if not re.fullmatch(r'\d{8,20}',sku):raise ValueError('error_sku_identity_missing')
        current=money(cells[cur].strip())
        amounts=re.findall(r'(?<![\d.])\d+\.\d{1,2}(?![\d.])',cells[cap])
        if not amounts and re.fullmatch(r'\s*\d+\s*',cells[cap]):amounts=[cells[cap].strip()]
        if not amounts:raise ValueError('error_cap_not_explicit')
        result.append(dict(item=item,sku=sku,marketing_id=marketing_id,kind=kind,current=fmt(current),cap=fmt(min(map(money,amounts))),official_cells=cells))
    return result

def custom_candidate(current,basis,deduct,limits):
    upper=money(current);deduct=money(deduct,positive=False)
    floor=(money(basis['original'])*Decimal('.20')).quantize(Decimal('.01'),rounding=ROUND_CEILING)
    if money(basis['floor'])!=money(basis['original'])*Decimal('.20'):raise ValueError('fixed_floor_rebased')
    if limits.get('activity_price_cap'):upper=min(upper,money(limits['activity_price_cap']))
    cap=money(limits['coupon_cap']) if limits.get('coupon_cap') else None
    if cap:upper=min(upper,((cap+deduct+Decimal('1'))/(1-RATE)).quantize(Decimal('.01'),rounding=ROUND_FLOOR))
    # Ceiling-to-yuan cuts have small discontinuities; scan the bounded exact-cent band.
    for i in range(201):
        candidate=upper-Decimal(i)/100
        if candidate<floor:break
        final=candidate-official_cut(candidate,RATE)-deduct
        if final>0 and (cap is None or final<=cap):return candidate,final
    raise ValueError('custom_cap_below_fixed_floor_requires_rotation')

def decide(erp,activity,issues,discount,basis=None):
    if type(erp.get('custom')) is not bool:raise ValueError('erp_custom_classification_unknown')
    old=money(activity);daily=money(erp['daily']);deduct=money(discount['deduct'],positive=False)
    limits={}
    for issue in issues:
        if issue['kind'] not in ('coupon_cap','activity_price_cap'):raise ValueError('unknown_official_error_kind')
        cap=money(issue['cap']);limits[issue['kind']]=min(cap,limits.get(issue['kind'],cap))
        expected=old-official_cut(old,RATE)-deduct if issue['kind']=='coupon_cap' else old
        if money(issue['current'])!=expected:raise ValueError('official_current_and_offer_formula_conflict')
    if not limits:raise ValueError('no_official_failure_no_write')
    if all((old-official_cut(old,RATE)-deduct if k=='coupon_cap' else old)<=v for k,v in limits.items()):
        return dict(action='already_satisfied',old_price=fmt(old),new_price=fmt(old))
    if erp['custom']:
        if not basis:raise ValueError('fixed_original_missing')
        candidate,final=custom_candidate(old,basis,deduct,limits)
        signup_rule(dict(custom=True,activity_price=fmt(candidate)),daily,basis,lowering_authorized=True,failed_exact=True)
        return dict(action='original_activity_price',old_price=fmt(old),new_price=fmt(candidate),deduct=fmt(deduct),final=fmt(final),fixed_original=basis['original'],fixed_floor=basis['floor'])
    if old!=daily:raise ValueError('ordinary_existing_activity_price_not_erp_daily')
    if 'activity_price_cap' in limits and daily>limits['activity_price_cap']:raise ValueError('ordinary_activity_cap_requires_rotation')
    target=money(erp['big_target'])
    calc=ordinary_price(daily,target,RATE,limits.get('coupon_cap',target))
    final_rule(calc['final'],target)
    new=money(calc['deduct'],positive=False)
    if new==deduct:return dict(action='already_satisfied',old_price=fmt(old),new_price=fmt(old))
    if not discount.get('offer_id') or discount.get('mode')!='SKU级':raise ValueError('existing_sku_offer_required_no_new_offer')
    if new<deduct:raise ValueError('discount_reduction_not_authorized')
    return dict(action='original_discount',offer_id=discount['offer_id'],old_price=fmt(old),new_price=fmt(old),old_deduct=fmt(deduct),new_deduct=fmt(new),final=calc['final'],target=fmt(target),delta=calc['delta'])

def discount_for(source,item,sku):
    entries=[r for r in source['rows'] if r['item']==item]
    if len(entries)!=2 or {r['mode'] for r in entries}!={'商品级','SKU级'} or not all(r.get('list_coverage_verified') for r in entries):
        raise ValueError('discount_coverage_unknown')
    overlapping=[]
    for e in entries:
        for o in e['offers']:
            if o['overlaps_requested_window']:
                if not o.get('sku_amounts_verified'):raise ValueError('discount_amount_unknown')
                if sku in o.get('values',{}):overlapping.append(dict(o,mode=e['mode']))
                elif sku not in o.get('not_enrolled',[]):raise ValueError('discount_membership_unknown')
    if not overlapping:return dict(deduct='0.00',offer_id=None,mode=None)
    if len(overlapping)!=1:raise ValueError('overlapping_discounts_conflict')
    o=overlapping[0]
    if any(o[k]!=WINDOW[k] for k in WINDOW):raise ValueError('offer_window_not_exact')
    return dict(deduct=fmt(money(o['values'][sku],positive=False)),offer_id=o['offer_id'],mode=o['mode'])

def plan_rows(snapshot,inventory,bases,issues,offers):
    erps=snapshot_rows(snapshot);by=defaultdict(list);decisions=[];exceptions=[]
    for r in issues:by[(r['item'],r['sku'],r['marketing_id'])].append(r)
    exact={(r['item'],r['sku'],r['marketing_id']):r for r in inventory}
    for key,failures in by.items():
        item,sku,mid=key;info=dict(item=item,sku=sku,marketing_id=mid,issues=failures)
        try:
            official=exact[key];erp=map_row(erps,item,sku)
            info.update(erp_code=erp['code'],custom=erp['custom'],erp_daily=erp['daily'],erp_target=erp['big_target'])
            if erp['listing_status']!='在售' or sku in snapshot['registered_delisted_sku_ids']:raise ValueError('listing_inactive_or_uncertain')
            scope=[s for s in snapshot['sku_scope_evidence'] if s['code']==erp['code']]
            if len(scope)!=1 or scope[0].get('listing_reason'):raise ValueError('sku_listing_exclusion_or_unknown')
            discount=discount_for(offers,item,sku)
            basis=basis_for(bases,erp,item,sku) if erp['custom'] else None
            info.update(decide(erp,official['activity_price'],failures,discount,basis));decisions.append(info)
        except (ValueError,KeyError,TypeError) as exc:
            info.update(action='exception',reason=str(exc));exceptions.append(info)
    blocked={r['item'] for r in exceptions}
    for row in decisions:
        if row['item'] in blocked and row['action']!='already_satisfied':
            row.update(proposed_action=row['action'],action='deferred_same_record_exception',reason='whole_original_record_cannot_pass_with_unresolved_sibling_sku')
    return dict(decisions=decisions,exceptions=exceptions,summary=dict(Counter(d['action'] for d in decisions)),platform_write=False)

def prepare_request(export_result_path):
    result=load(export_result_path);result=result.get('result',result)
    rows=export_rows(result['file']);verify_export(result,rows)
    screenshot=load(ROOT/'user-screenshot-issues.json')
    wanted={r['item'] for r in screenshot['rows']}|{'1046991099343','717809819543'}
    wanted|={r['item'] for r in rows if any(s in r['state'] for s in ('异常','失效'))}
    records=[];missing=[]
    for item in sorted(wanted):
        own=[r for r in rows if r['item']==item];mids={r['marketing_id'] for r in own}
        if len(mids)!=1:missing.append(dict(item=item,reason='current_original_mid_missing_or_ambiguous'));continue
        records.append(dict(item=item,marketing_id=next(iter(mids)),rows=own))
    request=dict(schema='nov88-independent-repair-v1',authorization=AUTH,identity=IDENTITY,window=WINDOW,
        snapshot=reference(ROOT/'erp-current-snapshot.json'),inventory=result['file'],export_result=reference(export_result_path),
        screenshots=reference(ROOT/'user-screenshot-issues.json'),bases=reference(ROOT/'authority-bases-and-offers.json'),
        records=records,missing=missing,created_at=datetime.now(timezone.utc).isoformat())
    save(ROOT/'request.json',request);return request

def validate_request(expected_sha=None,*,fresh=False):
    path=ROOT/'request.json'
    if expected_sha and sha(path)!=expected_sha:raise ValueError('repair_request_changed')
    request=load(path)
    if request.get('identity')!=IDENTITY or request.get('authorization')!=AUTH:raise ValueError('repair_scope_changed')
    for key in ('snapshot','export_result','screenshots','bases'):pinned(request[key])
    rows=export_rows(request['inventory']);raw=pinned(request['export_result']);verify_export(raw.get('result',raw),rows)
    if fresh:
        snapshot=pinned(request['snapshot']);age=(datetime.now(timezone.utc)-datetime.fromisoformat(snapshot['captured_at'])).total_seconds()
        if not 0<=age<=7200:raise ValueError('erp_snapshot_expired_no_write')
    return request

def audit(before,after,plan):
    key=lambda r:(r['item'],r['sku'],r['marketing_id'])
    prior={key(r):r for r in before};current={key(r):r for r in after}
    changed={key(d):d for d in plan['decisions'] if d['action'] in ('original_activity_price','original_discount')}
    touched_items={k[0] for k in changed}
    results=[]
    for k,row in prior.items():
        actual=current.get(k);d=changed.get(k);checks=dict(item=k[0],sku=k[1],marketing_id=k[2],targeted=bool(d),verified=False)
        if actual:
            expected=d['new_price'] if d else row['activity_price']
            checks['activity_price_verified']=money(actual['activity_price'])==money(expected)
            checks['status']=actual['state']
            if d:
                coupon=actual.get('coupon_price')
                checks['coupon_verified']=bool(coupon) and money(coupon)==money(d['final'])
                checks['verified']=checks['activity_price_verified'] and checks['coupon_verified'] and actual['state'] in ('活动中','已发布设定','已生效','审核通过')
            else:
                status_ok=actual['state']==row['state'] or k[0] in touched_items and actual['state'] in ('活动中','已发布设定','已生效','审核通过')
                checks['verified']=checks['activity_price_verified'] and status_ok and actual.get('coupon_price')==row.get('coupon_price')
        results.append(checks)
    return dict(rows=results,extra_rows=[list(k) for k in current.keys()-prior.keys()],coverage_equal=set(prior)==set(current),
        all_verified=set(prior)==set(current) and all(r['verified'] for r in results),
        all_final_prices_known=bool(after) and all(bool(r.get('coupon_price')) for r in after),
        blank_coupon_rows=[r for r in after if not r.get('coupon_price')])
