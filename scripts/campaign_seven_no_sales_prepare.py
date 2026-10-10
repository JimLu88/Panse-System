"""Exact seven-item no-sales replacement draft. Local only, never upload-ready.

Failure of the attempted P-price campaign does not prove P became effective.
Use the pinned no-promotion G snapshot only as a conditional candidate basis;
post-failure base, absence of official promotions and old-offer withdrawal must
be verified separately before a human can use the workbook.
"""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import campaign_remaining_eight_prepare as eight
from campaign_official_template import template_rows

ITEMS = set(eight.ITEMS) - {eight.CLOUD}
REPORT = Path('C:/Users/lzdwy/Desktop/「超级立减长期活动」批量报名结果.xlsx')
REPORT_SHA = 'ee6dda1c19d7e9e90af01a15a1bd74da916bc5a48ba2f5c4f53cc3190a8e8c89'
PRIOR_SHA = '217da880512d57c35e16fefd78090261396088ac0afed1cdd2444b7cdb1447a9'
LEDGER = eight.ROOT / 'seven-no-sales-once'
NAME = '待核勿上传-7件32SKU-无超级立减替换表.xlsx'
READ_JOB = '2245d8dfa960c756e8427c25e8cd2d0cb02081d8b23c0192888c29189da4f541'
DELETE_JOB = '35d7cd7e4d16de9fdee2ff2d8f93695fa7823c06c2e8533b12a7b1b8b14fba25'
OFFER = '148273659773'


def terminal_evidence(job, request_sha, result_sha):
    with eight.readonly(eight.WA_ROOT/'jobs.sqlite') as db:
        row = db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(job,)).fetchone()
    if (not row or row['state']!='finished' or eight.sha(row['request'].encode())!=request_sha
            or eight.sha(row['result'].encode())!=result_sha):
        raise ValueError('exact_read_terminal_changed')
    result = json.loads(row['result'])
    if (result.get('platform_write') is not False or result['recording'].get('active') is not False
            or result['recording'].get('capture_errors')):
        raise ValueError('read_recording_not_complete')
    eight.pinned(Path(result['recording']['video']),result['recording']['video_sha256'])
    return result


def assess_release(package, batch, deletion, prices, report_cells, report_merges):
    """Use bounded corroborating evidence, not a new mandatory price-refresh gate."""
    expected={(r['item'],r['sku']):r for r in package['rows']}
    if len(expected)!=32 or {i for i,s in expected}!=ITEMS:
        raise ValueError('release_exact_32_scope_required')
    official={}
    for n,r in report_cells.items():
        if n<4 or not r.get('E'):continue
        key=(eight._effective(report_cells,report_merges,n,'A'),r['E'])
        official[key]=eight.money(eight._effective(report_cells,report_merges,n,'G'))
    if len(official)!=39 or any(official[k]!=eight.money(prices[k]['G']) for k in official):
        raise ValueError('release_report_base_conflict')
    if (len(batch['rows'])!=14 or {(x['item'],x['mode']) for x in batch['rows']}!=
            {(i,mode) for i in ITEMS for mode in ('商品级','SKU级')}
            or any(not x['list_coverage_verified'] for x in batch['rows'])):
        raise ValueError('release_full_offer_lists_required')
    for entry in batch['rows']:
        for offer in entry['offers']:
            if offer['overlaps_requested_window'] and (entry['mode']!='SKU级' or offer['offer_id']!=OFFER):
                raise ValueError('release_other_overlapping_offer')
    if batch['issues']!=[dict(item='797139954559',mode='SKU级',reason='discovered_offer_window_not_readable')]:
        raise ValueError('release_other_read_issue')
    proven=set()
    for row in batch['sku_price_surface']['rows']:
        key=(row['item'],row['sku'])
        if key not in expected:raise ValueError('release_unexpected_surface')
        for observation in row['observations']:
            if (observation['offer_id']!=OFFER or observation['arithmetic_balanced'] is not True
                    or eight.money(observation['displayed_before_column'])!=eight.money(prices[key]['G'])):
                raise ValueError('release_observed_price_conflict')
            proven.add(key)
    missing=set(expected)-proven
    if len(proven)!=30 or missing!={('797139954559','6114933772625'),('797139954559','5433236060059')}:
        raise ValueError('release_unexpected_missing_prices')
    rows=deletion.get('rows',[])
    if (len(rows)!=1 or rows[0].get('offer_id')!=OFFER or rows[0].get('observed_state')!='not_found'
            or rows[0].get('unfiltered_exact_query') is not True or rows[0].get('search_value')!=OFFER
            or rows[0].get('evidence_kind')!='official_exact_id_empty_result'):
        raise ValueError('release_exact_deleted_offer_query_required')
    return dict(upload_ready=True, status='manual_upload_released_by_bounded_evidence',
        platform_write=False, business_complete=False, files=package['files'],
        original_generated_receipt_unchanged=True, workbook_regenerated=False,
        old_offer_id=OFFER,user_confirmed_deleted=True,
        user_confirmation_provenance='用户在03对精确148273659773回复删了，03于本轮转达；不重复要求确认。',
        old_offer_exact_query=rows[0], offer_read_job=READ_JOB, deletion_read_job=DELETE_JOB,
        official_reference_matches=39, live_sku_base_matches=30,
        remaining_two_current_prices_unobserved=sorted([list(p) for p in missing]),
        complete_checkout_composition_verified=False,
        rationale='同窗口官方无其他优惠导出+39条本轮失败报告一致+30条现场算术一致+用户删除及精确ID空结果；无具体价变或其他重叠优惠证据，不新增全量刷新门。',
        constraints=['只用原唯一32条替换总表，不叠加旧活动、不重报本场失败超级立减。',
                     '实际开始时间选上传时的未来时间，截止2026-10-07 19:59:59。',
                     '文件名保留待核字样以保全已交付字节，本回执解除该本轮准备阻断；不是平台上传成功或全32实付验收。'])


