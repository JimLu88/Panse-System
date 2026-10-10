"""Local-only replacement sheet from the exact official eleven-item export.

The user uploads once, not before the exported 23:00 pricing instant. Preserve
the three beds' 7.5折 single-item promotion. Never use a campaign editor's
hypothetical estimate, a list-price fallback, or a caller-supplied ready flag.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from campaign_price_incident_review import verified_request, review, PROJECT, write_new
from campaign_cap_prepare import pinned, sha, coverage, load_checker
from campaign_official_template import _archive, _read, sheet_path, fill_single_discount_rows, money

JOB = '1ab734e9281d4e1b4bd122f35601ea5a8f83871bbdf32b4f29956a4dc5c79704'
EXPORT_JOB = '824c06da8e9494bb1e78dbe240838ebc6fc02368b6a5a41efc5b8cd8d69dc91e'
BASE = PROJECT/'Web-Agent程序/data/output/campaign-transfers'
PROBE = BASE/EXPORT_JOB/'price-calculation-probe'
EXPORT = PROBE/'official-price-details-1790691917967-0.xlsx'
EXPORT_SHA = 'dc5d8118c719ef8d72b5f2c8d5e0d46bba92cec66e7b8837eb4e176c984be093'
MASTER = PROJECT/'活动准备/固定模板/单品立减-SKU级-固定官方模板.xlsx'
MASTER_SHA = 'dae4da7f875398c7cc99226e129d1211cb849a012d4670a66df88459860f117f'
START, END = '2026-09-29 23:00:00', '2026-10-07 19:59:59'
AS_OF_TITLE = START+'-1件预估价(公域)-11个商品-1790691917967-0.xlsx'
PROMO = ('新品促销','单品级','单品宝','7.5折','134202771458')
PROMO_WINDOW = '2026-04-22 18:46:31-2029-05-31 23:59:59'
BEDS = {'1035582527998','1037237545657','1038071596128'}
HEADER = ('商品名称','商品Id','sku名称','skuId','1件预估价(公域)','一口价','标价',
          '活动名称','优惠层级','工具名称','优惠信息','活动id','优惠金额','活动时间')
LEDGER = PROJECT/'outputs/campaign-price-incident-20260929/generated-once'
REQUEST = PROJECT/'outputs/campaign-price-incident-20260929/read-request.json'


def official_rows(raw):
    with _archive(raw) as archive:
        _, root, cells, merges = _read(archive,sheet_path(archive,'sheet1'))
        if list(root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f')):
            raise ValueError('official_export_must_not_contain_formulas')
    columns=list('ABCDEFGHIJKLMN')
    if (cells.get(1,{}).get('A')!=AS_OF_TITLE
            or tuple(cells.get(3,{}).get(c,'') for c in columns)!=HEADER):
        raise ValueError('official_export_headers_or_price_instant_changed')
    rows=[dict(cells[n],source_row=n) for n in sorted(cells) if n>=4]
    if len(rows)!=82 or len({r.get('B') for r in rows})!=11:
        raise ValueError('official_export_82_rows_11_items_required')
    return rows


def calculate(doc, rows, withdrawal, lists):
    originals={(r['item'],r['sku']):r for r in doc['rows']}
    if len(originals)!=59 or len(doc['rows'])!=59:
        raise ValueError('exact_59_frozen_rows_required')
    authorized_items={i for i,_ in originals}
    if {r.get('B') for r in rows}!=authorized_items:
        raise ValueError('official_export_item_scope_changed')
    selected={}
    for r in rows:
        key=r.get('B'),r.get('D')
        if key in originals:
            if key in selected:raise ValueError('duplicate_target_sku_in_official_export')
            selected[key]=r
    if set(selected)!=set(originals):raise ValueError('missing_target_sku_in_official_export')
    absent={(r['item'],r['sku']) for r in withdrawal['rows']
            if r.get('withdrawal_member_absent_verified') is True}
    if set(originals)!=absent:raise ValueError('original_offer_withdrawal_not_verified')
    for item in authorized_items:
        for mode in ('商品级','SKU级'):
            evidence=lists.get((item,mode),{})
            if evidence.get('complete') is not True:raise ValueError('withdrawal_list_incomplete')
            for offer in evidence.get('offers',[]):
                if offer['start']<=END and START<=offer['end'] and offer.get('status') not in ('已暂停','已结束'):
                    raise ValueError('another_overlapping_discount_not_inactive:'+offer['offer_id'])
    result=[]
    for key, original in originals.items():
        r=selected[key]
        final,list_price,base=(money(r.get(c)) for c in 'EFG')
        if min(final,list_price,base)<=0:raise ValueError('positive_official_price_required')
        if key[0] in BEDS:
            if (tuple(r.get(c) for c in 'HIJKL')!=PROMO or r.get('N')!=PROMO_WINDOW
                    or final!=base or base!=list_price*Decimal('.75')
                    or money(r.get('M'))!=list_price-base):
                raise ValueError('existing_bed_discount_composition_not_verified')
            mode='preserve_existing_single_item_75_percent'
        else:
            if any(r.get(c) not in (None,'') for c in 'HIJKLMN') or not final==base==list_price:
                raise ValueError('unexpected_discount_or_price_basis')
            mode='official_export_no_discount'
        target=money(original['frozen_target']);deduct=base-target
        if target<=0 or deduct<=0 or deduct!=deduct.quantize(Decimal('.01')):
            raise ValueError('target_not_reachable_with_positive_cent_deduction')
        result.append(dict(original,official_excel_row=r['source_row'],effective_base=format(base,'.2f'),
            one_price=format(list_price,'.2f'),replacement_deduction=format(deduct,'.2f'),
            expected_final=format(base-deduct,'.2f'),price_basis=mode,price_as_of=START,
            original_promotion_preserved=True,official_activity_discount='0.00',
            current_transaction_price_proven=False,upload_ready=True))
    counts=Counter(r['price_basis'] for r in result)
    if counts!={'preserve_existing_single_item_75_percent':28,'official_export_no_discount':31}:
        raise ValueError('expected_28_bed_plus_31_plain_scope_required')
    return sorted(result,key=lambda r:(r['item'],r['sku']))


def build():
    doc,ref=verified_request(REQUEST)
    withdrawal=review(REQUEST,JOB)  # revalidate existing evidence, never recapture
    data=json.loads(Path(withdrawal['terminal']['path']).read_bytes())
    lists=coverage(data,doc['request']['payload'],load_checker())
    raw=pinned(EXPORT,EXPORT_SHA)
    receipt=json.loads((PROBE/'official-price-details-receipt.json').read_bytes())
    if (receipt.get('sha256')!=EXPORT_SHA or receipt.get('price_as_of')!=START
            or receipt.get('price_type')!='1件预估价(公域)' or receipt.get('dimension')!='SKU'
            or receipt.get('platform_price_write') is not False
            or '成功11条' not in receipt.get('official_row','') or '失败0条' not in receipt['official_row']):
        raise ValueError('official_export_receipt_changed')
    rows=calculate(doc,official_rows(raw),withdrawal,lists)
    workbook=fill_single_discount_rows(pinned(MASTER,MASTER_SHA),[
        dict(item=r['item'],sku=r['sku'],deduct=r['replacement_deduction']) for r in rows])
    return workbook,dict(schema='incident-eleven-official-price-generation-v1',rows=rows,
        counts=dict(items=11,skus=59,preserved_75_percent_skus=28,no_other_discount_skus=31),
        window=dict(start=START,end=END),price_as_of=START,price_type='1件预估价(公域)',
        sources=dict(request=ref,official_export=dict(path=str(EXPORT),sha256=EXPORT_SHA),
                     withdrawal=withdrawal['terminal'],fixed_master=dict(path=str(MASTER),sha256=MASTER_SHA)),
        status='prepared_for_user_upload_not_submitted',upload_not_before=START,
        platform_write=False,database_write=False,business_complete=False,
        protected_other_277_retained=True,successful_and_unknown_other_scope_unchanged=True,
        no_super_reduce_reenrollment=True,
        notes=['保留三款床原7.5折单品宝，仅增加折后减钱，不重复扣25%。',
               '官方快照按23:00生效价计算，不是实时成交证明；价格或其他优惠变化后本表失效。',
               '仅用户上传一次，设置SKU级减钱，开始不早于23:00，截止10/7 19:59:59。'])


def generate(output_dir, *, ledger=LEDGER):
    output_dir=Path(output_dir).resolve();ledger=Path(ledger)
    if ledger.exists():raise ValueError('exact_scope_already_reserved_do_not_generate_or_upload_again')
    if output_dir.exists():raise ValueError('output_directory_exists_do_not_overwrite')
    now=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    if now.strftime('%Y-%m-%d')!='2026-09-29':raise ValueError('pricing_snapshot_date_requires_new_review')
    workbook,receipt=build()
    ledger.mkdir(parents=True)  # atomic singleton, even with a different output path
    write_new(ledger/'reserved.json',dict(output_dir=str(output_dir),export_sha256=EXPORT_SHA,
        request_sha256=receipt['sources']['request']['sha256'],platform_write=False))
    output_dir.mkdir(parents=True)
    file=output_dir/'11件59SKU-保留原优惠-单品立减.xlsx'
    with file.open('xb') as stream:stream.write(workbook)
    receipt.update(file=str(file),sha256=sha(workbook))
    write_new(output_dir/'receipt.json',receipt)
    write_new(ledger/'prepared.json',dict(file=str(file),sha256=sha(workbook),receipt=str(output_dir/'receipt.json')))
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();result=generate(args.output_dir)
    print(json.dumps({k:result[k] for k in ('file','sha256','counts','window','status','platform_write')},ensure_ascii=False))


if __name__=='__main__':main()
