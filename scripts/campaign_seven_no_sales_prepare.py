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