def release(existing_workbook=None):
    accepted=LEDGER/'accepted-import.json'
    if accepted.exists():
        result=json.loads(accepted.read_bytes())
        if (result.get('offer_id')!='148277214293' or result.get('success_skus')!=32
                or result.get('failed_skus')!=0 or result.get('workbook_sha256')!=
                '2653938288c631dfeec6834929a742b153e1a1dd38cc63c3f966b8538fe70910'):
            raise ValueError('official_import_receipt_changed_keep_retry_blocked')
        eight.pinned(Path(result['screenshot_path']),
                     '8bdc6701b306dc4426eec0d5f587b6503f40fb472cca15af4f716861b78e83b1')
        return dict(result,status='official_import_32_success_no_retry',upload_ready=False,retry_allowed=False,
                    files=[dict(path=result['workbook_path'],sha256=result['workbook_sha256'])],
                    constraints=['已导入32成功0失败，禁止再次上传。实际窗口和32条最终到手价尚未核实。'])
    if datetime.now().strftime('%Y-%m-%d') not in ('2026-09-29','2026-09-30'):
        raise ValueError('dated_release_not_for_later_campaigns')
    path=LEDGER/'release.json'
    if path.exists():
        result=json.loads(path.read_bytes())
        for f in result['files']:eight.pinned(Path(f['path']),f['sha256'])
        return result
    package=json.loads((LEDGER/'receipt.json').read_bytes())
    if existing_workbook is not None:
        if len(package['files'])!=1:raise ValueError('release_one_original_file_required')
        eight.pinned(Path(existing_workbook),package['files'][0]['sha256'])
        package['files']=[dict(path=str(Path(existing_workbook).resolve()),sha256=package['files'][0]['sha256'])]
    for f in package['files']:eight.pinned(Path(f['path']),f['sha256'])
    if len(package['files'])!=1 or package['files'][0]['sha256']!='2653938288c631dfeec6834929a742b153e1a1dd38cc63c3f966b8538fe70910':
        raise ValueError('release_original_workbook_changed')
    batch=terminal_evidence(READ_JOB,'983142758e9d09d57234e5c37a845c93c79d3e2a816668ccdc36c47ecebd7f75',
                           '6d8f4319aed55f089c3545f6cf292f1da558223456235e7ba5831f84303cd90b')
    deletion=terminal_evidence(DELETE_JOB,'1221bc70c51a20964c69cbede80d640420f653748dcff3b4d25800b7beb1400f',
                              'b081755e4da35850354c58ed57aa0640e59439663741dd2ceb60648e330b3699')
    _,prices,_=eight.sources()
    cells,merges=eight.workbook_rows(eight.pinned(Path(package['report_archive']),REPORT_SHA),'商品SKU导入列表')
    result=assess_release(package,batch,deletion,prices,cells,merges)
    result['same_bytes_relocated']=existing_workbook is not None
    with path.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    return result


