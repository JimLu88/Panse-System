"""Exact Sep28 official21 failure continuation. Local record/conditional prepare only."""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import re

import campaign_rotation_prepare as c
from campaign_recovery_trial import import_terminal, canonical_scope, write_new
from campaign_official_template import template_rows,read_rows
from campaign_replacement_audit import PROTECTION_ROOT, PROTECTION_FILES, END

ROOT=c.OWNER/'rotation-20260928'
DELIVERY=ROOT/'整批交付'
RECEIPT_SHA='229584e5bb8e77859f2ac23909f2d4a3b765f7af9f6af46c79567fe101c60583'
RESULT=ROOT/'官方21条报名结果-20260928-175731.xlsx'
RESULT_SHA='dd855f5591627abb65db31418dbaef6198c7c2350c8996a3b6d9c8198b01f77a'
BED_SUCCESS=ROOT/'官方床14条成功-20260928-183147.xlsx'
BED_SUCCESS_SHA='d269c2de93a0979ee4621ec6077b23a70ae8ccc29208d64107cc86058d3ab098'
LEDGER=c.PROJECT/'活动准备/报名状态/rotation-preparations/20260928-nine-sku-batch'
BED='1036273574687'
ROCK='792992319206'
ACCESSORIES={'6280268983408','6280268983409'}
ROCK_ORDINARY={'6126676768459','6126676768460','6126676768461','6126676768462','6141883480156'}


def original():
    receipt=json.loads(c.pinned(DELIVERY/'receipt.json',RECEIPT_SHA))
    reservation=json.loads((LEDGER/'reservation.json').read_text(encoding='utf-8'))
    if Path(reservation['output']).resolve()!=DELIVERY.resolve() or reservation['sources']!=receipt['sources']:
        raise ValueError('original_prepare_reservation_changed')
    missing=[];verified=[]
    for f in receipt['files']:
        path=DELIVERY/f['name']
        if not path.is_file() and f['name'] in ('活动报名-完整待处理商品-待条件确认.xlsx','单品立减-9条新SKU-待条件确认.xlsx'):
            path=Path('C:/Users/lzdwy/Desktop')/f['name']
        if not path.is_file():
            missing.append(dict(name=f['name'],sha256=f['sha256']))
            continue  # Pinned reservation/receipt + full official terminal; never reconstruct originals.
        raw=c.pinned(path,f['sha256'])
        verified.append(dict(name=f['name'],path=str(path),sha256=f['sha256']))
        if f['name'].startswith('活动报名'):
            cells=read_rows(raw,'商品SKU导入列表')
            rows=[dict(r,activity_price=cells[r['row']].get('N')) for r in template_rows(raw)]
            if canonical_scope(rows)!=canonical_scope(receipt['activity_rows']):
                raise ValueError('original_activity_scope_changed')
    receipt['missing_original_files']=missing
    receipt['verified_original_files']=verified
    return receipt


def terminal(receipt,raw):
    parsed=import_terminal({'scope':receipt['activity_rows']},raw)
    if (len(parsed['rows'])!=21 or parsed['status']!='failed'
            or parsed['no_sales_items']!=[ROCK]):
        raise ValueError('expected_exact_21_failed_result_changed')
    bed=[r for r in parsed['rows'] if r['item']==BED]
    rock=[r for r in parsed['rows'] if r['item']==ROCK]
    if (len(bed)!=14 or len(rock)!=7 or any(r['failure_kind']!='no_sales_this_campaign' for r in rock)
            or any(r['failure_kind']!='other_official_failure' for r in bed)):
        raise ValueError('whole_item_failure_classification_changed')
    for r in bed:
        constraints=re.findall(r'活动普惠券后价：([0-9.]+)元，最低普惠券后价：([0-9.]+)元',r['warning'])
        if (constraints!=[('195.50','136.38')]*2 or 'mini床头柜-配件;松木' not in r['warning']
                or 'mini床头柜-配件;榉木' not in r['warning']):
            raise ValueError('bed_exact_accessory_constraints_changed')
    return dict(parsed,source=dict(path=str(RESULT),sha256=RESULT_SHA),
                original_receipt_sha256=RECEIPT_SHA,campaign=c.CAMPAIGN,
                missing_original_files=receipt.get('missing_original_files',[]),
                verified_original_files=receipt.get('verified_original_files',[]),
                original_file_hash_basis='pinned_existing_delivery_receipt_not_reconstructed',
                platform_write=False,discount_result='unknown_not_inferred_from_activity_failure')


