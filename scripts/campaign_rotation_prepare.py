"""Prepare the Sep28 changed-SKU batch, never release/upload or change ERP.

Reuses the existing official package projector; current facts do not manufacture
activity eligibility, enabled switches or missing prices. All outputs conditional.
"""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from campaign_official_template import fill_selected_rows,fill_single_discount_rows,money
from campaign_fixed_template_projection import project
from campaign_generate_current_files import official_cut
from campaign_recovery_trial import MASTER,MASTER_SHA,CAMPAIGN,LEDGER,import_terminal
from campaign_discount_template import load_fixed_discount_template
from campaign_replacement_audit import load_current_protection

PROJECT=Path('D:/AI/畔色ERP系统')
OWNER=PROJECT/'outputs/01a067c6-7e83-7483-9a21-84b44ed7299b'
INPUTS={
 'mapping':(OWNER/'rotation-20260928/nine-sku-mapping.json','dfdd926ce39dc291ad82a6292945c6a2f0709d525d20f45433bdfb2d06816e22'),
 'scope':(OWNER/'rotation-20260928/verified-scope.json','2a1deea7a402be522d3af686fb088d919beac97a4739780bb22ac258631846fb'),
 'export':(OWNER/'rotation-20260928/export-latest.json','1f74b9f585b9d6db53c55269647db392ffa22822587d315d379cf80336b8072c'),
 'current':(OWNER/'super-all-actions-20260928/0403全场核对及整批交付.json','e7204494c67871c091d244d8e3d143c73158077668374eff8aaeb4c38c81bf2c'),
 'snapshot':(PROJECT/'outputs/custom-correspondence-20260927/03-post-correction-snapshot.json','76dfa053fa0170cc54d9bcdbcffe670d9e32dfc225f0251983c1d0c6559ca68c'),
 'custom':(OWNER/'super-all-actions-20260928/老款岩板桌2定制修正回执.json','f27478d4ee1ead76b6f5bda8911a2073532077653cab16c0738370978638002c'),
 'round':(OWNER/'super-all-actions-20260928/明早01-四条替换计算回执.json','2a67665367cb00de92e0ccb6e96f3afce053fbc3c31320a2e56a4405efad9e45'),
}
ITEMS={'1036273574687':('10030569742795',14),'792992319206':('10030565274059',7)}
VERSION='31f2515a700fa3a33f1ade50f569e4119e64376e3100c76a6cb4913aaf51385c'
OFFICIAL=(PROJECT/'outputs/campaign-super43-rejected-20260928/official-current-20260928-022929.xlsx',
          '262a29aafeae858021f0e273a8274eaf8e378a082e163742ea2dfebbf1bc0778')
OLD_BED_SUCCESS='d09bb22174af4f8cbc2d1f9309a81cc1:1036273574687'
OLD_BED_PROOF='c38c0914494c1c458eddca17281c10ed6488ccb95bb511f8204c1b43817985bd'


def digest(raw):return hashlib.sha256(raw).hexdigest()


def pinned(path,sha):
    raw=Path(path).read_bytes()
    if digest(raw)!=sha:raise ValueError('source_changed:'+str(path))
    return raw


def trial_protection(ledger=LEDGER):
    """Read all existing prepared/unknown/accepted holds; never reset old trials."""
    pairs=set();no_sales=set()
    if not Path(ledger).is_dir():raise ValueError('existing_trial_ledger_missing')
    for folder in Path(ledger).iterdir():
        if not folder.is_dir():continue
        if folder.name.endswith('.lock'):raise ValueError('trial_ledger_busy')
        reserved=json.loads((folder/'request.json').read_text(encoding='utf-8'))
        req=reserved['request']
        if req.get('campaign')!=CAMPAIGN:continue
        prepared=json.loads((folder/'prepared.json').read_text(encoding='utf-8'))
        if prepared['request']!=req:raise ValueError('trial_request_changed')
        events=sorted(folder.glob('event-*.json'))
        last=json.loads(events[-1].read_text(encoding='utf-8'))['result'] if events else prepared
        terminal=last.get('official_terminal')
        if terminal:
            parsed=import_terminal(req,pinned(terminal['source']['path'],terminal['source']['sha256']))
            if any(terminal.get(k)!=v for k,v in parsed.items()):raise ValueError('trial_terminal_changed')
            pairs.update(map(tuple,parsed['successful_pairs']+parsed['unknown_pairs']))
            no_sales.update(parsed['no_sales_items'])
        else:pairs.update((r['item'],r['sku']) for r in req['scope'])
    return pairs,no_sales


