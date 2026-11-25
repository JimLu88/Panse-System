"""Run the accepted local pipeline with current ERP data and ERP write claims.

Default is offline contract validation. --prepare uses only platform reads;
--execute additionally requires the production enable switch on the ERP API.
No credential is generated or persisted. Never retry a POST after timeout.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import sys
import urllib.request
import urllib.error

DEFAULT_RUNTIME = Path(r'D:/AI/畔色ERP系统/Web-Agent程序')
PINS = {
    'app/engine/campaign_pipeline_calc.py': '070011304c5c741217db661c443914c4d789fffb191e03bc8fdd20b05dac582f',
    'app/engine/campaign_pipeline.py': '5197e541ef48001e239912bb509b70482fcec40ea615d5fc09a85af20e0fafea',
    'campaign_cli.py': '4d9599fbabf469feeb399ca03e56d8209c273828d5b62acf9f4525e46c745f70',
    'app/browser/campaign_edge_actions.py': '848ae27efb2f59fb77aca43b6716e60dcba674ec7b51f60a693c521c4c708c24',
}


def api(base, path, token, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(base.rstrip('/') + path, data=data,
        headers={'Authorization':f'Bearer {token}', 'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read().decode('utf-8')).get('detail', 'ERP request failed')
        raise RuntimeError(f'ERP {exc.code}: {detail}') from None


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def validate_runtime(runtime):
    for name, expected in PINS.items():
        if hashlib.sha256((runtime/name).read_bytes()).hexdigest() != expected:
            raise ValueError('运行源码与第五轮验收版本不一致：' + name)


def options_for(bundle, bundle_path, work_dir):
    return {'campaign_id':bundle['campaign_id'],
        'united_activity_id':bundle['united_activity_id'],
        'sign_record_id':bundle['sign_record_id'], 'shop_name':bundle['shop'],
        'campaign_start':bundle['campaign_start'], 'campaign_end':bundle['campaign_end'],
        'discount_rate':bundle['discount_rate'], 'erp_target_mode':bundle['campaign_tier'],
        'expected_price_version':bundle['price_version'],
        'single_discount_activity_id':bundle['single_discount_activity_id'],
        'erp_json_path':str(bundle_path), 'work_dir':str(work_dir)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-id', type=int, required=True)
    parser.add_argument('--erp-url', default='http://192.168.31.21:8200')
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument('--output', type=Path, default=Path(r'D:/AI/畔色ERP系统/outputs/campaign-erp-runs'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--recover', metavar='ERP_ATTEMPT_ID', help='只读原任务状态并补回执，绝不重传')
    parser.add_argument('--max-wait', type=int, default=1800)
    args = parser.parse_args()
    token = os.environ.get('PANSE_API_TOKEN')
    if not token:
        raise ValueError('缺 PANSE_API_TOKEN；只接受已有操作员凭据')
    validate_runtime(args.runtime)
    claim = None
    if args.recover:
        if not re.fullmatch(r'[0-9a-f]{24}', args.recover):
            raise ValueError('原ERP任务编号格式错误')
        claim = api(args.erp_url, f'/api/campaigns/{args.plan_id}/pipeline-claims/{args.recover}', token)
        bundle = claim['bundle']
    else:
        bundle = api(args.erp_url, f'/api/campaigns/{args.plan_id}/pipeline-bundle', token)
    run_dir = args.output / f"plan-{args.plan_id}" / bundle['price_version']
    if claim:
        run_dir = run_dir / claim['attempt_id']
    run_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = run_dir/'erp-bundle.json'
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
    opts = options_for(bundle, bundle_path, run_dir)
    calc = load_module(args.runtime/'app/engine/campaign_pipeline_calc.py', '_erp_pipeline_calc')
    cfg = calc.CampaignConfig.from_options(opts, require_erp=True)
    calc.validate_erp_against_config(calc.load_erp_bundle(bundle_path), cfg)
    if not args.prepare and not args.execute and not args.recover:
        print(json.dumps({'validated':True, 'plan_id':args.plan_id,
            'sku_count':len(bundle['all_erp_rows']), 'execution_ready':bundle['execution_ready'],
            'live_enabled':bundle['live_enabled'], 'platform_write':False,
            'file':str(bundle_path)}, ensure_ascii=False))
        return 0
    if not bundle['execution_ready'] and not args.recover:
        raise ValueError('无可执行范围或原系统已有占位；保留原任务，不能启动第二份')
    cli = load_module(args.runtime/'campaign_cli.py', '_erp_pipeline_cli')
    edge_token = cli.load_edge_token()
    if args.execute:
        claim = api(args.erp_url, f'/api/campaigns/{args.plan_id}/pipeline-claim', token,
                    {'price_version': bundle['price_version']})
        run_dir = run_dir / claim['attempt_id']
        run_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = run_dir / 'erp-bundle.json'
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
        opts = options_for(bundle, bundle_path, run_dir)
    if claim:
        (run_dir/'erp-claim.json').write_text(json.dumps({k:claim[k] for k in ('attempt_id','request_id')},indent=2),encoding='utf-8')
    try:
        if args.recover:
            view = cli.call_edge('pipeline_status', opts, token=edge_token)
            result = view.get('status') or {'state':'unknown_pending_verify', 'error':'原任务尚无本地回执；保留占位排查'}
            if (claim.get('receipt') or {}).get('attempt_id') and result.get('attempt_id') != claim['receipt']['attempt_id']:
                raise ValueError('本地结果与原浏览器任务不匹配')
        else:
            started = cli.call_edge('pipeline_enroll' if args.execute else 'pipeline_prepare', opts, token=edge_token)
            if not started.get('ok'):
                raise RuntimeError('浏览器入口未接受任务：' + str(started.get('error')))
            result = cli.poll_until_done(opts, edge_url=cli.DEFAULT_EDGE_URL,
                                        max_wait_sec=args.max_wait, token=edge_token)
    except Exception as exc:
        result = {'state':'unknown_pending_verify' if claim else 'failed', 'error':str(exc)[:2000]}
    (run_dir/'pipeline-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if claim:
        receipt = {'state':result.get('state') if result.get('state') in {'done','partial_done','failed','unknown_pending_verify','needs_manual'} else 'unknown_pending_verify',
            'attempt_id':str(result.get('attempt_id') or (claim.get('receipt') or {}).get('attempt_id') or ''), 'audit':result.get('audit') or {},
            'steps':result.get('steps') or [], 'error':result.get('error')}
        if not args.recover or not claim.get('receipt') or claim['receipt']['state']=='unknown_pending_verify':
            api(args.erp_url, f"/api/campaigns/{args.plan_id}/pipeline-receipts/{claim['attempt_id']}", token, receipt)
    print(json.dumps({'state':result.get('state'), 'erp_attempt_id':claim['attempt_id'] if claim else None,
        'result_file':str(run_dir/'pipeline-result.json'), 'erp_reconciliation_complete':False},ensure_ascii=False))
    return 0 if result.get('state')=='done' else 2


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
