"""One local file-generation entry for 01's exact September Super-88 continuation.

Reads one ERP price version and the already-downloaded official files.
Never opens a browser, uploads, submits, modifies prices, or uploads discounts.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from campaign_official_template import fill_selected_rows, money, read_rows, template_rows

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / 'docs/campaign-draft52-continuation.json'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def verify_local_evidence(scope, template, discount):
    if digest(template) != scope['template_sha256'] or digest(discount) != scope['discount_source_sha256']:
        raise ValueError('not_the_current_authorized_official_files')
    required = {(r['item_id'], r['sku_id']): money(r['legacy_deduct']) for r in scope['ordinary_scope']}
    if len(required) != 52 or len(scope['ordinary_scope']) != 52:
        raise ValueError('ordinary_scope_not_exact_52')
    blocked = {(r['item_id'], r['sku_id']) for r in scope['blocked_custom']}
    if len(blocked) != 18 or required.keys() & blocked:
        raise ValueError('invalid_custom_exclusion')
    rows = read_rows(discount, '0')
    headers = rows.get(1, {})
    if any(headers.get(c) != v for c, v in {'A':'活动ID', 'D':'活动开始时间', 'E':'活动结束时间', 'G':'商品ID', 'I':'SKU ID', 'L':'优惠值', 'M':'抹分取整'}.items()):
        raise ValueError('official_discount_columns_changed')
    found = {}
    for n, row in rows.items():
        pair = (row.get('G'), row.get('I'))
        if n == 1 or pair not in required:
            continue
        if pair in found:
            raise ValueError('duplicate_official_discount')
        if row.get('A') != scope['existing_discount_activity_id'] or row.get('D') != scope['start'] or row.get('E') != scope['end']:
            raise ValueError('existing_discount_window_mismatch')
        if row.get('M') != '不抹分取整':
            raise ValueError('unsupported_existing_discount_rounding')
        found[pair] = money(row.get('L'))
    if found != required:
        raise ValueError('existing_discount_scope_or_amount_mismatch')
    current = template_rows(template)
    if {(r['item'], r['sku']) for r in current} != required.keys() | blocked:
        raise ValueError('current_template_not_exact_70_scope')
    if any(r['state'] != '草稿' for r in current):
        raise ValueError('only_unpublished_current_drafts_allowed')
    return required


def build_price_rows(scope, erp):
    result, issues = [], []
    required = scope['ordinary_scope']
    for row in required:
        pair = row['item_id'], row['sku_id']
        matches = []
        for b in erp:
            alt = b.get('alt') or []
            if isinstance(alt, str):
                alt = json.loads(alt)
            if str(b.get('item')) == pair[0] and (str(b.get('sku')) == pair[1] or (isinstance(alt, list) and pair[1] in map(str, alt))):
                matches.append(b)
        if len(matches) != 1 or matches[0].get('custom') is not False:
            issues.append({'item':pair[0], 'sku':pair[1], 'error':'ordinary_erp_mapping_not_unique'})
            continue
        b = matches[0]
        daily, target, deduct = money(b['daily']), money(b['target']), money(row['legacy_deduct'])
        official_cut = (daily * Decimal('.12')).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        final = daily - official_cut - deduct
        delta = final - target
        # This one user's exact continuation exception never modifies global policy.
        if daily <= 0 or target <= 0 or not Decimal('0.00') <= delta <= Decimal('2.00'):
            issues.append({'item':pair[0], 'sku':pair[1], 'error':'outside_current_user_positive_tolerance', 'delta':str(delta)})
            continue
        result.append({'item':pair[0], 'sku':pair[1], 'erp_code':b['code'], 'activity_price':str(daily), 'erp_big_target':str(target), 'existing_deduct':str(deduct), 'calculated_final':str(final), 'positive_delta_authorized_this_time':str(delta)})
    return result, issues


def load_erp_snapshot(scope):
    items = sorted({r['item_id'] for r in scope['ordinary_scope']})
    if not all(i.isdigit() for i in items):
        raise ValueError('invalid_fixed_item_scope')
    sql = "BEGIN READ ONLY; SELECT row_to_json(q) FROM (SELECT p.taobao_item_id::text AS item, p.taobao_sku_id::text AS sku, p.alt_taobao_sku_ids AS alt, s.sku_code::text AS code, s.daily_price::text AS daily, p.big_buyer_price::text AS target, s.is_custom_placeholder AS custom FROM pricing_sku s JOIN pricing_sku_promo p ON p.sku_code=s.sku_code WHERE p.taobao_item_id::text IN (" + ','.join("'" + i + "'" for i in items) + ") ORDER BY p.taobao_item_id,p.taobao_sku_id,s.sku_code) q; COMMIT;"
    command = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-db-1 psql -X -v ON_ERROR_STOP=1 -U panse -d panse_erp -At'
    completed = subprocess.run(['C:/Program Files/Git/usr/bin/ssh.exe', '-i', str(Path.home()/'.ssh/panse_nas'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-p', '2222', '15068803006@DS923plus', command], input=sql+'\n', capture_output=True, text=True, encoding='utf-8', timeout=45, check=True)
    return [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{')]


def prepare(template_path, discount_path, output_dir, erp=None):
    scope = json_read(SCOPE)
    if scope.get('submission_checkpoint', {}).get('new_generation_allowed') is False:
        raise ValueError('this_continuation_already_uploaded_do_not_replay')
    template, discount = template_path.read_bytes(), discount_path.read_bytes()
    verify_local_evidence(scope, template, discount)
    erp = load_erp_snapshot(scope) if erp is None else erp
    rows, issues = build_price_rows(scope, erp)
    if issues or len(rows) != 52:
        return {'status':'local_formula_or_mapping_blocked', 'issues':issues, 'platform_write':False, 'output_created':False}
    prior = json_read(ROOT / 'docs/receipts/campaign-49462-price-audit-20260906.json')
    successful = {(r['item'],r['sku']) for r in prior['repaired_rows']}
    if successful & {(r['item'],r['sku']) for r in rows}:
        raise ValueError('successful_scope_replay_forbidden')
    output = fill_selected_rows(template, rows, official_rate='12%')
    receipt = {
        'status':'file_ready_for_01_single_activity_upload',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'campaign_id':scope['campaign_id'], 'united_activity_id':scope['united_activity_id'], 'sign_record_id':scope['sign_record_id'],
        'campaign_url':'https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/campaign/item.htm?campaignId=49462&unitedActivityId=49469&signRecordId=3527841611',
        'sku_rows':52, 'items':6, 'rows':rows,
        'blocked_custom':scope['blocked_custom'], 'custom_block_reason':'first_original_custom_floor_unverified',
        'price_tolerance_authority':scope['price_tolerance'],
        'exact_target_rows':sum(money(r['positive_delta_authorized_this_time']) == 0 for r in rows),
        'authorized_positive_delta_rows':sum(money(r['positive_delta_authorized_this_time']) > 0 for r in rows),
        'erp_price_version_sha256':digest(json.dumps(erp,ensure_ascii=False,sort_keys=True).encode()),
        'template_sha256':digest(template), 'output_sha256':digest(output),
        'existing_discount_activity_id':scope['existing_discount_activity_id'],
        'existing_discount_reused':True, 'single_discount_upload_required':False,
        'official_partial_sku_acceptance_verified':False,
        'platform_write':False, 'submitted':False, 'automatic_retry':False,
        'runtime_deployment_required':False,
        'note':'Template does not expressly require every SKU of a product. This 52-row file does not establish platform acceptance. Read this upload terminal once; if whole-item completeness is rejected, report it and do not include custom SKUs or replay successful scope.'
    }
    # A new directory is the single local generation receipt; never overwrite old output.
    output_dir.mkdir(parents=True, exist_ok=False)
    xlsx = output_dir / '计划8-52普通SKU-仅活动报名.xlsx'
    receipt['output_file'] = str(xlsx.resolve())
    try:
        with xlsx.open('xb') as stream:
            stream.write(output)
        with (output_dir/'receipt.json').open('x',encoding='utf-8') as stream:
            json.dump(receipt,stream,ensure_ascii=False,indent=2)
    except Exception:
        # Preserve any partially written artifacts for inspection; never auto-retry.
        raise
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--discount-export', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--erp-snapshot', type=Path, help='Reuse the already captured ERP JSON row array for this price version; no second ERP query')
    args = parser.parse_args()
    try:
        result = prepare(args.template,args.discount_export,args.output_dir, json_read(args.erp_snapshot) if args.erp_snapshot else None)
        print(json.dumps({k:v for k,v in result.items() if k not in ('rows','blocked_custom')},ensure_ascii=False))
        sys.exit(0 if result['status'] == 'file_ready_for_01_single_activity_upload' else 2)
    except Exception as exc:
        print(json.dumps({'status':'not_ready','platform_write':False,'error_type':type(exc).__name__,'error':str(exc)},ensure_ascii=False))
        sys.exit(2)