def current_protection():
    from campaign_entry_authority import Authority,STATE
    a=object.__new__(Authority)
    a.config=json.loads((PROJECT/'ERP程序/docs/campaign-entry-sources.json').read_text(encoding='utf-8-sig'))
    a.db=sqlite3.connect(STATE.as_uri()+'?mode=ro',uri=True);a.db.row_factory=sqlite3.Row
    historical=[]
    try:
        items=a.blocked(CAMPAIGN,'signup','2025-06-21 00:00:00','2028-07-31 23:59:59')
        rows=list(a.db.execute("SELECT * FROM attempts WHERE item=? AND campaign=? AND phase='signup' AND status IN ('success','unknown')",
                               ('1036273574687',CAMPAIGN)))
        if len(rows)==1 and rows[0]['id']==OLD_BED_SUCCESS and rows[0]['status']=='success':
            evidence=json.loads(rows[0]['evidence'])
            if evidence.get('sha256')==OLD_BED_PROOF:
                pinned(evidence['path'],OLD_BED_PROOF)
                historical=['1036273574687']
    finally:a.db.close()
    pairs,no_sales=trial_protection()
    from campaign_rotation_followup import registered_protection
    extra_pairs,extra_no_sales=registered_protection()
    pairs.update(extra_pairs);no_sales.update(extra_no_sales)
    return dict(signup_items=items,signup_pairs=pairs,no_sales_items=no_sales,historical_changed_scope_candidates=historical,
                discount_pairs=set(map(tuple,load_current_protection()['protected_pairs'])))


def exact_index(rows,keys):
    result={}
    for row in rows:
        key=tuple(str(row[k]) for k in keys)
        if key in result:raise ValueError('duplicate_source_identity')
        result[key]=row
    return result