def terminal_identity(value):
    # These two fields describe file availability WHEN recorded, not the
    # official outcome identity. Moving an original later cannot erase success.
    return {k:v for k,v in value.items() if k not in ('missing_original_files','verified_original_files')}


def record(value,ledger=LEDGER):
    path=Path(ledger)/'official-result-21.json'
    if path.exists():
        if terminal_identity(json.loads(path.read_text(encoding='utf-8')))!=terminal_identity(value):raise ValueError('registered_terminal_changed')
    else:write_new(path,value)
    return path


def bed_success_value(reservation,receipt,raw):
    scope=reservation['activity_rows']
    if (reservation.get('result_sha256')!=RESULT_SHA or len(scope)!=14
            or {r['item'] for r in scope}!={BED}
            or canonical_scope(scope)!=canonical_scope(receipt['activity_rows'])
            or receipt.get('official_terminal',{}).get('source',{}).get('sha256')!=RESULT_SHA):
        raise ValueError('bed_success_original_followup_scope_changed')
    parsed=import_terminal({'scope':scope},raw)
    if (parsed['status']!='success' or len(parsed['successful_pairs'])!=14
            or parsed['failed_pairs'] or parsed['unknown_pairs']):
        raise ValueError('bed_success_requires_full_official_14_success')
    return dict(parsed,campaign=c.CAMPAIGN,source=dict(path=str(BED_SUCCESS),sha256=BED_SUCCESS_SHA),
        state='official_import_success_no_replay',original_failed_result_sha256=RESULT_SHA,
        platform_write=False,whole_event_complete=False,same_marketing_active_readback='not_observed',
        remaining_no_official_rows=9,remaining_no_official_upload_ready=False)


def read_bed_success(ledger=LEDGER):
    follow=Path(ledger)/'failed-followup'
    reservation_raw=(follow/'reservation.json').read_bytes()
    reservation=json.loads(reservation_raw)
    receipt_path=Path(reservation['output'])/'receipt.json'
    receipt_raw=receipt_path.read_bytes();receipt=json.loads(receipt_raw)
    prior_raw=(Path(ledger)/'official-result-21.json').read_bytes()
    prior=json.loads(prior_raw)
    if (prior.get('source',{}).get('sha256')!=RESULT_SHA or prior.get('status')!='failed'
            or terminal_identity(prior)!=terminal_identity(terminal(original(),c.pinned(RESULT,RESULT_SHA)))):
        raise ValueError('bed_success_original_failed_history_changed')
    value=bed_success_value(reservation,receipt,c.pinned(BED_SUCCESS,BED_SUCCESS_SHA))
    value.update(reservation_sha256=c.digest(reservation_raw),receipt_sha256=c.digest(receipt_raw),
                 previous_failed_record_sha256=c.digest(prior_raw))
    return value


def record_bed_success(ledger=LEDGER):
    value=read_bed_success(ledger);path=Path(ledger)/'failed-followup/official-result-bed14.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=value:raise ValueError('bed_success_record_changed')
    else:write_new(path,value)
    return dict(record_path=str(path),successful_rows=14,failed_rows=0,unknown_rows=0,
                previous_failure_preserved=True,whole_event_complete=False,platform_write=False)


def registered_protection():
    """Consumed by the installed prepare entry; original success/unknown untouched."""
    pairs=set();no_sales=set()
    if not LEDGER.exists():return pairs,no_sales
    registered=LEDGER/'official-result-21.json'
    if registered.exists():
        expected=terminal(original(),c.pinned(RESULT,RESULT_SHA))
        if terminal_identity(json.loads(registered.read_text(encoding='utf-8')))!=terminal_identity(expected):raise ValueError('registered_terminal_changed')
        pairs.update(map(tuple,expected['successful_pairs']+expected['unknown_pairs']))
        no_sales.update(expected['no_sales_items'])
    follow=LEDGER/'failed-followup'
    if follow.exists():
        reservation=json.loads((follow/'reservation.json').read_text(encoding='utf-8'))
        if reservation['result_sha256']!=RESULT_SHA:raise ValueError('followup_reservation_changed')
        pairs.update((r['item'],r['sku']) for r in reservation['activity_rows'])
        success=follow/'official-result-bed14.json'
        if success.exists():
            value=read_bed_success()
            if json.loads(success.read_text(encoding='utf-8'))!=value:raise ValueError('bed_success_record_changed')
            pairs.update(map(tuple,value['successful_pairs']))
    return pairs,no_sales