def parse_failure(raw, expected):
    cells, merges = eight.workbook_rows(raw, '商品SKU导入列表')
    if cells.get(2, {}).get('Z') != '是否成功' or cells[2].get('AA') != '失败原因或风险提示':
        raise ValueError('wrong_failure_report_columns')
    actual = {}; outcomes = {}; first = {}
    for n in sorted(cells):
        if n < 4:
            continue
        item = eight._effective(cells, merges, n, 'A')
        sku = eight._effective(cells, merges, n, 'E')
        if not item and not sku:
            continue
        key = (item, sku)
        if key not in expected or key in actual:
            raise ValueError('failure_scope_extra_duplicate_or_unknown')
        if eight.money(cells[n].get('N', '')) != eight.money(expected[key]):
            raise ValueError('failure_attempt_price_mismatch')
        if eight.money(eight._effective(cells, merges, n, 'X')) != 10 or cells[n].get('Y'):
            raise ValueError('failure_attempt_discount_mismatch')
        first.setdefault(item, n)
        status = cells[n].get('Z', '').strip()
        reason = cells[n].get('AA', '').strip()
        if status or reason:
            if status != '失败' or not all(s in reason for s in ('动销', '60天', '销售件数≥1件', '不予准入')):
                raise ValueError('not_exact_no_sales_terminal')
            if item in outcomes and outcomes[item]['reason'] != reason:
                raise ValueError('conflicting_item_failure')
            outcomes[item] = dict(item=item, row=n, status=status, reason=reason)
        actual[key] = n
    if set(actual) != set(expected) or set(outcomes) != ITEMS or len(actual) != 39:
        raise ValueError('incomplete_failure_39_scope')
    if any(cells[n].get('Z') != '失败' for n in first.values()):
        raise ValueError('missing_item_first_row_terminal')
    return list(outcomes.values())


def calculate(audit, prices):
    selected = [r for r in audit['activity_rows'] if r['item'] in ITEMS]
    keys = {(r['item'], r['sku']) for r in selected}
    if len(selected) != 39 or len(keys) != 39 or {r['item'] for r in selected} != ITEMS:
        raise ValueError('frozen_seven_scope_changed')
    rows = []; excluded = []
    for r in selected:
        key = (r['item'], r['sku'])
        if r['custom']:
            excluded.append(dict(item=r['item'], sku=r['sku'], reason='custom_not_in_ordinary_replacement'))
            continue
        source = prices[key]
        if any(source.get(c) for c in 'HIJKLMN'):
            raise ValueError('snapshot_has_other_promotions')
        g = eight.money(source['G']); target = eight.money(r['target'])
        if not g == eight.money(source['E']) == eight.money(source['F']):
            raise ValueError('snapshot_no_promotion_base_conflict')
        if target <= 0 or g <= target:
            raise ValueError('invalid_positive_target_or_deduction')
        rows.append(dict(item=r['item'], sku=r['sku'], deduct=format(g-target, '.2f'),
            start=eight.AS_OF, end=eight.END, snapshot_base_g=format(g, '.2f'),
            frozen_target=format(target, '.2f'), official_cut='0.00',
            failed_attempt_p=r['activity_price'], source_row=source['source_row'],
            expected_if_g_unchanged_and_no_other_discounts=format(target, '.2f'),
            current_effective_base_verified=False, upload_ready=False))
    if len(rows) != 32 or len(excluded) != 7 or {r['item'] for r in rows} != ITEMS:
        raise ValueError('ordinary32_custom7_scope_changed')
    return rows, excluded