def calculate(d,protection):
    scope,mapping,snapshot=d['scope'],d['mapping'],d['snapshot']
    if (not scope.get('complete') or mapping.get('schema')!='rotation_mapping_evidence_v1'
            or mapping.get('platform_write') is not False or mapping.get('erp_write') is not False
            or snapshot.get('resolved_price_version_sha256')!=VERSION):
        raise ValueError('rotation_frozen_source_contract_changed')
    facts=exact_index([e['facts'] for e in scope['sku_facts']],('item','sku'))
    old=exact_index(mapping['rows'],('item','old_sku'))
    new=exact_index(mapping['rows'],('item','new_sku'))
    if len(old)!=9 or len(new)!=9 or {i for i,s in old}!=set(ITEMS):raise ValueError('exact_nine_rotation_required')
    current=exact_index([r for r in d['current']['current_rows'] if r['item'] in ITEMS],('item','sku'))
    for item,(marketing,count) in ITEMS.items():
        rows=[r for (i,s),r in current.items() if i==item]
        if len(rows)!=count or any(r['marketing_id']!=marketing or r['state']!='异常' for r in rows):
            raise ValueError('original_full_item_marketing_scope_changed')
    custom=exact_index(d['custom']['fixed_basis'],('item','sku'))
    if set(custom)!={('792992319206','5602711422165'),('792992319206','6056644376634')}:
        raise ValueError('exact_two_custom_corrections_required')
    activity=[];discounts=[];conditions=[];held=[]
    for pair,r in current.items():
        item,sku=pair;replacement=old.get(pair);target_sku=replacement['new_sku'] if replacement else sku
        if pair not in facts or (item,target_sku) not in facts:raise ValueError('current_export_identity_missing')
        price=money(r['activity_price'])  # Never substitute G for absent P.
        if replacement:
            f=facts[(item,target_sku)]
            matched=[x for x in snapshot['all_erp_rows'] if x['code']==replacement['erp_code']
                     and sku in {str(x.get('sku')),*map(str,x.get('alt') or [])}]
            if (len(matched)!=1 or matched[0].get('custom') is not False
                    or f['sku_code']!=replacement['erp_code'] or f['attributes']!=replacement['new_attributes']
                    or facts[pair]['attributes']!=replacement['old_attributes']
                    or money(f['price'])!=money(replacement['list_price'])):
                raise ValueError('exact_old_new_erp_binding_changed')
            frozen=matched[0];goal=money(frozen['medium_target'])
            cut=official_cut(price,Decimal('.10'));deduct=price-cut-goal
            if (price!=money(frozen['daily']) or price!=money(replacement['activity_price'])
                    or goal!=money(replacement['target']) or goal<money(frozen['big_target'])
                    or deduct<0 or deduct!=money(replacement['deduct'])):
                raise ValueError('frozen_rotation_price_or_target_changed')
            if (item,target_sku) in protection['discount_pairs']:
                held.append(dict(item=item,sku=target_sku,phase='discount',reason='successful_or_unknown_no_replay'))
            else:discounts.append(dict(item=item,sku=target_sku,deduct=str(deduct),activity_price=str(price),target=str(goal)))
            conditions.append(dict(item=item,old_sku=sku,new_sku=target_sku,
                old_marketing_id=r['marketing_id'],old_offer=replacement['old_offer'],
                old_enabled='unknown',new_enabled='unknown',new_cap=None,
                scope_basis='authorized_old_to_new_plan_not_stock_or_disabled_proof'))
        if pair in custom:
            c=custom[pair];b=c['details']['basis'];candidate=money(c['new_price'])
            if (money(c['old_price'])!=price or c['marketing_id']!=r['marketing_id']
                    or c.get('custom') is not True or b.get('uncertain') is not False
                    or money(b['floor'])!=money(b['original'])*Decimal('.20')
                    or not money(b['floor'])<=candidate<=price):
                raise ValueError('custom_fixed_basis_or_price_changed')
            pinned(b['source'],b['source_sha256'])
            price=candidate
        activity.append(dict(item=item,sku=target_sku,activity_price=str(price),
                             original_sku=sku,original_marketing_id=r['marketing_id']))
    # A preserved old success is NOT rewritten as failed. Only the exact known
    # historical attempt plus a newer pinned original abnormal full-item export
    # permits a CONDITIONAL changed-SKU recovery plan. New trial/unknown holds
    # below are never exempted. This entry has no release/upload operation.
    historical_recovery=[]
    official=exact_index([r for r in d.get('official_current',[]) if r['item'] in ITEMS
                         and r['marketing_id']==ITEMS[r['item']][0]],('item','sku'))
    for item in protection.get('historical_changed_scope_candidates',[]):
        if item not in ITEMS or not any(i==item for i,s in old):continue
        expected={p:r for p,r in current.items() if p[0]==item}
        actual={p:r for p,r in official.items() if p[0]==item}
        if (set(expected)==set(actual) and all(actual[p]['state']=='异常'
                and actual[p]['marketing_id']==expected[p]['marketing_id']
                and money(actual[p]['activity_price'])==money(expected[p]['activity_price']) for p in expected)):
            historical_recovery.append(item)
    blocked_items={i for i in ITEMS if (i in protection['signup_items'] and i not in historical_recovery)
                   or i in protection['no_sales_items']}
    blocked_items.update(r['item'] for r in activity if (r['item'],r['sku']) in protection['signup_pairs']
                         or (r['item'],r['original_sku']) in protection['signup_pairs'])
    held.extend(dict(item=i,phase='signup',reason='whole_item_contains_success_or_unknown_no_replay') for i in sorted(blocked_items))
    held_activity=[r for r in activity if r['item'] in blocked_items]
    activity=[r for r in activity if r['item'] not in blocked_items]
    # No-sales replacements remain entirely separate from the new-SKU sheet.
    rounds=[]
    for r in d['round']['scope_skus']:
        pair=r['item'],r['sku'];f=facts.get(pair)
        erp=[x for x in snapshot['all_erp_rows'] if x['code']==r['erp_code']]
        if (not f or len(erp)!=1 or erp[0].get('custom') is not False or f['sku_code']!=r['erp_code']
                or money(f['price'])!=money(r['base']) or money(erp[0]['medium_target'])!=money(r['target'])
                or money(r['base'])-money(r['target'])!=money(r['deduct'])):
            raise ValueError('round_conditional_math_or_mapping_changed')
        rounds.append(dict(r,upload_ready=False,mode='no_official_conditional',
                           protected_existing_discount=pair in protection['discount_pairs']))
    if len(rounds)!=4 or {r['item'] for r in rounds}!={'918692510350'}:raise ValueError('exact_round_four_required')
    remaining=[r for r in d['current']['missing_price_rows'] if r['item'] in ('793202812082','722275846168')]
    return dict(schema='campaign-rotation-prepare-v1',campaign=CAMPAIGN,price_version=VERSION,
        activity_rows=activity,discount_rows=discounts,conditional_round_rows=rounds,
        held=held,held_activity_rows=held_activity,custom_corrections=d['custom']['fixed_basis'],
        rotation_lineage=conditions,missing_price_rows=remaining,
        requested_activity_items=ITEMS,upload_ready=False,platform_write=False,erp_write=False,
        historical_success_preserved=True,conditional_changed_scope_recovery=historical_recovery,
        official_current_original_source=dict(path=str(OFFICIAL[0]),sha256=OFFICIAL[1],observed_at='2026-09-28 02:29:29'),
        later_0403_original_missing=True,whole_event_complete=False,
        activity_conditions=['旧9禁用和新9启用未由导出证明；不得将库存当开关',
            '02:29原始官方导出核同营销ID异常；04:03只作派生佐证。旧成功不改写、旧营销ID未撤销，不证明导入可原位更新',
            '新9活动卡控及报名结果未知；2定制仅为修正值，尚未生效'],
        discount_conditions=['精确未来窗口、新9的同窗口优惠无叠加和超级立减10%模式尚须验证'],
        round_conditions=d['round']['release_conditions'],
        whole_scope_note='59件商品导出不等于全场已报名；本表仅两件21行授权改动范围，另有缺价和其他范围。')


