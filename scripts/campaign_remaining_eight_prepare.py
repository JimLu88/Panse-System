"""Local manual-upload packages for the exact remaining eight; never submit.

Price export proves no hidden promotions, not the future campaign base. The
new activity sets ERP daily P, then ceil(P*10%) and the single deduction apply.
Old evidence/claims stay unchanged. Cloud sofa starts only after its old window.
"""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
from pathlib import Path

from campaign_cap_prepare import pinned,sha,readonly,fingerprint
from campaign_local_offer_projection import PROJECT,ITEMS,SOURCE,SOURCE_SHA,terminal,authority_snapshot,project
from campaign_replacement_audit import load_current_protection
from campaign_official_template import _archive,_read,_effective,sheet_path,fill_selected_rows,fill_single_discount_rows,money

ROOT=PROJECT/'outputs/campaign-remaining-eight-price-20260929'
EXPORT_SHA='d5974508087cfc4466944f7cba35e2d5f07093b899ca20383d4c2f366cfc341b'
ACTIVITY=Path('C:/Users/lzdwy/Desktop/「超级立减长期活动」活动商品导出20260928234325.xlsx')
ACTIVITY_SHA='ea4955472980ea39d7e5c9cb04355760cabf82687b4e1aeda7d1ad6ee2068ed4'
AS_OF='2026-09-30 00:00:00'
END='2026-10-07 19:59:59'
CLOUD='720234422814'
CLAIM='c9821f48cda947468c334f9ad0386148'
JOB='d7f370023d905be7e9dc0a884f5dc3fd1b78ad817339dd2cbdade86bd60c612a'
PREUPLOAD_ITEMS={'717780219729','793128577437','793554793170','797139954559'}
WA_ROOT=PROJECT/'Web-Agent程序/data/output/campaign-transfers'
MASTER=PROJECT/'outputs/campaign-continuous/master-replacement-20260911/超级立减长期活动-固定官方母版.xlsx'
MASTER_SHA='be5ae804a19ccf987381ec0d2dab76e374ede9e780c1cc338ae19084574e4e83'
SINGLE=PROJECT/'活动准备/固定模板/单品立减-SKU级-固定官方模板.xlsx'
SINGLE_SHA='dae4da7f875398c7cc99226e129d1211cb849a012d4670a66df88459860f117f'
LEDGER=ROOT/'manual-package-once'
CUSTOM_AUTH=PROJECT/'outputs/campaign-continuous/runs/4200cd437ea3ab6b8c6adb74e7396e3b4a2f46c45a3bceb91c1743d9236d6da7/segments/8ade70facea2f67b318219a3b20c458cb0cb139608060f79c8af80b5e38b2125/actions/2b66d8d0b8e382ee80866ba806db2aeec5c0fcd4ad8101d8a29c2b280aea9d15/custom-authorization.json'


def custom_corrections():
    auth=json.loads(pinned(CUSTOM_AUTH,'bd30214947ef86f8eeea44b4db02ce9abb3e45c4de40917e15b986d52dfb816d'))
    failure=json.loads(pinned(CUSTOM_AUTH.with_name('failed-custom-rows.json'),'a3b04c5c3f9ec83d161ee65d7e24db3c6567f935a62a751593585c9f6429597e'))
    pinned(Path(auth['source_report']),auth['source_report_sha256'])
    result={(r['item'],r['sku']):money(r['activity_price']) for r in auth['authorized_custom_prices']}
    if (len(result)!=5 or set(result)!={(r['item'],r['sku']) for r in failure['rows']}
            or any(r['status']!='failed' for r in failure['rows'])
            or auth['campaign']!='legacy/itemApply/3172207691'):
        raise ValueError('exact_five_custom_corrections_changed')
    return result


