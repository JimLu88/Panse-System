"""Audit an original saved export recovered through the fixed feedback reader.

Local only. Does not change transport jobs, claims, old audits or ERP prices.
The review binds independently inspected original frames; the official file
and complete coverage, not export success alone, determine audit results.
"""
import argparse
from collections import Counter,defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
from campaign_entry_authority import load,file_sha
from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import persist
from campaign_final_audit import manifest,parse_inventory,reconcile,execution_export_conflicts


def saved_job(db_path,jid):
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(jid,)).fetchone()
    if not row:raise ValueError('original_job_missing')
    return dict(row)|{'request':json.loads(row['request']),'result':json.loads(row['result'])}


def validate_source(original,feedback,review,transfers):
    p=original['request']['payload'];f=feedback['result'];record=f.get('record') or {}
    if (original['operation']!='final_inventory' or original['state']!='interrupted_read'
            or feedback['operation']!='feedback' or feedback['state']!='finished'
            or f.get('state')!='downloaded' or f.get('batch')!=review['batch']
            or record.get('历史记录ID')!=review['batch'] or record.get('执行操作')!='已报商品导出'
            or record.get('执行状态')!='成功' or record.get('执行时间')!=review['record_time']
            or feedback['request']['payload']['identity']!=p['identity']
            or review['settings']!={'dimension':'SKU','status':'全部'}):
        raise ValueError('saved_export_identity_changed')
    for job in (original,feedback):
        req=job['request']
        if req['request_sha']!=fingerprint(req['payload']):raise ValueError('saved_export_request_changed')
    counts=re.fullmatch(r'总数量(\d+)件[，,]已成功(\d+)件[，,]失败(\d+)件[。.]?',re.sub(r'\s+','',record.get('执行结果','')))
    if not counts or int(counts[1])!=int(counts[2]) or int(counts[3])!=0 or int(counts[1])<=0:
        raise ValueError('saved_export_not_complete')
    rec=original['result']['recording'];directory=Path(rec['directory']).resolve()
    if not directory.is_relative_to((transfers/original['id']).resolve()):raise ValueError('saved_export_recording_outside_job')
    for name,sha in review['frames'].items():
        path=directory/name
        if path.parent!=directory or path.suffix!='.jpg' or file_sha(path)!=sha:raise ValueError('saved_export_frame_changed')
    if file_sha(rec['video'])!=review['video_sha256']:raise ValueError('saved_export_video_changed')
    path=Path(f['path']).resolve(strict=True)
    if (not path.is_relative_to((transfers/feedback['id']).resolve())
            or file_sha(path)!=f['sha256'] or f['sha256']!=review['file_sha256']):
        raise ValueError('saved_export_file_changed')
    rows=parse_inventory(path.read_bytes())
    if len({r['marketing_id'] for r in rows})!=int(counts[1]):raise ValueError('saved_export_coverage_mismatch')
    return rows,p,dict(path=str(path),sha256=f['sha256']),int(counts[1])


def run(root,transfers,review,output):
    root=Path(root);transfers=Path(transfers);result=load(root/'result.json')
    if result.get('status')!='complete':raise ValueError('original_execution_not_settled')
    original=saved_job(transfers/'jobs.sqlite',review['original_job_id'])
    feedback=saved_job(transfers/'jobs.sqlite',review['feedback_job_id'])
    rows,p,source,total=validate_source(original,feedback,review,transfers)
    checked=[];prices=[]
    for segment in result['segments']:
        data=manifest(root,segment);i=data['payload']['identity']
        identity={k:str(i[k]) for k in ('campaign_id','phase_id','sign_record_id','title','phase_title','start','end')}|{'shop_name':i['shop_id'],'rate_label':format((Decimal(i['official_rate'])*100).normalize(),'f')+'%'}
        if p['identity']!=identity or p['price_window']!=data['window']:raise ValueError('saved_export_wrong_segment')
        value=reconcile(data['pairs'],rows,identity=identity,window=data['window'],observed_at=feedback['updated_at'],coverage_complete=True)
        value.update(segment_id=segment['segment_id'],source_file=source,protected_discount_gaps=data['protected_discount_gaps'])
        checked.append(value)
        snapshot=load(root/'segments'/segment['segment_id']/'resolved-snapshot.json')
        index=defaultdict(dict)
        for e in snapshot['all_erp_rows']:
            for sku in [e.get('sku')]+list(e.get('alt') or []):
                if sku:index[(str(e['item']),str(sku))][e['code']]=e
        expected={(r['item'],r['sku']) for r in data['pairs']}
        for r in rows:
            if (r['item'],r['sku']) not in expected or r['state'] in ('撤销报名','已撤销','已取消','草稿','待提交'):continue
            matches=list(index[(r['item'],r['sku'])].values())
            value=dict(r,erp_mapping_count=len(matches),final_checkout_verified=False)
            if len(matches)==1:
                e=matches[0];value.update(product_name=e['product_name'],erp_code=e['code'],custom=e['custom'],erp_daily=e['daily'],erp_medium_target=e['medium_target'])
                if not e['custom'] and r['activity_price']:
                    value['signup_daily_delta']=str(Decimal(r['activity_price'])-Decimal(e['daily']))
            prices.append(value)
    audit=dict(schema='recovered_saved_inventory_audit_v1',coverage_verified=True,official_marketing_records=total,export_rows=len(rows),segments=checked,price_rows=prices,ordinary_signup_mismatches=[r for r in prices if Decimal(r.get('signup_daily_delta','0'))!=0],execution_export_conflicts=execution_export_conflicts(result,{'segments':checked}),source_file=source,review=review,platform_write=False,old_result_unchanged=True,all_signed_up=False)
    persist(output,audit)
    return dict(coverage_verified=True,official_marketing_records=total,export_rows=len(rows),sku_status_counts=dict(Counter(r['status'] for s in checked for r in s['rows'])),ordinary_signup_mismatch_rows=len(audit['ordinary_signup_mismatches']),all_signed_up=False,output=str(output))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--transfers',type=Path,required=True);ap.add_argument('--review',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    print(json.dumps(run(a.root,a.transfers,load(a.review),a.output),ensure_ascii=False))