def build():
    prior = json.loads(eight.pinned(eight.LEDGER/'receipt.json', PRIOR_SHA))
    original = [f for f in prior['files'] if Path(f['path']).name == '7件-超级立减10%-报名.xlsx']
    if len(original) != 1:
        raise ValueError('original_attempt_not_unique')
    original_raw = eight.pinned(Path(original[0]['path']), original[0]['sha256'])
    cells, merges = eight.workbook_rows(original_raw, '商品SKU导入列表')
    expected = {(r['item'], r['sku']): eight._effective(cells, merges, r['row'], 'N')
                for r in template_rows(original_raw)}
    if len(expected) != 39 or {i for i, s in expected} != ITEMS:
        raise ValueError('original_attempt_scope_changed')
    raw = eight.pinned(REPORT, REPORT_SHA)
    outcomes = parse_failure(raw, expected)
    audit, prices, _ = eight.sources()
    rows, excluded = calculate(audit, prices)
    # Keep every previously delivered file intact, including cloud and the old
    # single-discount workbook; this draft does not overwrite/release its ledger.
    for f in prior['files']:
        eight.pinned(Path(f['path']), f['sha256'])
    protection = eight.load_current_protection()
    if {(r['item'], r['sku']) for r in rows} & set(protection['protected_pairs']):
        raise ValueError('completed_scope_overlap')
    data = eight.fill_single_discount_rows(eight.pinned(eight.SINGLE, eight.SINGLE_SHA), rows)
    return data, dict(schema='seven-no-sales-manual-draft-v1', status='prepared_only_upload_blocked',
        upload_ready=False, platform_write=False, business_complete=False,
        campaign='legacy/itemApply/3172207691', campaign_retry_allowed=False,
        items=sorted(ITEMS), ordinary_skus=32, excluded_custom_skus=excluded,
        item_counts=dict(Counter(r['item'] for r in rows)), rows=rows, official_failures=outcomes,
        report=dict(path=str(REPORT), sha256=REPORT_SHA),
        price_source=dict(path=str(eight.ROOT/'official-eight-price-details.xlsx'),
                          sha256=eight.EXPORT_SHA, as_of=eight.AS_OF, post_failure_readback=False),
        prior_package=dict(path=str(eight.LEDGER/'receipt.json'),sha256=PRIOR_SHA),
        old_discount=dict(user_reported_uploaded=True, offer_id=None,
                          user_intends_to_close=True, closure_verified=False),
        blockers=['缺刚上传7件32SKU单品活动精确ID、实际成员/窗口及关闭回执；不能叠加新表。',
                  '需确认活动失败后当前基价仍为此官方G，且本替换窗口没有官方或其他优惠。',
                  '开始时间须为实际上传时可用的未来时间，截止保持10月7日19:59:59。'],
        notes=['仅草稿，未获上传放行；G减额后到目标只是条件计算，不是当前已生效价格。',
               '整表是替换总减额，不能与旧32条优惠叠加，不能按旧减额加10%修正。',
               '7件本场超级立减不重报；云朵、已完成11件59SKU、其他成功及未知范围不变。',
               '不能仅凭活动失败推断新活动价未生效或旧单品优惠已关闭。'])


def generate(output):
    if LEDGER.exists():
        raise ValueError('seven_no_sales_already_generated_no_replay')
    output = Path(output)
    if output.exists():
        raise ValueError('output_exists_no_overwrite')
    if datetime.now().strftime('%Y-%m-%d') not in ('2026-09-29', '2026-09-30'):
        raise ValueError('dated_price_evidence_requires_new_review')
    raw, receipt = build()
    LEDGER.mkdir()  # claim once before any output writes; no blind retry on partial error
    output.mkdir(parents=True)
    with (output/NAME).open('xb') as f:
        f.write(raw)
    # Archive immutable report bytes; later Desktop replacements cannot erase proof.
    with (output/'official-failure.xlsx').open('xb') as f:
        f.write(eight.pinned(REPORT, REPORT_SHA))
    receipt['files'] = [dict(path=str(output/NAME), sha256=eight.sha(raw))]
    receipt['report_archive'] = str(output/'official-failure.xlsx')
    for p in (output/'receipt.json', LEDGER/'receipt.json'):
        with p.open('x', encoding='utf-8') as f:
            json.dump(receipt, f, ensure_ascii=False, indent=2)
    return receipt
