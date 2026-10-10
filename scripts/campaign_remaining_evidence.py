"""Offline, hash-linked diagnosis of remaining items; never grants a retry."""
import hashlib
import json
import sqlite3
from pathlib import Path
from campaign_official_template import read_rows


def category(messages):
    messages=[m.strip() for m in messages if m.strip()]
    if messages and all(m=='商品已报名，且不是草稿态，不支持批量导入，请到商品编辑页手动更新' for m in messages):
        return '已有报名，不可重复批量导入'
    if messages and all(m.startswith('参加本次活动的商品要求满足“动销”校验基础准入门槛要求') and m.endswith('新品规则(点此查看)') for m in messages):
        return '动销准入规则反馈，未提供具体销量结论'
    if any('活动普惠券后价：' in m and not m.rstrip().endswith(('）]', '。')) for m in messages):
        return '原反馈价格文字疑似截断，需要完整官方约束'
    return '需核对完整官方原因' if messages else '提交前事实缺口'


def collect(root, ledger):
    root=Path(root).resolve();result=json.loads((root/'result.json').read_text(encoding='utf-8'))
    pending={i:es for s in result['segments'] for i,es in s['exceptions'].items()}
    records={}
    with sqlite3.connect(Path(ledger).resolve().as_uri()+'?mode=ro',uri=True) as db:
        for jid,res in db.execute("SELECT id,result FROM campaign_transfer_jobs WHERE operation='signup' AND state='finished' ORDER BY updated_at"):
            terminal=json.loads(res or '{}');claim=terminal.get('claim') or {}
            claimed=Path((claim.get('file') or {}).get('path','')).resolve()
            if not claimed.is_relative_to(root):continue
            folder=Path(ledger).parent/jid
            for path in folder.glob('official-feedback-*.xlsx'):
                raw=path.read_bytes();rows=read_rows(raw,'商品SKU导入列表')
                headers=[r for r in rows.values() if all(x in r.values() for x in ('商品ID','失败原因或风险提示'))]
                if len(headers)!=1:raise ValueError('feedback_header_not_unique')
                h=headers[0];ic=next(k for k,v in h.items() if v=='商品ID');mc=next(k for k,v in h.items() if v=='失败原因或风险提示')
                groups={};item=None
                for _,row in sorted(rows.items()):
                    if row.get(ic,'').isdigit():item=row[ic]
                    m=row.get(mc,'')
                    if item in pending and m:groups.setdefault(item,[]).append(m)
                for item,messages in groups.items():
                    records[item]={'messages':list(dict.fromkeys(messages)),'batch':terminal.get('batch'),
                        'file':str(path.resolve()),'sha256':hashlib.sha256(raw).hexdigest()}
    audit={p['item']:p['status'] for s in result.get('final_audit',{}).get('segments',[]) for p in s.get('products',[])}
    out=[]
    for item,issues in sorted(pending.items()):
        evidence=records.get(item,{'messages':[]})
        out.append(dict(item=item,category=category(evidence['messages']),audit_status=audit.get(item),
            issues=list(dict.fromkeys(e.get('reason','') for e in issues)),evidence=evidence,
            retry_authorized=False))
    return {'schema':'remaining-evidence-v1','source_result_sha256':hashlib.sha256((root/'result.json').read_bytes()).hexdigest(),
        'platform_write':False,'claims_changed':False,'rows':out}


if __name__=='__main__':
    import argparse
    from openpyxl import Workbook
    from openpyxl.styles import Font,Alignment
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--ledger',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    value=collect(a.run,a.ledger);raw=json.dumps(value,ensure_ascii=False,indent=2).encode();folder=Path(a.output)/hashlib.sha256(raw).hexdigest();folder.mkdir(parents=True,exist_ok=True)
    jp=folder/'剩余问题证据.json';jp.write_bytes(raw)
    w=Workbook();s=w.active;s.title='剩余问题';s.append(['商品ID','实际问题分类','当前SKU核对状态','平台原文','原批次','原文件','SHA256'])
    for r in value['rows']:
        e=r['evidence'];s.append([r['item'],r['category'],r['audit_status'],'\n'.join(e['messages']) or '；'.join(r['issues']),e.get('batch'),e.get('file'),e.get('sha256')])
    for row in s:
        for cell in row:
            cell.font=Font(name='Arial',size=11,bold=cell.row==1);cell.alignment=Alignment(wrap_text=True,vertical='top')
            if isinstance(cell.value,str):cell.data_type='s'
    for col,width in [('A',19),('B',42),('C',29),('D',90),('E',18),('F',60),('G',40)]:s.column_dimensions[col].width=width
    s.freeze_panes='A2';s.auto_filter.ref=s.dimensions;xp=folder/'剩余问题证据.xlsx';w.save(xp)
    from collections import Counter
    print(json.dumps({'json':str(jp.resolve()),'xlsx':str(xp.resolve()),'categories':dict(Counter(r['category'] for r in value['rows']))},ensure_ascii=False))