def inputs():
    d={k:json.loads(pinned(path,sha).decode('utf-8-sig')) for k,(path,sha) in INPUTS.items()}
    from campaign_official_current import parse
    d['official_current']=parse(pinned(*OFFICIAL))[0]
    from campaign_product_scope import from_edge_job
    verified=from_edge_job(d['export'],expected_request_id='c6a097fed233d133c3c59495b5157de0fe9771ccb1b64dc1cd6cbf3af1ad4fb2',
        expected_shop='畔色木作',roots=[PROJECT/'Web-Agent程序/data/output/campaign-transfers'])
    if verified!=d['scope'] or d['mapping']['source_sha256']!=INPUTS['scope'][1]:raise ValueError('export_scope_changed')
    return d


def build(d,protection,master,discount_master):
    result=calculate(d,protection);files={}
    if result['activity_rows']:
        wanted={(r['item'],r['sku']) for r in result['activity_rows']}
        subset=deepcopy(d['scope']);subset['sku_facts']=[e for e in subset['sku_facts'] if (e['facts']['item'],e['facts']['sku']) in wanted]
        projected=project(master,subset,sorted({i for i,s in wanted}))
        files['活动报名-完整待处理商品-待条件确认.xlsx']=fill_selected_rows(projected,result['activity_rows'],official_rate='10%')
    if result['discount_rows']:
        files['单品立减-9条新SKU-待条件确认.xlsx']=fill_single_discount_rows(discount_master,result['discount_rows'])
    if result['conditional_round_rows']:
        files['单品立减-圆弧4条-HOLD不得上传.xlsx']=fill_single_discount_rows(discount_master,result['conditional_round_rows'])
    result['files']=[dict(name=name,sha256=digest(raw),upload_ready=False) for name,raw in files.items()]
    result['sources']={k:dict(path=str(path),sha256=sha) for k,(path,sha) in INPUTS.items()}
    result['sources']['official_current']=dict(path=str(OFFICIAL[0]),sha256=OFFICIAL[1])
    return result,files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,help='Explicit one-time prepare-only XLSX delivery; otherwise check in memory only')
    args=parser.parse_args()
    result,files=build(inputs(),current_protection(),pinned(MASTER,MASTER_SHA),load_fixed_discount_template())
    if args.output_dir:
        # One immutable local batch. Existing preparation (even unknown) cannot
        # be regenerated at a different path to evade the hold.
        hold=PROJECT/'活动准备/报名状态/rotation-preparations/20260928-nine-sku-batch'
        if args.output_dir.exists() or hold.exists():raise ValueError('existing_preparation_do_not_regenerate')
        hold.parent.mkdir(parents=True,exist_ok=True);hold.mkdir()
        with (hold/'reservation.json').open('x',encoding='utf-8') as f:
            json.dump(dict(output=str(args.output_dir.resolve()),sources=result['sources'],upload_ready=False),f,ensure_ascii=False,indent=2)
        args.output_dir.mkdir(parents=True,exist_ok=False)
        for name,raw in files.items():
            with (args.output_dir/name).open('xb') as f:f.write(raw)
        with (args.output_dir/'receipt.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(activity_rows=len(result['activity_rows']),discount_rows=len(result['discount_rows']),
        round_hold_rows=len(result['conditional_round_rows']),missing_price_rows=len(result['missing_price_rows']),
        held=result['held'],files=result['files'],upload_ready=False,written=bool(args.output_dir)),ensure_ascii=False))


if __name__=='__main__':main()