def calculate(receipt,d,result,protection,old_discount):
    if result['status']!='failed' or result['no_sales_items']!=[ROCK]:raise ValueError('wrong_terminal')
    facts=c.exact_index([e['facts'] for e in d['scope']['sku_facts']],('item','sku'))
    old=c.exact_index(old_discount['rows'],('item','sku'))
    bed=[r for r in receipt['activity_rows'] if r['item']==BED]
    if len(bed)!=14 or len({r['sku'] for r in bed})!=14:raise ValueError('full_bed_scope_required')
    blocked=any((r['item'],r['sku']) in protection['signup_pairs'] or
                (r['item'],r['original_sku']) in protection['signup_pairs'] for r in bed)
    if BED in protection['no_sales_items']:blocked=True
    if BED in protection['signup_items'] and BED not in protection.get('historical_changed_scope_candidates',[]):blocked=True
    accessories=[]
    for r in bed:
        if r['sku'] not in ACCESSORIES:continue
        pair=BED,r['sku'];previous=old.get(pair)
        if (c.money(r['activity_price'])!=Decimal('217.50') or not previous
                or c.money(previous['deduct'])!=Decimal('59.12')
                or c.money(previous['frozen_target'])!=Decimal('136.63')
                or c.money(previous['calculated_final'])!=Decimal('136.38')
                or 'mini床头柜' not in facts[pair]['attributes']):
            raise ValueError('accessory_original_target_or_binding_changed')
        deduction=c.money(r['activity_price'])-c.official_cut(c.money(r['activity_price']),Decimal('.10'))-Decimal('136.38')
        if deduction!=Decimal('59.12'):raise ValueError('accessory_amount_changed')
        accessories.append(dict(item=BED,sku=r['sku'],deduct=str(deduction),target='136.63',
            calculated_final='136.38',cumulative_delta='-0.25',
            mode='official_10_conditional',old_offer='147717819883',
            existing_success_protected=True,additional_discount_authorized=False,
            action='核原优惠窗口和成员；已有效则不补，确证缺失或替换后才使用总额59.12，不是再加59.12'))
    if {r['sku'] for r in accessories}!=ACCESSORIES:raise ValueError('exact_two_accessories_required')
    replacements=[]
    for sku in sorted(ROCK_ORDINARY):
        f=facts[(ROCK,sku)]
        mapping=[r for r in d['mapping']['rows'] if r['item']==ROCK and r['new_sku']==sku]
        old_sku=mapping[0]['old_sku'] if len(mapping)==1 else sku
        erp=[r for r in d['snapshot']['all_erp_rows'] if r['item']==ROCK and r['code']==f['sku_code']
             and old_sku in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
        if len(erp)!=1 or erp[0]['custom'] is not False:raise ValueError('exact_rock_mapping_required')
        goal=c.money(erp[0]['medium_target']);base=c.money(f['price']);deduct=base-goal
        if goal<c.money(erp[0]['big_target']) or deduct<0:raise ValueError('rock_target_invalid')
        replacements.append(dict(item=ROCK,sku=sku,original_sku=old_sku,erp_code=f['sku_code'],
            base=str(base),target=str(goal),deduct=str(deduct),mode='no_official_conditional',
            original_discount_result='success_or_unknown_protected',upload_ready=False))
    rounds=deepcopy(receipt['conditional_round_rows'])
    if len(rounds)!=4 or {r['item'] for r in rounds}!={'918692510350'}:raise ValueError('round_scope_changed')
    return dict(schema='campaign-rotation-failed-followup-v1',campaign=c.CAMPAIGN,
        official_terminal=result,activity_rows=[] if blocked else bed,held_activity_rows=bed if blocked else [],
        accessory_rows=accessories,no_official_rows=replacements+rounds,
        skipped_activity_items=[ROCK],skipped_activity_rows=7,missing_price_rows=receipt['missing_price_rows'],
        prior_nine_discount_not_replayed=True,prior_nine_discount_result='unknown',
        upload_ready=False,platform_write=False,erp_write=False,whole_event_complete=False,
        end=END,start=None,conditions=[
            '床两配件59.12为总优惠，不是新增59.12；旧327成功保护不解除，先核真实窗口和成员',
            '床14仅为同一失败范围候选；原9单品是否生效未知，不重传9表',
            '岩板桌本场7条活动不再报名；5普通款以商品G减原中促目标，仅在无官方优惠且原优惠精确撤出、不叠加后使用',
            '圆弧4同样HOLD；本表9无官方款不包含两条岩板定制',
            '读取窗口不等于新优惠实际生效窗口；起点必须为实际设置的未来时间，终点10月7日19:59:59'])


def build(receipt,d,result,protection,old_discount):
    plan=calculate(receipt,d,result,protection,old_discount);files={}
    if plan['activity_rows']:
        wanted={(r['item'],r['sku']) for r in plan['activity_rows']}
        scope=deepcopy(d['scope']);scope['sku_facts']=[e for e in scope['sku_facts'] if (e['facts']['item'],e['facts']['sku']) in wanted]
        projected=c.project(c.pinned(c.MASTER,c.MASTER_SHA),scope,[BED])
        files['床14条活动-配件优惠核实后使用.xlsx']=c.fill_selected_rows(projected,plan['activity_rows'],official_rate='10%')
    master=c.load_fixed_discount_template()
    files['床2配件总优惠-59.12非追加-HOLD.xlsx']=c.fill_single_discount_rows(master,plan['accessory_rows'])
    files['岩板5及圆弧4无官方优惠替换-HOLD.xlsx']=c.fill_single_discount_rows(master,plan['no_official_rows'])
    plan['files']=[dict(name=n,sha256=c.digest(b),upload_ready=False) for n,b in files.items()]
    return plan,files


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--record',action='store_true',help='Append exact official21 result to existing reservation, idempotent')
    ap.add_argument('--record-bed-success',action='store_true',help='Record pinned official14 success to original followup; no XLSX creation')
    ap.add_argument('--output-dir',type=Path,help='One prepare-only followup; requires --record; never upload')
    args=ap.parse_args()
    if args.record_bed_success:
        if args.record or args.output_dir:raise ValueError('bed_success_record_only_no_prepare')
        print(json.dumps(record_bed_success(),ensure_ascii=False));return
    if args.output_dir and not args.record:raise ValueError('record_original_terminal_first')
    receipt=original();value=terminal(receipt,c.pinned(RESULT,RESULT_SHA))
    d=c.inputs();protection=c.current_protection()
    old=json.loads(c.pinned(PROTECTION_ROOT/'prepared-327-receipt.json',PROTECTION_FILES['prepared-327-receipt.json']))
    plan,files=build(receipt,d,value,protection,old)
    if args.record:record(value)
    if args.output_dir:
        hold=LEDGER/'failed-followup'
        if args.output_dir.exists() or hold.exists():raise ValueError('followup_exists_no_regeneration')
        hold.mkdir()
        write_new(hold/'reservation.json',dict(output=str(args.output_dir.resolve()),result_sha256=RESULT_SHA,
                  activity_rows=plan['activity_rows'],upload_ready=False))
        args.output_dir.mkdir(parents=True,exist_ok=False)
        for name,raw in files.items():
            with (args.output_dir/name).open('xb') as f:f.write(raw)
        write_new(args.output_dir/'receipt.json',plan)
    print(json.dumps(dict(official_failed=len(value['rows']),activity_rows=len(plan['activity_rows']),
        accessory_rows=len(plan['accessory_rows']),no_official_rows=len(plan['no_official_rows']),
        files=plan['files'],recorded=args.record,written=bool(args.output_dir),upload_ready=False),ensure_ascii=False))


if __name__=='__main__':main()
