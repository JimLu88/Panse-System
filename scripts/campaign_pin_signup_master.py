"""One explicitly authorized missing-master replacement, never signup/upload."""
import argparse
import json
from pathlib import Path
import sys
from campaign_continuous_transport import persist
from campaign_edge_client import EdgeClient
from campaign_entry_authority import load, file_sha
from campaign_price_snapshot import build_snapshot,load_rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--identity',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--collect-job',help='Exact maintenance collection of an already-generated template; never regenerate')
    args=parser.parse_args()
    identity=load(args.identity);root=args.output
    root.mkdir(parents=True,exist_ok=True)
    if identity.get('title')!='超级立减长期活动' or identity.get('campaign_id')!='legacy':
        raise ValueError('only_authorized_missing_super_reduce_master')
    token=json.loads(sys.stdin.readline())['token'];edge=EdgeClient(token)
    snapshot=root/'master-scope-snapshot.json'
    if not snapshot.exists():persist(snapshot,build_snapshot(load_rows()))
    items=load(snapshot)['current_sellable_item_ids']
    request={'identity':identity,'items':items}
    persist(root/'master-download-request.json',request)
    ref=root/'master-download-job.json'
    if args.collect_job:job=edge.status(args.collect_job)
    elif ref.exists():job=edge.status(load(ref)['job_id'])
    else:
        job=edge.submit('template',request);persist(ref,{'job_id':job['job_id']})
    if job['state']=='running':job=edge.wait(job['job_id'],timeout=180)
    persist(root/('master-collection-result.json' if args.collect_job else 'master-download-result.json'),job)
    result=job.get('result') or {}
    if job['state']!='finished' or result.get('state')!='downloaded':
        print(json.dumps({'status':'blocked','job_id':job['job_id'],'detail':result},ensure_ascii=False));return
    if args.collect_job and (job.get('operation')!='template_collect' or result.get('source_job_id')!=load(ref)['job_id']):
        raise ValueError('collection_not_bound_to_original_generation')
    source=Path(result['path'])
    if file_sha(source)!=result['sha256'] or result['items']!=items or result['identity']!=identity:
        raise ValueError('official_master_receipt_mismatch')
    destination=root/'超级立减长期活动-固定官方母版.xlsx'
    if not destination.exists():
        with destination.open('xb') as stream:stream.write(source.read_bytes())
    if file_sha(destination)!=result['sha256']:raise ValueError('master_copy_changed')
    manifest=persist(root/'master.json',dict(result,path=str(destination.resolve()),job_id=job['job_id'],
        replacement_authorization='user_allowed_one_missing_master_redownload',original_download=str(source)))
    print(json.dumps({'status':'official_master_pinned','manifest':manifest,'sha256':result['sha256'],
                      'requested_products':len(items),'platform_write':False},ensure_ascii=False))


if __name__=='__main__':main()
