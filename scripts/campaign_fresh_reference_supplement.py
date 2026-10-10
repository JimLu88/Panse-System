"""Explicit same-campaign reference supplement, preserving original failures."""
from copy import deepcopy
from pathlib import Path
import hashlib
from campaign_entry_authority import load
from campaign_official_template import read_rows
from campaign_official_failure_report import amount


def supplement(terminal, body, descriptor):
    path=Path(descriptor['receipt_path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=descriptor['receipt_sha256']:
        raise ValueError('fresh_reference_receipt_changed')
    receipt=load(path); identity=receipt['identity']; source=receipt['result']
    key='/'.join(identity[k] for k in ('campaign_id','phase_id','sign_record_id'))
    if (receipt['state']!='succeeded' or receipt['tool_id']!='activity_template'
            or key!=body['campaign'] or identity['start']!=body['start'] or identity['end']!=body['end']
            or source['source']!='official_current_template_download' or source['business_write'] is not False):
        raise ValueError('fresh_reference_identity_invalid')
    raw=Path(source['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('fresh_reference_file_changed')
    rows=read_rows(raw,'商品SKU导入列表')
    labels=('商品ID','SKUID','最低标价','最低普惠券后价要求')
    headers=[(n,c) for n,c in rows.items() if all(v in c.values() for v in labels)]
    if len(headers)!=1:raise ValueError('fresh_reference_header_invalid')
    n,h=headers[0]
    if any(list(h.values()).count(v)!=1 for v in labels):raise ValueError('fresh_reference_header_duplicate')
    cols={v:next(k for k,x in h.items() if x==v) for v in labels}
    facts={};item=None
    for row,c in sorted(rows.items()):
        if row<=n:continue
        value=c.get(cols['商品ID'],'')
        if value.isdigit():item=value
        elif value:item=None
        sku=c.get(cols['SKUID'],'')
        if item and sku.isdigit():
            pair=item,sku
            if pair in facts:raise ValueError('fresh_reference_duplicate_sku')
            facts[pair]=(amount(c[cols['最低标价']]),amount(c[cols['最低普惠券后价要求']]),row)
    if {i for i,s in facts}!=set(source['items']):raise ValueError('fresh_reference_scope_mismatch')
    submitted={(r['item'],r['sku']):r for r in body['signup_rows']}
    errors=deepcopy(terminal['errors']);selected={}
    for e in errors:
        if (e.get('kind')=='unknown' and e.get('parse_issue')=='unparsed_or_incomplete_official_failure'
                and e['item'] in descriptor['items'] and e.get('terminal')=='failed'
                and e.get('official_evidence') and '活动价格由卖家' in e['message']
                and ('您的sku：' in e['message'] or '您的以下sku' in e['message'])):
            selected.setdefault(e['item'],[]).append(e)
    for item,unknown in list(selected.items()):
        scope={pair for pair in submitted if pair[0]==item}
        if (not scope or not scope.issubset(facts) or any(e['item']==item and e.get('kind')=='unknown'
                and e not in unknown for e in errors)):
            del selected[item]
    revised=[e for e in errors if not (e['item'] in selected and e in selected[e['item']])]
    for item,unknown in selected.items():
        base=unknown[0]
        for pair,row in submitted.items():
            if pair[0]!=item:continue
            listed,coupon,line=facts[pair]
            proof=dict(path=source['path'],sha256=source['sha256'],row=line,
                receipt_sha256=descriptor['receipt_sha256'],source='fresh_official_price_reference',
                original_errors=unknown,original_message_preserved=True)
            for kind,cap in [('list_price',listed),('coupon_price',coupon)]:
                revised.append(dict(item=item,sku=pair[1],batch=base['batch'],terminal='failed',
                    message=base['message'],official_evidence=base['official_evidence'],kind=kind,
                    submitted_price=row['activity_price'],official_cap=cap,supplemental_reference=True,
                    supplemental_evidence=proof,product_level_requirement=True))
    return dict(terminal,errors=revised)


def supplement_registered(terminal,body,root):
    path=Path(root)/'fresh-reference-supplement.json'
    return supplement(terminal,body,load(path)) if path.exists() else terminal
