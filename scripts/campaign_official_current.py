"""Pinned, read-only official inventory audit; no enrollment or price mutation."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from campaign_official_template import _archive, _read, _effective, sheet_path, money

PROJECT=Path('D:/AI/畔色ERP系统')
EXPORT=PROJECT/'outputs/campaign-super43-rejected-20260928/official-current-20260928-022929.xlsx'
EXPORT_SHA='262a29aafeae858021f0e273a8274eaf8e378a082e163742ea2dfebbf1bc0778'
PREVIOUS=PROJECT/'outputs/01a03341-b2cd-7810-92f3-66fad189521d/super43-full-period-20260928/result.json'
PREVIOUS_SHA='f35bbb9e991838a99d162ee0749e672d3eaefe3ae92e1193b76a5b9b6530edf6'
FIELDS={'A':'商品ID','B':'商品名称','C':'营销ID','D':'商品状态','E':'SKUID','F':'SKU名称',
        'I':'最低普惠券后价要求','K':'活动普惠券后价','P':'活动价'}


def pinned(path, digest):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('official_current_source_changed:'+str(path))
    return raw


def parse(raw):
    with _archive(raw) as archive:
        _,_,rows,merges=_read(archive,sheet_path(archive,'已报商品列表'))
    if any(rows.get(2,{}).get(k)!=v for k,v in FIELDS.items()):
        raise ValueError('official_current_headers_changed')
    parsed=[]
    for n,row in sorted(rows.items()):
        if n<4 or not row.get('E'):continue
        item,name,marketing,state=(_effective(rows,merges,n,c) for c in 'ABCD')
        if not all((item,name,marketing,state)) or not all(v.isdigit() for v in (item,marketing,row['E'])):
            raise ValueError('official_current_identity_missing_not_merged:'+str(n))
        parsed.append(dict(item=item,name=name,marketing_id=marketing,state=state,sku=row['E'],
                           spec=row.get('F',''),final=row.get('K') or None,cap=row.get('I') or None,
                           activity_price=row.get('P') or None,excel_row=n))
    return parsed,dict(price_rule=rows[3].get('I'),final_price_meaning=rows[3].get('K'),
                       marketing_id_import_limit=rows[3].get('C'),state_import_limit=rows[3].get('D'),
                       abnormal_recovery_instruction=None)


def price_status(row):
    if row['final'] is None or row['cap'] is None:return '价格证据缺失'
    try:return '价格满足' if money(row['final'])<=money(row['cap']) else '券后价超限'
    except ValueError:return '价格证据无效'


def audit_rows(rows, expected):
    records={}
    for row in rows:
        key=row['item'],row['marketing_id']
        if key in records and records[key]!=row['state']:raise ValueError('marketing_state_conflict')
        records[key]=row['state']
    current=[dict(r,price_check=price_status(r)) for r in rows if r['state'] in ('活动中','异常')]
    by_pair={}
    for row in current:
        pair=row['item'],row['sku']
        if pair in by_pair:raise ValueError('duplicate_current_sku_requires_resolution')
        by_pair[pair]=row
    expected=set(expected)
    matched=[by_pair[pair] for pair in sorted(expected) if pair in by_pair]
    missing=sorted(expected-set(by_pair))
    over=[r for r in current if r['price_check']=='券后价超限']
    unknown=[r for r in current if r['price_check'] in ('价格证据缺失','价格证据无效')]
    groups=defaultdict(list)
    for row in current:groups[row['item']].append(row)
    if any(len({r['marketing_id'] for r in rs})!=1 for rs in groups.values()):
        raise ValueError('multiple_current_marketing_records_for_item')
    products=[dict(item=item,name=rs[0]['name'],marketing_id=rs[0]['marketing_id'],
                   state=rs[0]['state'],sku_count=len(rs),price_counts=dict(Counter(r['price_check'] for r in rs)),
                   all_sku_price_satisfied=all(r['price_check']=='价格满足' for r in rs)) for item,rs in groups.items()]
    return dict(schema='official-current-inventory-audit-v1',total_rows_including_history=len(rows),
        current_items=len(groups),current_marketing_records=sum(s in ('活动中','异常') for s in records.values()),
        current_skus=len(current),record_states=dict(Counter(records.values())),
        draft_records=sum(s=='草稿' for s in records.values()),
        current_price_counts=dict(Counter(r['price_check'] for r in current)),
        protected_price_satisfied_pairs=[[r['item'],r['sku']] for r in current if r['price_check']=='价格满足'],
        protected_active_pairs=[[r['item'],r['sku']] for r in current if r['state']=='活动中'],
        matched_scope=dict(expected=len(expected),matched=len(matched),missing=missing,rows=matched,
                           price_counts=dict(Counter(r['price_check'] for r in matched)),
                           record_states=dict(Counter(r['state'] for r in matched))),
        overcap_rows=over,overcap_products=len({r['item'] for r in over}),missing_price_rows=unknown,
        products=products,current_rows=current,
        recovery_path_status='not_proven_from_export',official_abnormal_reason=None,
        next_evidence='价格满足但仍异常的同一营销ID：当前官方异常原因及平台提供的处理入口/规则；不撤回重报，不假定刷新延迟。',
        platform_write=False,database_write=False,upload_ready=False,files=[],
        enrollment_success_claimed=False,whole_event_complete=False)


def current_audit():
    raw=pinned(EXPORT,EXPORT_SHA)
    previous=json.loads(pinned(PREVIOUS,PREVIOUS_SHA))
    expected={(r['item'],r['sku']) for r in previous['discount_rows']}
    if len(expected)!=106:raise ValueError('exact_prior_106_scope_required')
    rows,instructions=parse(raw)
    result=audit_rows(rows,expected)
    # This exact export supersedes the old retry advice only with the complete
    # observed scope. No generic missing-price or unknown-state success fallback.
    match=result['matched_scope']
    if match['missing'] or match['price_counts']!={'价格满足':106}:
        raise ValueError('latest_106_price_confirmation_not_complete')
    result.update(source=dict(path=str(EXPORT),sha256=EXPORT_SHA,as_of='2026-09-28 02:29:29'),
                  official_column_instructions=instructions,prior_generation_sha256=PREVIOUS_SHA,
                  prior_rejection_preserved=True,retry_106_allowed=False,
                  supersedes='old_106_retransmission_instructions_not_original_evidence')
    return result


def supersession_guard():
    current_audit()  # Missing/tampered evidence must not revive obsolete generation.
    raise ValueError('official_0229_export_supersedes_old_prepare_do_not_retry_106_use_audit-current')


def report(result):
    match=result['matched_scope']
    lines=['# 最新官方全店结果（只读）','',
           '106条当前券后价全部满足本次卡控，停止沿用此前重传建议。价格满足不等于活动中。',
           f"当前{result['current_items']}件/{result['current_skus']}SKU，另{result['draft_records']}条草稿营销记录。",
           '营销记录状态：'+json.dumps(result['record_states'],ensure_ascii=False),
           '当前价格分类：'+json.dumps(result['current_price_counts'],ensure_ascii=False),
           f"超限{result['overcap_products']}件/{len(result['overcap_rows'])}SKU，另有{len(result['missing_price_rows'])}SKU缺价或价格无效，不计为通过。",
           '106条自身的状态：'+json.dumps(match['record_states'],ensure_ascii=False),
           '官方导出未提供异常原因或恢复操作说明。'+result['next_evidence'],
           '不能用营销ID制表伪装原位恢复：官方列说明明确导入场景不识别营销ID和商品状态。',
           '不生成上传表，不调整折扣、不撤回重报、不猜测等待刷新即可恢复。旧失败和时间截图保持历史记录。','',
           '## 所有SKU价格满足但仍异常的商品','|商品ID|营销ID|SKU数|','|---|---|---:|']
    for p in result['products']:
        if p['all_sku_price_satisfied'] and p['state']=='异常':lines.append(f"|{p['item']}|{p['marketing_id']}|{p['sku_count']}|")
    for title,rs in [('当前券后价超限',result['overcap_rows']),('缺价或价格无效',result['missing_price_rows'])]:
        lines.extend(['', '## '+title,'|商品ID|SKU ID|营销ID|状态|券后价|卡控价|原表行|','|---|---|---|---|---:|---:|---:|'])
        for r in rs:lines.append('|'+ '|'.join(str(r.get(k) if r.get(k) is not None else '未知') for k in ('item','sku','marketing_id','state','final','cap','excel_row'))+'|')
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output_dir.exists():raise ValueError('output_exists_do_not_overwrite')
    result=current_audit()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    for name,body in [('result.json',json.dumps(result,ensure_ascii=False,indent=2)),('官方最新结果说明.md',report(result))]:
        with (args.output_dir/name).open('x',encoding='utf-8') as f:f.write(body)
    print(json.dumps({k:result[k] for k in ('current_items','current_skus','record_states','current_price_counts','overcap_products','upload_ready','whole_event_complete')},ensure_ascii=False))