def preupload_proof():
    """Consume the existing chunk-3001 proof, without its recovery side effects."""
    with readonly(WA_ROOT/'jobs.sqlite') as db:
        row=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(JOB,)).fetchone()
        if not row or row['operation']!='discount' or row['state']!='unknown':
            raise ValueError('original_preupload_job_changed')
        if (row['request_sha']!='366d5d82a5d3cdc4cf62296961a24de8784b6cebf01b3506b43f6edbc20e1821'
                or sha(row['result'].encode())!='e21904f507937b07caeecab8b9eb0a69189b089d2c131e7a75ac72d5cf4c7b0a'):
            raise ValueError('original_preupload_request_or_result_changed')
        req=json.loads(row['request']);result=json.loads(row['result'])
        if req['payload']['claim_id']!=CLAIM or req['request_sha']!=fingerprint(req['payload']):
            raise ValueError('original_claim_binding_changed')
        if db.execute("SELECT 1 FROM campaign_discount_form_recovery WHERE id=?",(JOB,)).fetchone():
            raise ValueError('old_preupload_job_has_recovery_do_not_release')
    frames={(f.get('file'),f.get('function')) for f in result['program_location']}
    if (('campaign_discount_create.py','set_claim_name') not in frames
            or any(f.get('function')=='create_once' for f in result['program_location'])):
        raise ValueError('preupload_failure_location_changed')
    root=WA_ROOT/JOB
    if {p.name for p in root.iterdir() if p.is_file()}!={'recording-result.json','failure-disposition.json'}:
        raise ValueError('old_job_contains_possible_upload_artifact')
    record=json.loads(pinned(root/'recording-result.json','9cbaf53dda6cad61f5aaa9a0a6faa1cbf6970b76018cb2aa045cf7540ff33301'))
    directory=Path(record['directory']).resolve()
    if directory.parent!=(root/'recording').resolve() or result['recording']!=record:
        raise ValueError('recording_path_or_result_changed')
    pinned(directory/'000013.jpg','c926996f805b26b64d8014fa3fe9dd53808b9bbeffc654419734128988aac8f1')
    pinned(Path(record['video']),'186d7ba6a618c9b7b112d3df7f40ee356c0428d09eacaec14dbfb023eaf9373a')
    events=json.loads((directory/'recording.json').read_bytes())
    if events.get('events')!=[] or record.get('capture_errors') or record.get('active') is not False:
        raise ValueError('preupload_recording_not_complete')
    with readonly(PROJECT/'活动准备/报名状态/campaign-entry.sqlite3') as db:
        transport=db.execute('SELECT transport,state,job_id FROM claim_transports WHERE claim_id=?',(CLAIM,)).fetchone()
        rows=db.execute('SELECT item,status FROM attempts WHERE id LIKE ?',(CLAIM+':%',)).fetchall()
        if tuple(transport or ())!=('dedicated_edge_v1','claimed_not_dispatched',None):
            raise ValueError('old_claim_consumed_or_missing')
        if {r['item'] for r in rows}!=PREUPLOAD_ITEMS or any(r['status']!='unknown' for r in rows):
            raise ValueError('preupload_item_scope_changed')
    return dict(job_id=JOB,claim_id=CLAIM,items=sorted(PREUPLOAD_ITEMS),pre_upload_verified=True,
                old_records_unchanged=True,old_controller_resume_allowed=False,
                scope='new_local_manual_upload_preparation_only')


