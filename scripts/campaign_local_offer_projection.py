"""Consume the finished 807d6fd read for eight items, offline and report-only.

This is NOT a replacement availability receipt, a generation/upload grant, or
a claim reconciliation. No existing ledger, workbook, platform or service writes.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from campaign_cap_prepare import coverage, fingerprint, load_checker, pinned, readonly, sha

PROJECT = Path('D:/AI/畔色ERP系统')
JOB = '807d6fd40f9fe97025b0d2bf2086e73ac88d410ae8667d367f761cdacd4e51ae'
REQUEST_SHA = '4e928f59d6e626dee96dbc0f78c7a1c94585235b3c64d1577c56bf6de1c0c832'
PAYLOAD_SHA = '13c81c6e7ce412604d4e829b9bb4c704f35cabe1d705239a4b94eaa067cd9fcb'
RESULT_SHA = 'b39467d09c64fc5df84f56f632431a065dd24b77d51655661d0f96e64a1fa9dd'
SOURCE_SHA = '449dcc88e23754397f3e772d5f9b4fa2d16e2889aa5367725a80897dbdb777b3'
READ_ID = 'fedb41943c0b84867a5ee3a6cfbee7b7cd9e1289ae734edf1f2cd9b7d796ed5e'
FINISHED_AT = '2026-09-28T17:08:24.768224+00:00'
VIDEO_SHA = '288e4e624b992213e51e32b77bae3b03ff76544116758fd00cf60ffe1916d350'
SOURCE = PROJECT/'outputs/01a067c6-7e83-7483-9a21-84b44ed7299b/national-closeout-20260929/remaining-audit.json'
WRAPPER = SOURCE.with_name('remaining-discount-read-receipt.json')
ROOT = PROJECT/'Web-Agent程序/data/output/campaign-transfers'
ITEMS = ('717780219729', '720234422814', '722912832184', '793062930418',
         '793128577437', '793135173033', '793554793170', '797139954559')
WINDOW = {'start': '2026-09-28 00:00:00', 'end': '2026-10-07 19:59:59'}
OLD_WINDOW = {'start': '2026-09-28 00:00:00', 'end': '2026-09-30 23:59:59'}
KNOWN_OLD_IDS = {'145761399121', '145807488351'}
IDENTITY = dict(campaign_id='legacy', phase_id='itemApply', sign_record_id='3172207691',
                title='超级立减长期活动', phase_title='商品提交',
                start='2025-06-21 00:00:00', end='2028-07-31 23:59:59',
                shop_name='畔色木作', rate_label='10%')
MODES = ('商品级', 'SKU级')
EXCLUDED_ITEM = '1035582527998'


def validate_terminal(row, raw, wrapper, source_raw):
    """Hashes plus durable finished request/result bind every evidence byte."""
    if (not row or row['id'] != JOB or row['operation'] != 'discount_item_discovery'
            or row['state'] != 'finished' or row['updated_at'] != FINISHED_AT):
        raise ValueError('exact_finished_807_terminal_required_no_restart')
    request = json.loads(row['request'])
    payload = request['payload']
    if (row['request_sha'] != REQUEST_SHA
            or fingerprint(dict(operation=row['operation'], request=request)) != REQUEST_SHA
            or request.get('action_id') != JOB or request.get('request_sha') != PAYLOAD_SHA
            or fingerprint(payload) != PAYLOAD_SHA or payload.get('identity') != IDENTITY
            or tuple(payload.get('items', [])) != ITEMS or payload.get('batch_read') is not True
            or payload.get('price_window') != WINDOW or payload.get('read_request_id') != READ_ID):
        raise ValueError('exact_original_request_scope_or_window_changed')
    skus = payload.get('sku_scope', {})
    if (set(skus) != set(ITEMS) or sum(map(len, skus.values())) != 102
            or any(not v or len(set(v)) != len(v) for v in skus.values())):
        raise ValueError('exact_102_sku_request_required')
    data = json.loads(raw)
    durable = json.loads(row['result'] or '{}')
    if (sha(raw) != RESULT_SHA or data.get('schema') != 'discount-batch-read-v2'
            or data.get('state') != 'readback' or data.get('platform_write') is not False
            or data.get('business_acceptance') is not False or data.get('automatic_retry') is not False
            or data.get('issues') != [] or data.get('source_meta') is not None
            or data.get('amount_scope') != 'overlapping_requested_window_only'
            or any(durable.get(k) != v for k, v in data.items())
            or any(data.get(k) != payload[k] for k in ('items', 'sku_scope', 'price_window', 'read_request_id'))):
        raise ValueError('saved_result_not_bound_to_exact_finished_job')
    wjob = wrapper.get('job', {})
    if (sha(source_raw) != SOURCE_SHA or wrapper.get('source_sha256') != SOURCE_SHA
            or Path(wrapper.get('source', '')).resolve() != SOURCE.resolve()
            or wrapper.get('stage') != 'terminal' or wrapper.get('platform_write') is not False
            or wrapper.get('request') != dict(step='discount_item_discovery', payload=payload)
            or wjob.get('job_id') != JOB or wjob.get('state') != 'finished'
            or wjob.get('operation') != 'discount_item_discovery' or wjob.get('result') != durable):
        raise ValueError('source_or_original_dispatch_receipt_changed')
    return data, payload, durable


def checked_lists(data, payload, check):
    lists = coverage(data, payload, check)
    if set(lists) != {(i, mode) for i in ITEMS for mode in MODES}:
        raise ValueError('all_16_lists_required_no_partial_release')
    failed = {i + '/' + mode: v.get('error') for (i, mode), v in lists.items() if not v['complete']}
    if failed:
        raise ValueError('incomplete_page_evidence:' + json.dumps(failed, ensure_ascii=False))
    return lists


def terminal():
    path = ROOT/JOB/'discount-batch-read.json'
    raw = pinned(path, RESULT_SHA)
    wrapper_raw = WRAPPER.read_bytes()
    source_raw = pinned(SOURCE, SOURCE_SHA)
    db = readonly(ROOT/'jobs.sqlite')
    try:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?', (JOB,)).fetchone()
        data, payload, durable = validate_terminal(row, raw, json.loads(wrapper_raw), source_raw)
        recording = durable.get('recording') or {}
        video = Path(recording.get('video') or '').resolve(strict=True)
        if (Path(durable.get('evidence_path', '')).resolve() != path.resolve()
                or not video.is_relative_to((ROOT/JOB/'recording').resolve())
                or recording.get('active') is not False or recording.get('frames', 0) <= 0
                or recording.get('capture_errors') != 0 or recording.get('error')
                or recording.get('video_sha256') != VIDEO_SHA):
            raise ValueError('original_recording_or_evidence_path_changed')
        pinned(video, VIDEO_SHA)
        lists = checked_lists(data, payload, load_checker())
        if path.read_bytes() != raw or WRAPPER.read_bytes() != wrapper_raw:
            raise ValueError('source_changed_during_local_read')
        return payload, lists, dict(job_id=JOB, finished_at=FINISHED_AT,
            request_sha256=REQUEST_SHA, payload_sha256=PAYLOAD_SHA, result_path=str(path),
            result_sha256=RESULT_SHA, source_path=str(SOURCE), source_sha256=SOURCE_SHA,
            dispatch_receipt_path=str(WRAPPER), dispatch_receipt_sha256=sha(wrapper_raw),
            video_sha256=VIDEO_SHA, coverage_recomputed=True,
            observation_is_historical_snapshot=True, current_platform_state_claimed=False)
    finally:
        db.close()


def authority_snapshot():
    # Do not call Authority.__init__: it installs tables/writes metadata.
    from campaign_entry_authority import Authority, MANIFEST, STATE
    a = object.__new__(Authority)
    config_raw = MANIFEST.read_bytes()
    a.config = json.loads(config_raw.decode('utf-8-sig'))
    a.db = readonly(STATE)
    try:
        a.db.execute('BEGIN')
        offers = a.discount_offers()
        attempts = [dict(r) for r in a.db.execute("SELECT id,bundle_id,campaign,phase,start,end,item,status FROM attempts WHERE phase='discount'")]
        before = fingerprint(dict(offers=offers, attempts=attempts))
        return offers, attempts, dict(path=str(STATE), snapshot_sha256=before,
            manifest_sha256=sha(config_raw), read_only=True, snapshot_transaction=True)
    finally:
        a.close()


def project(payload, lists, offers, attempts, protected_pairs):
    """Separate snapshot membership, historical outcome and downstream holds.

    Never return a filtered authority-offer set or a reusable availability grant.
    Unknown remains held EVEN when a known offer ID is absent in both lists.
    """
    if tuple(payload['items']) != ITEMS or payload['price_window'] != WINDOW:
        raise ValueError('projection_exact_scope_only')
    if set(lists) != {(i, m) for i in ITEMS for m in MODES} or not all(v['complete'] for v in lists.values()):
        raise ValueError('projection_complete_lists_only')
    protected_pairs = set(map(tuple, protected_pairs))
    rows = []
    for item in ITEMS:
        observed = [o for mode in MODES for o in lists[item, mode]['offers']]
        overlaps = [deepcopy(o) for o in observed if o['start'] <= WINDOW['end'] and WINDOW['start'] <= o['end']]
        history = []
        for offer in offers:
            if not (offer['start'] <= WINDOW['end'] and WINDOW['start'] <= offer['end']):
                continue
            platform = str(offer.get('platform_offer_id') or offer['offer_id'])
            for member in offer['items']:
                if member['item'] != item or member['status'] not in ('success', 'unknown'):
                    continue
                old_absent = (platform in KNOWN_OLD_IDS
                    and dict(start=offer['start'], end=offer['end']) == OLD_WINDOW
                    and not any(o['offer_id'] == platform for o in observed))
                unresolved = member['status'] == 'unknown' or not old_absent
                reason = ('historical_unknown_not_resolved_by_list_absence' if member['status'] == 'unknown'
                          else 'known_old_member_absent_in_both_complete_snapshot_lists' if old_absent
                          else 'success_protected_without_exact_local_absence')
                history.append(dict(offer_id=offer['offer_id'], platform_offer_id=platform if platform.isdigit() else None,
                    window=dict(start=offer['start'], end=offer['end']), historical_status=member['status'],
                    member_absent_in_bound_snapshot=old_absent, global_deleted=False,
                    unresolved_history_hold=unresolved, reason=reason,
                    original_claims=[r['id'] for r in attempts if r['item'] == item and
                        offer['offer_id'] == 'bundle:' + r['bundle_id'] and r['status'] == member['status']],
                    affected_historical_skus=sorted({r['sku'] for r in offer.get('rows', []) if r['item'] == item}),
                    history_unchanged=True, original_submission_replay_allowed=False))
        pair_holds = sorted([sku for i, sku in protected_pairs if i == item and sku in payload['sku_scope'][item]])
        reasons = []
        if overlaps: reasons.append('observed_offer_overlap_requires_resolution')
        if any(h['unresolved_history_hold'] for h in history): reasons.append('historical_success_or_unknown_still_protected')
        if pair_holds: reasons.append('official_327_candidate_scope_still_protected')
        # Price composition and current-state acceptance are not provided by this read.
        rows.append(dict(item=item, sku_scope=payload['sku_scope'][item], observed_offers=observed,
            observed_overlapping_offers=overlaps, snapshot_no_overlap=not overlaps,
            history=history, protected_327_skus=pair_holds, remaining_history_or_overlap_reasons=reasons,
            local_overlap_review_clear=not reasons, price_composition_verified=False,
            upload_ready=False, next_gate='effective_sku_price_composition_and_existing_submission_protection'))
    return dict(schema='campaign-local-eight-offer-projection-v1', purpose='bounded_offline_evidence_review_only',
        identity=deepcopy(IDENTITY), price_window=deepcopy(WINDOW), items=list(ITEMS), sku_count=102,
        rows=rows, counts=dict(items=8, skus=102, verified_lists=16,
            snapshot_no_overlap_items=sum(r['snapshot_no_overlap'] for r in rows),
            known_old_absent_members=sum(h['member_absent_in_bound_snapshot'] for r in rows for h in r['history']),
            unknown_held_items=sum(any(h['historical_status'] == 'unknown' for h in r['history']) for r in rows),
            local_overlap_review_clear_items=sum(r['local_overlap_review_clear'] for r in rows)),
        excluded_items=[dict(item=EXCLUDED_ITEM, reason='user_reported_delisted', independently_verified=False,
            restore_listing_allowed=False, reenroll_allowed=False)],
        protected_other_scope=dict(old_327_unchanged=True, two_tables_9_unchanged=True,
            prices_or_claims_modified=False, unlisted_scope_not_released=True),
        platform_write=False, database_write=False, browser_jobs_started=0, automatic_retry=False,
        availability_ttl_changed=False, global_deleted=False, upload_ready=False,
        workbook_generated=False, business_acceptance=False)


def build():
    payload, lists, evidence = terminal()
    offers, attempts, ledger = authority_snapshot()
    from campaign_replacement_audit import load_current_protection
    protection = load_current_protection()
    result = project(payload, lists, offers, attempts, protection['protected_pairs'])
    result.update(terminal=evidence, authority_snapshot=ledger,
        official_327_protection=dict(offer_id=protection['offer_id'], count=len(protection['protected_pairs']),
            scope_basis=protection['scope_basis'], source_validation_passed=True))
    return result


def report_text(result):
    lines = ['# 国庆8件本地证据适配结果', '',
        '这是本地证据消费结果，不是新报名成功、实时价格验收或可上传材料。',
        '只读取既有807d6fd终态，观察完成于北京时间2026-09-29 01:08；未重读平台。',
        '精确窗口：2026-09-28 00:00:00—2026-10-07 19:59:59。',
        '16组列表完整；“当时未显示成员”不等于历史提交失败或优惠已全局删除。', '',
        '|商品ID|原始SKU数|窗口重叠优惠|仍受保护的历史记录|', '|---|---:|---:|---|']
    for row in result['rows']:
        holds = ['{} / {} / claim {}'.format(h['platform_offer_id'] or h['offer_id'], h['historical_status'],
                 ','.join(h['original_claims']) or '无单独claim') for h in row['history'] if h['unresolved_history_hold']]
        if row['protected_327_skus']: holds.append('327保护SKU：' + ','.join(row['protected_327_skus']))
        lines.append('|{}|{}|{}|{}|'.format(row['item'], len(row['sku_scope']), len(row['observed_overlapping_offers']),
            '；'.join(holds) or '本地重叠审查无未解历史项；仍不是价格或上传放行'))
    lines += ['', '## 价格纠正尚缺的证据',
        '按精确商品/SKU及规格，集中提供同一时点/窗口下的真实优惠前有效价、官方立减、其他优惠及叠加顺序、实际原单品优惠ID/金额和最终展示价，并绑定冻结ERP目标价格版本。',
        '商品一口价、旧模板参考价、ERP日常价不能自动代替有效优惠前价格；单条床截图不能推广50条SKU。',
        '现有replacement/rotation入口已拒绝无证据G回退；原位amend/cost-revision入口绑定旧活动，不可改ID挪用。此次不新增平台改价入口。', '',
        '## 保留与排除', '原unknown、旧327、两桌9保护不变；不重制、不重传、不撤出、不改变通用30分钟时效。',
        '黑胡桃软包床1035582527998按用户报告已下架排除；尚未独立核实，不恢复上架，不重报。',
        '完整逐商品历史优惠、claim及SKU范围见result.json。其余未覆盖记录不因本报告而解除保护。']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        raise ValueError('output_exists_do_not_overwrite')
    result = build()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    with (args.output_dir/'集中处理说明.md').open('x', encoding='utf-8') as stream:
        stream.write(report_text(result))
    print(json.dumps(dict(counts=result['counts'], output=str(args.output_dir.resolve()),
        upload_ready=False, platform_write=False, database_write=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
