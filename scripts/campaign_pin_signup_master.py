"""One explicitly authorized missing-master replacement, never signup/upload."""
import argparse
import json
from pathlib import Path
import sys
from campaign_continuous_transport import persist
from campaign_edge_client import EdgeClient
from campaign_entry_authority import load, file_sha
from campaign_price_snapshot import build_snapshot,load_rows
from campaign_official_template import template_rows


def received_master(source, page, request, source_job_id):
    """Receive the user's saved download, without rewriting old timeout jobs.

    The user supplied file is a separate receipt, not a fabricated automatic
    download event. Bind it to the still-visible generated filename and scope.
    """
    source=Path(source).resolve(strict=True)
    if (page.get('ok') is not True or page.get('operation')!='program_inspect_generated_template'
            or page.get('source_job_id')!=source_job_id or page.get('download_clicked') is not False
            or page.get('platform_write') is not False or page.get('identity')!=request['identity']
            or page.get('items')!=request['items'] or page.get('official_filename')!=source.name
            or source.suffix.lower()!='.xlsx'):
        raise ValueError('received_template_generation_binding_mismatch')
    rows=template_rows(source.read_bytes())
    pairs=[(r['item'],r['sku']) for r in rows]
    if not pairs or len(pairs)!=len(set(pairs)) or not {p[0] for p in pairs}.issubset(set(request['items'])):
        raise ValueError('received_template_scope_invalid')
    return dict(path=str(source),sha256=file_sha(source),items=request['items'],
                identity=request['identity'],source='official_current_template_download',
                official_filename=source.name,official_page_url=page['official_page_url'],
                source_job_id=source_job_id,receipt_kind='user_received_existing_download',
                observed_at=page['observed_at'],business_write=False,
                returned_products=len({p[0] for p in pairs}),sku_rows=len(rows),
                missing_requested_products=sorted(set(request['items'])-{p[0] for p in pairs}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--identity',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--collect-job',help='Exact maintenance collection of an already-generated template; never regenerate')
    parser.add_argument('--received-file',type=Path,help='User downloaded file already preserved locally; no new download')
    args=parser.parse_args()
    identity=load(args.identity);root=args.output
    root.mkdir(parents=True,exist_ok=True)
    if identity.get('title')!='超级立减长期活动' or identity.get('campaign_id')!='legacy':
        raise ValueError('only_authorized_missing_super_reduce_master')
    if args.received_file and (root/'master.json').exists():
        from campaign_fixed_signup_template import resolve_master
        master=resolve_master(root/'master.json',identity=identity,roots=[root])
        if file_sha(args.received_file)!=master['sha256']:
            raise ValueError('existing_master_must_not_be_replaced')
        print(json.dumps({'status':'official_master_already_pinned','manifest':str((root/'master.json').resolve()),
                          'sha256':master['sha256'],'platform_write':False},ensure_ascii=False));return
    token=json.loads(sys.stdin.readline())['token'];edge=EdgeClient(token)
    snapshot=root/'master-scope-snapshot.json'
    if not snapshot.exists():persist(snapshot,build_snapshot(load_rows()))
    items=load(snapshot)['current_sellable_item_ids']
    request={'identity':identity,'items':items}
    persist(root/'master-download-request.json',request)
    ref=root/'master-download-job.json'
    if args.received_file:
        if args.collect_job or not ref.exists():raise ValueError('existing_generation_required')
        source_job_id=load(ref)['job_id']
        page=edge.inspect_generated_template(source_job_id)
        result=received_master(args.received_file,page,request,source_job_id)
        persist(root/'master-received-page.json',page)
        persist(root/'master-received-file.json',result)
        job={'job_id':source_job_id,'state':'user_received_existing_download'}
    elif args.collect_job:job=edge.status(args.collect_job)
    elif ref.exists():job=edge.status(load(ref)['job_id'])
    else:
        job=edge.submit('template',request);persist(ref,{'job_id':job['job_id']})
    if job['state']=='running':job=edge.wait(job['job_id'],timeout=180)
    if not args.received_file:
        persist(root/('master-collection-result.json' if args.collect_job else 'master-download-result.json'),job)
        result=job.get('result') or {}
    if not args.received_file and (job['state']!='finished' or result.get('state')!='downloaded'):
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