def workbook_rows(raw,name):
    with _archive(raw) as z:
        _,root,cells,merges=_read(z,sheet_path(z,name))
        if list(root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f')):
            raise ValueError('unexpected_formula_in_official_source')
    return cells,merges


def sources():
    audit=json.loads(pinned(SOURCE,SOURCE_SHA))
    receipt=json.loads((ROOT/'receipt.json').read_bytes())
    if (receipt.get('sha256')!=EXPORT_SHA or tuple(receipt.get('items',[]))!=ITEMS
            or receipt.get('price_as_of')!=AS_OF or receipt.get('dimension')!='SKU'
            or receipt.get('price_type')!='1件预估价(公域)' or receipt.get('ok') is not True
            or '成功8条' not in receipt.get('official_row','') or '失败0条' not in receipt['official_row']):
        raise ValueError('official_eight_export_receipt_changed')
    cells,merges=workbook_rows(pinned(ROOT/'official-eight-price-details.xlsx',EXPORT_SHA),'sheet1')
    from campaign_price_incident_generate import HEADER
    if (tuple(cells[3].get(c,'') for c in 'ABCDEFGHIJKLMN')!=HEADER
            or cells[1].get('A')!='2026-09-30 00:00:00-1件预估价(公域)-8个商品-1790694799113-0.xlsx'):
        raise ValueError('eight_price_header_or_instant_changed')
    rows=[dict({c:_effective(cells,merges,n,c) for c in 'ABCDEFGHIJKLMN'},source_row=n)
          for n in sorted(cells) if n>=4]
    pairs={(r['B'],r['D']) for r in rows}
    expected={(r['item'],r['sku']) for r in audit['activity_rows'] if r['item'] in ITEMS}
    if len(rows)!=102 or len(pairs)!=102 or pairs!=expected:
        raise ValueError('official_102_sku_coverage_not_exact')
    for r in rows:
        if any(r[c] for c in 'HIJKLMN') or not money(r['E'])==money(r['F'])==money(r['G']):
            raise ValueError('hidden_promotion_requires_price_composition_review')
    ac,am=workbook_rows(pinned(ACTIVITY,ACTIVITY_SHA),'已报商品列表')
    caps={}
    for n in sorted(ac):
        item=_effective(ac,am,n,'A')
        if n<4 or item not in ITEMS:continue
        if _effective(ac,am,n,'D')!='草稿' or any(_effective(ac,am,n,c) for c in ('K','P')):
            raise ValueError('existing_activity_success_or_unknown_protected')
        caps[item,_effective(ac,am,n,'E')]=money(_effective(ac,am,n,'I'))
    return audit,{(r['B'],r['D']):r for r in rows},caps


def official_cut(price):return (price*Decimal('.10')).to_integral_value(rounding=ROUND_CEILING)


def calculate(audit,prices,caps,projection,proof):
    activity=[];single=[];blocked=[];facts=[]
    corrections=custom_corrections()
    scopes={r['item']:r for r in projection['rows']}
    for original in audit['activity_rows']:
        item=original['item'];sku=original['sku']
        if item not in ITEMS:continue
        p=money(original['activity_price']);start='2026-10-01 00:00:00' if item==CLOUD else AS_OF
        scope=scopes[item]
        if scope['protected_327_skus']:raise ValueError('original_327_scope_must_not_be_replayed')
        for offer in scope['observed_offers']:
            if offer['start']<=END and start<=offer['end'] and offer.get('status') not in ('已结束','已暂停'):
                raise ValueError('overlap_in_prepared_segment')
        for hold in scope['history']:
            if not hold['unresolved_history_hold']:continue
            if hold['window']['end']<start:continue
            if (item in proof['items'] and hold['original_claims']==[CLAIM+':'+item]):continue
            raise ValueError('unresolved_submission_history:'+item)
        raw=prices[item,sku];cap=caps.get((item,sku));deduct=Decimal(0)
        if original['custom']:
            basis=original['custom_basis']
            p=corrections.get((item,sku),p)
            if cap is not None and p-official_cut(p)>cap:
                p=min(p,(cap/Decimal('.90')).quantize(Decimal('.01'),rounding=ROUND_FLOOR))
            floor=None
            if basis:
                pinned(Path(basis['source']),basis['source_sha256'])
                floor=money(basis['floor'])
            if floor is None and p!=money(original['activity_price']):
                blocked.append(dict(item=item,sku=sku,reason='custom_reduction_missing_fixed_floor'));continue
            if floor is not None and p<floor:
                blocked.append(dict(item=item,sku=sku,reason='custom_below_fixed_floor_rotation_required'));continue
            expected=p-official_cut(p)
        else:
            target=money(original['target']);expected=target
            if cap is not None and cap<target:
                if target-cap>Decimal('2'):
                    blocked.append(dict(item=item,sku=sku,reason='ordinary_cap_beyond_original_two_yuan',target=str(target),cap=str(cap)));continue
                expected=cap
            deduct=p-official_cut(p)-expected
            if deduct<=0:raise ValueError('nonpositive_ordinary_deduction')
        if p>money(raw['F']):raise ValueError('new_activity_price_above_current_one_price')
        row=dict(original,activity_price=format(p,'.2f'))
        activity.append(row)
        if deduct:single.append(dict(item=item,sku=sku,deduct=format(deduct,'.2f'),start=start,end=END))
        facts.append(dict(item=item,sku=sku,custom=original['custom'],window=dict(start=start,end=END),
            current_one_price=raw['F'],current_estimate=raw['E'],campaign_price=format(p,'.2f'),
            official_cut=format(official_cut(p),'.2f'),single_deduction=format(deduct,'.2f'),
            expected_after_both_success=format(expected,'.2f'),cap=str(cap) if cap is not None else None,
            source_row=raw['source_row'],ordinary_erp_daily_preserved=original['custom'] or p==money(original['activity_price']),
            existing_promotions_none=True,not_current_transaction_price=True))
    # A real price blocker isolates its whole item; complete SKU scope remains intact.
    bad={r['item'] for r in blocked}
    return ([r for r in activity if r['item'] not in bad],
            [r for r in single if r['item'] not in bad],facts,blocked)


def build():
    proof=preupload_proof();audit,prices,caps=sources()
    payload,lists,evidence=terminal()
    offers,attempts,ledger=authority_snapshot()
    protected=load_current_protection()
    projection=project(payload,lists,offers,attempts,protected['protected_pairs'])
    activity,single,facts,blocked=calculate(audit,prices,caps,projection,proof)
    files={};groups=[]
    for label,start,wanted in [('7件','2026-09-30 00:00:00',set(ITEMS)-{CLOUD}),('云朵10月1日起','2026-10-01 00:00:00',{CLOUD})]:
        selected=[r for r in activity if r['item'] in wanted]
        discounts=[r for r in single if r['item'] in wanted]
        if not selected:continue
        files[label+'-超级立减10%-报名.xlsx']=fill_selected_rows(pinned(MASTER,MASTER_SHA),selected,official_rate='0.10')
        if discounts:files[label+'-单品立减.xlsx']=fill_single_discount_rows(pinned(SINGLE,SINGLE_SHA),discounts)
        groups.append(dict(label=label,start=start,end=END,items=sorted({r['item'] for r in selected}),
                           activity_skus=len(selected),discount_skus=len(discounts)))
    return files,dict(schema='remaining-eight-manual-package-v1',status='prepared_not_uploaded',
        groups=groups,rows=facts,blocked=blocked,preupload_proof=proof,
        price_source=dict(path=str(ROOT/'official-eight-price-details.xlsx'),sha256=EXPORT_SHA,as_of=AS_OF),
        activity_source=dict(path=str(ACTIVITY),sha256=ACTIVITY_SHA),
        old_snapshot=evidence,ledger_snapshot=ledger,
        old_claims_unchanged=True,cloud_before_october1_protected=True,
        completed_eleven_59_not_in_scope=True,platform_write=False,business_complete=False,
        high_cabinet_724042164333_separate_missing_current_skus=True,
        notes=['单品立减先上传，再超级立减10%；必须两步成功后才达到目标。',
               '新活动价是ERP日常价P，不把当前一口价G当作活动生效后基数。',
               '云朵两张表10月1日00:00后使用；9月30日旧未知范围不重报。',
               '旧chunk3001记录保留，仅确证提交前失败允许新人工制表；禁止恢复旧自动上传。',
               '快照后价格/其他优惠变化时需核对，文件生成不保证平台准入或最终成功。'])


def generate(output):
    if LEDGER.exists():raise ValueError('manual_package_already_generated_no_replay')
    if Path(output).exists():raise ValueError('output_exists_no_overwrite')
    if datetime.now().strftime('%Y-%m-%d') not in ('2026-09-29','2026-09-30'):
        raise ValueError('dated_price_evidence_requires_new_review')
    files,receipt=build()
    LEDGER.mkdir()  # singleton even if caller supplies a different output path
    output=Path(output);output.mkdir(parents=True)
    manifest=[]
    for name,raw in files.items():
        path=output/name
        with path.open('xb') as f:f.write(raw)
        manifest.append(dict(path=str(path),sha256=sha(raw)))
    receipt['files']=manifest
    for path in (output/'receipt.json',LEDGER/'receipt.json'):
        with path.open('x',encoding='utf-8') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--existing-workbook',type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--seven-no-sales',action='store_true')
    mode.add_argument('--seven-no-sales-release',action='store_true');args=parser.parse_args()
    if args.seven_no_sales_release:
        if args.output_dir:parser.error('release does not create or replace workbooks')
        import campaign_seven_no_sales_prepare as fallback
        receipt=fallback.release(args.existing_workbook)
        print(json.dumps({k:receipt[k] for k in ('status','upload_ready','files','constraints')},ensure_ascii=False))
        raise SystemExit(0)
    if args.existing_workbook:parser.error('existing-workbook is only for same-hash release rebinding')
    if args.seven_no_sales:
        import campaign_seven_no_sales_prepare as fallback
        receipt=fallback.generate(args.output_dir) if args.output_dir else fallback.build()[1]
        print(json.dumps({k:receipt[k] for k in ('items','ordinary_skus','status','upload_ready','blockers')},ensure_ascii=False))
        raise SystemExit(0)
    receipt=generate(args.output_dir) if args.output_dir else build()[1]
    print(json.dumps({k:receipt[k] for k in ('groups','blocked','status','platform_write')},ensure_ascii=False))
