"""Build a submission checklist from existing pinned ERP/export evidence only.

No network access, new export, claim mutation or fallback prices. The normal
entry verifier still replays the original price/baseline and template checks.
"""
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from campaign_entry_authority import file_sha, load
from campaign_price_snapshot import digest

VERSION = 'submission-checklist-20261012-v2'


def read_scope(reference):
    if not reference or file_sha(reference['path']) != reference['sha256']:
        raise ValueError('checklist_scope_source_missing_or_changed')
    scope = load(reference['path'])
    return validate_scope(scope)


def validate_scope(scope):
    source = scope['page_evidence']
    if file_sha(source['path']) != source['sha256']:
        raise ValueError('checklist_original_export_receipt_changed')
    evidence = load(source['path'])
    data = evidence['result'] if source.get('operation') == 'campaign_atomic' else evidence
    from campaign_product_scope import complete_scope
    actual = complete_scope(
        [(Path(f['path']).read_bytes(), f['sha256']) for f in data['files']],
        observed_item_ids=data['observed_item_ids'], observed_page_count=data['page_count'],
        observed_total=data['observed_total'], page_evidence=source)
    if actual != scope:
        raise ValueError('checklist_scope_not_original_export')
    return scope


def materialize(body, snapshot, bases):
    """Return contract plus product-scoped issues; global source errors raise."""
    scope = read_scope(body.get('checklist_scope_source'))
    if digest(snapshot['all_erp_rows']) != body['price_version']:
        raise ValueError('checklist_price_version_changed')
    facts = {(e['facts']['item'], e['facts']['sku']): e['facts'] for e in scope['sku_facts']}
    from campaign_failure_remediation import mapped_erp_rows, excluded_pairs, verified_code_aliases
    from campaign_catalog_repair import excluded_pairs as catalog_excluded
    from campaign_official_template import template_rows
    excluded = excluded_pairs(body.get('sku_exclusion_receipts', [])) if body.get('sku_exclusion_receipts') else set()
    # Reuse the established official failed-product replacement evidence. A
    # refreshed product must not be compared to its superseded original SKUs.
    for ref in body.get('sku_exclusion_receipts', []):
        doc=load(ref['path'])  # Hash and original batch already checked above.
        fresh=validate_scope(doc['scope'])
        affected={r['item'] for r in doc['excluded']}
        facts={p:f for p,f in facts.items() if p[0] not in affected}
        facts.update({(e['facts']['item'],e['facts']['sku']):e['facts']
                      for e in fresh['sku_facts'] if e['facts']['item'] in affected})
    excluded |= catalog_excluded(snapshot, template_rows(Path(body['template_path']).read_bytes()))
    erp = defaultdict(list)
    aliases = verified_code_aliases(snapshot)
    for r in mapped_erp_rows(snapshot):
        erp[r['code']].append(r)
    selected = {r['item'] for r in body['signup_rows']}
    expected = {p for p in facts if p[0] in selected and p not in excluded}
    actual = {(r['item'],r['sku']) for r in body['signup_rows']}
    issues = [dict(item=i,sku=s,error='checklist_current_sku_scope_mismatch') for i,s in sorted(expected ^ actual)]
    finals = {(r['item'],r['sku']):r['final'] for r in body['planned_discount_rows']}
    finals.update({(r['item'],r['sku']):r['final'] for r in body.get('discount_reuse',[]) if not r.get('custom')})
    from campaign_generate_current_files import official_cut
    from campaign_official_template import discount_rate
    rate = discount_rate(body['official_rate'])
    rows, row_evidence = [], []
    for r in body['signup_rows']:
        pair = r['item'],r['sku']
        f = facts.get(pair)
        matches = erp[r['erp_code']]
        try:
            if f is None: raise ValueError('checklist_sku_missing_from_export')
            if len(matches) != 1: raise ValueError('checklist_erp_code_not_unique')
            original = matches[0]
            if (f['sku_code'] and f['sku_code'] != r['erp_code'] and
                    (*pair,r['erp_code'],f['sku_code']) not in aliases):
                raise ValueError('checklist_export_erp_code_mismatch')
            if pair[0] not in {str(original.get('item')),str(original.get('product_item_id')),
                               *map(str,original.get('product_alt_item_ids') or [])}:
                raise ValueError('checklist_erp_product_mismatch')
            if not f['attributes'].strip(): raise ValueError('checklist_sku_spec_missing')
            stock = Decimal(str(f['stock']))
            if not stock.is_finite() or stock < 0 or stock != stock.to_integral_value():
                raise ValueError('checklist_stock_invalid')
            if stock == 0: raise ValueError('checklist_zero_stock_rule_unknown')
            out = dict(item=pair[0],sku=pair[1],erp_code=r['erp_code'],
                product_name=f['attributes'],stock=str(stock),activity_price=r['activity_price'],
                custom=r['custom'],route='keep')
            if r['custom']:
                basis = bases.get(pair)
                if not basis or basis.get('uncertain'): raise ValueError('checklist_fixed_original_missing_or_uncertain')
                base = Decimal(str(basis['original']))
                if base <= 0 or Decimal(r['activity_price']) < base * Decimal('.20'):
                    raise ValueError('checklist_custom_floor_breached')
                out.update(original_price=str(base), target_price=str(base))
            else:
                daily = Decimal(str(original['daily']))
                target = Decimal(str(original[body['target']+'_target']))
                final = Decimal(finals.get(pair, str(daily-official_cut(daily,rate))))
                if Decimal(r['activity_price']) != daily: raise ValueError('checklist_signup_not_daily')
                if Decimal(r['target']) != target: raise ValueError('checklist_fixed_target_changed')
                if abs(final-target) > 2: raise ValueError('checklist_final_outside_fixed_target')
                out.update(daily_price=str(daily),target_price=str(target),final_price=str(final))
            rows.append(out)
            row_evidence.append(dict(item=pair[0],sku=pair[1],attributes=f['attributes'],
                baseline=bases.get(pair) if r['custom'] else None))
        except (ValueError, KeyError, ArithmeticError) as exc:
            issues.append(dict(item=pair[0],sku=pair[1],error=str(exc)))
    current = defaultdict(list)
    for i,s in sorted(expected):current[i].append(s)
    campaign = body['campaign'].split('/')
    identity = dict(zip(('campaign_id','phase_id','sign_record_id'),campaign))
    identity.update(start=body['start'],end=body['end'])
    export = load(scope['page_evidence']['path'])
    shop = ((export.get('identity') or {}).get('shop_name') if scope['page_evidence'].get('operation') == 'campaign_atomic'
            else export.get('shop_name'))
    if not shop:raise ValueError('checklist_export_shop_missing')
    identity['shop_name']=shop
    checklist = dict(identity=identity,
        template=dict(sheet='商品SKU导入列表',sku_dimension=True,includes_reference=True,smart_price=False),
        template_source=dict(path=body['template_path'],sha256=body['template_sha256']),
        current_sku_scope=dict(current),old_sku_scope={},protected_scope={},
        rows=rows,failed_feedback=[],
        source_evidence=dict(scope=body['checklist_scope_source'],snapshot=dict(path=body['snapshot_path'],sha256=body['snapshot_sha256']),
            price_version=body['price_version'],rows=row_evidence,
            official_exclusions=body.get('sku_exclusion_receipts',[]),
            price_correction_sources=body.get('corrections',{'rows':[]})),rule_version=VERSION)
    return checklist, issues


def verify_contract(body, snapshot, bases):
    contract, issues = materialize(body,snapshot,bases)
    if issues:raise ValueError('submission_checklist_inputs_invalid:'+issues[0]['error'])
    if body.get('submission_checklist') != contract:
        raise ValueError('submission_checklist_source_binding_changed')
    return contract
