"""Offline consumer of the pinned super43 batch-resume-1 terminal result.

Invoked ONLY through campaign_cap_batch.py prepare. No job dispatch, network,
authority claims, uploads or platform edits. Existing enrollment is NOT replayed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

from campaign_generate_current_files import build_rows, official_cut
from campaign_official_template import fill_single_discount_rows, money

PROJECT = Path('D:/AI/畔色ERP系统')
JOB = '39c2c97ed748184efc921985ea9837f4a72f4990e8619f2f69b71f17c5b91bea'
SOURCE_SHA = '996ff2b1dcd9ad6df35330bfecf78151bc740af3be440f0fcccd272544e9d486'
REQUEST_SHA = '10e3743ac0e67538a0bf72eb54ab421efe236bf03b7e8a35f1f3f5e3749fd682'
CAMPAIGN = 'legacy/itemApply/3172207691'
WINDOW = {'start':'2026-09-28 00:00:00','end':'2026-09-30 23:59:59'}
TZ = timezone(timedelta(hours=8))
INPUTS = {
    'calculation': ('outputs/01a067c6-7e83-7483-9a21-84b44ed7299b/super43-closeout-20260927/calculation.json', '99a191d8f43f433039e9a53d1aa5ef727171469269305c4b55d7658965742495'),
    'inventory': ('outputs/01a067c6-7e83-7483-9a21-84b44ed7299b/super43-closeout-20260927/live-analysis.json', '73d644bbe59b613e01a0aa4caf197f65f4a2ddec45fc3c724a196977ceba9faa'),
    'snapshot': ('outputs/custom-correspondence-20260927/03-post-correction-snapshot.json', '76dfa053fa0170cc54d9bcdbcffe670d9e32dfc225f0251983c1d0c6559ca68c'),
    'request': ('outputs/campaign-cap-batch-20260927/installed-replay/batch-read-request.json', '85a7d8917faad4dea7723ec594f91e0a0738716c3419d2340b246c43810b5d57'),
    'discount_master': ('活动准备/固定模板/单品立减-SKU级-固定官方模板.xlsx', 'dae4da7f875398c7cc99226e129d1211cb849a012d4670a66df88459860f117f'),
    'activity_master': ('outputs/campaign-continuous/master-replacement-20260911/超级立减长期活动-固定官方母版.xlsx', 'be5ae804a19ccf987381ec0d2dab76e374ede9e780c1cc338ae19084574e4e83'),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def pinned(path, expected):
    raw = Path(path).read_bytes()
    if sha(raw) != expected:
        raise ValueError('source_changed:' + str(path))
    return raw


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def validate_terminal(row, audit, raw, original_raw, expected_payload):
    """Bind exact durable terminal, request, original evidence and one resume."""
    if not row or row['state'] != 'finished':
        raise ValueError('original_read_not_terminal_do_not_restart_or_poll')
    request = json.loads(row['request'])
    payload = request['payload']
    if (row['id'] != JOB or row['operation'] != 'discount_item_discovery'
            or row['request_sha'] != REQUEST_SHA
            or fingerprint(dict(operation=row['operation'], request=request)) != REQUEST_SHA
            or request.get('action_id') != JOB or request.get('request_sha') != fingerprint(payload)
            or payload != expected_payload or payload['price_window'] != WINDOW):
        raise ValueError('original_request_identity_scope_or_window_changed')
    if (not audit or audit['source_sha256'] != SOURCE_SHA
            or audit['previous_request'] != row['request'] or sha(original_raw) != SOURCE_SHA):
        raise ValueError('original_resume_audit_or_source_changed')
    previous = json.loads(audit['previous_result'])
    original = json.loads(original_raw)
    if any(previous.get(k) != v for k,v in original.items()):
        raise ValueError('original_source_differs_from_durable_receipt')
    data = json.loads(raw)
    durable = json.loads(row['result'] or '{}')
    if (data.get('schema') != 'discount-batch-read-v2'
            or data.get('state') not in ('readback','partial_readback')
            or data.get('platform_write') is not False
            or any(durable.get(k) != v for k,v in data.items())
            or any(data.get(k) != payload[k] for k in ('items','sku_scope','price_window','read_request_id'))):
        raise ValueError('terminal_file_not_bound_to_finished_job')
    meta = data.get('source_meta') or {}
    plan = json.loads(audit['plan'])
    for key,value in {'job_id':JOB, 'source_sha256':SOURCE_SHA,
                      'original_request_sha':REQUEST_SHA, 'price_window':WINDOW}.items():
        if meta.get(key) != value or plan.get(key) != value:
            raise ValueError('terminal_resume_provenance_changed')
    return data


def terminal(root, payload):
    # Read once; never wait, dispatch, inspect a browser, or consume a running file.
    db = readonly(root/'jobs.sqlite')
    try:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(JOB,)).fetchone()
        if not row or row['state'] != 'finished':
            raise ValueError('original_read_not_terminal_do_not_restart_or_poll')
        audit = db.execute('SELECT * FROM campaign_discount_batch_read_resume WHERE id=?',(JOB,)).fetchone()
        path = root/JOB/'batch-resume-1/discount-batch-read.json'
        raw = path.read_bytes()
        data = validate_terminal(row,audit,raw,(root/JOB/'discount-batch-read.json').read_bytes(),payload)
        if path.read_bytes() != raw:
            raise ValueError('terminal_file_changed_during_read')
        return data, dict(path=str(path),sha256=sha(raw),job_id=JOB,finished_at=row['updated_at'])
    finally:
        db.close()


def load_checker():
    # Reuse only the existing stdlib-only offline validator, not browser modules.
    path = PROJECT/'Web-Agent程序/app/engine/campaign_discount_item_coverage.py'
    spec = importlib.util.spec_from_file_location('offline_campaign_item_coverage',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def coverage(data, payload, check):
    """Recompute every full page list; flags/empty arrays are never evidence."""
    lists, seen = {}, set()
    for entry in data.get('rows',[]):
        key = entry.get('item'),entry.get('mode')
        if key in seen or key[0] not in payload['items'] or key[1] not in ('商品级','SKU级'):
            raise ValueError('terminal_list_scope_duplicate_or_foreign')
        seen.add(key)
        try:
            pages = entry.get('pages') or []
            if not pages:
                raise ValueError('missing_pages')
            offers, ids = [], set()
            for n,page in enumerate(pages,1):
                if (page['item'],page['mode']) != key:
                    raise ValueError('page_scope_changed')
                verified = check(page,**payload['price_window'],page_number=n,page_total=len(pages))
                for offer in verified['offers']:
                    if offer['offer_id'] in ids:
                        raise ValueError('repeated_offer_across_pages')
                    ids.add(offer['offer_id']); offers.append(offer)
            lists[key] = {'complete':True,'offers':offers}
        except (ValueError,KeyError,TypeError) as exc:
            lists[key] = {'complete':False,'error':str(exc),'offers':[]}
    return lists


def future_window(start, now):
    value = datetime.strptime(start,'%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
    if value.strftime('%Y-%m-%d %H:%M:%S') != start or now.tzinfo is None:
        raise ValueError('exact_beijing_activation_time_required')
    if not WINDOW['start'] <= start <= WINDOW['end'] or value <= now:
        raise ValueError('activation_must_be_future_inside_verified_window_no_backdate')
    return dict(start=start,end=WINDOW['end'])


def custom_price(row, basis):
    if row.get('current_price') in (None,'') or row.get('min_final') in (None,''):
        raise ValueError('custom_current_price_or_cap_missing')
    current,cap = money(row['current_price']),money(row['min_final'])
    if current <= 0 or cap <= 0:
        raise ValueError('custom_current_price_or_cap_missing')
    if current-official_cut(current,Decimal('.10')) <= cap:
        return None  # No new custom discount and no forced price reset.
    if not basis or basis.get('uncertain') or not basis.get('source'):
        raise ValueError('fixed_custom_original_basis_missing')
    original,floor = money(basis['original']),Decimal(str(basis['floor']))
    if not floor.is_finite() or original <= 0 or floor != original*Decimal('.20'):
        raise ValueError('fixed_custom_floor_changed')
    price = (cap/Decimal('.90')).quantize(Decimal('.01'),rounding=ROUND_DOWN)
    if price < floor:
        raise ValueError('custom_below_first_original_twenty_percent_requires_rotation')
    if price > current or price-official_cut(price,Decimal('.10')) > cap:
        raise ValueError('custom_correction_formula_invalid')
    return dict(activity_price=str(price),original=str(original),floor=str(floor),basis=basis)


def partition(identities, activity, discounts, errors, lists, protected, offers, window):
    """Every source SKU receives one outcome; local problems don't suppress peers."""
    by_a = {(r['item'],r['sku']):r for r in activity}
    by_d = {(r['item'],r['sku']):r for r in discounts}
    by_e = {(r['item'],r['sku']):r for r in errors}
    results, ready, actions = [], [], []
    for source in identities:
        pair = source['item'],source['sku']
        item,sku = pair
        r = dict(item=item,sku=sku,product=source.get('product'),spec=source.get('spec'),
                 marketing_id=source.get('marketing_id'),current_activity_price=source.get('current_price'),
                 current_final=source.get('current_final'),platform_cap=source.get('min_final'),issues=[])
        if not source.get('marketing_id') or source.get('state') != '异常':
            r['issues'].append('existing_abnormal_enrollment_identity_required_no_new_signup')
        a,d,e = by_a.get(pair),by_d.get(pair),by_e.get(pair)
        if e:
            r['issues'].append(e['error']); r['calculation_issue']=e
        if not a and not e:
            r['issues'].append('sku_mapping_or_scope_excluded_requires_review')
        complete = all(lists.get((item,m),{}).get('complete') is True for m in ('商品级','SKU级'))
        overlap = [o for m in ('商品级','SKU级') for o in lists.get((item,m),{}).get('offers',[])
                   if o['start'] <= window['end'] and window['start'] <= o['end']]
        # Never interpret an incomplete list, missing amount, paused/unknown offer,
        # or a historic successful claim as proof of no active overlapping discount.
        if not complete:
            r['issues'].append('both_complete_offer_lists_missing')
        old = [o for o in offers if o['start'] <= window['end'] and window['start'] <= o['end']
               and any(v['item']==item and v['status'] in ('success','unknown') for v in o['items'])]
        if overlap:
            r['issues'].append('overlapping_offer_requires_exact_resolution_before_import')
            r['overlapping_offers']=[{k:o[k] for k in ('offer_id','mode','name','start','end','status','deduction_summary')} for o in overlap]
        if item in protected or old:
            r['issues'].append('registered_success_unknown_or_inflight_do_not_replay')
            r['registered_offers']=[dict(offer_id=o.get('platform_offer_id',o['offer_id']),start=o['start'],end=o['end']) for o in old]
        if a:
            r['custom']=a['custom']
            try:
                if not a['custom'] and source.get('current_price') in (None,''):
                    raise ValueError('ordinary_current_activity_price_missing')
                change = custom_price(source,a.get('custom_basis')) if a['custom'] else (
                    dict(activity_price=a['activity_price']) if money(source['current_price']) != money(a['activity_price']) else None)
                if change and source.get('marketing_id') and source.get('state') == '异常':
                    action=dict(item=item,sku=sku,marketing_id=source.get('marketing_id'),
                        old_price=source.get('current_price'),new_price=change['activity_price'],
                        custom=a['custom'],action='modify_existing_activity_price_in_place_not_reenroll',
                        import_supported=False,requires_readback_before_discount=True,details=change)
                    actions.append(action);r['price_change']=action
                    r['issues'].append('existing_activity_price_must_be_corrected_in_place')
            except (ValueError,TypeError,ArithmeticError) as exc:
                r['issues'].append(str(exc))
        if d:
            r['calculated_discount']=d
        if r['issues']:
            r['disposition']='待处理'
        elif a['custom']:
            r['disposition']='定制不加单品立减'
        elif d:
            r['disposition']='可生成单品立减';ready.append(d)
        else:
            r['disposition']='无需单品立减'
        results.append(r)
    if len({(r['item'],r['sku']) for r in results}) != len(identities):
        raise ValueError('source_partition_lost_or_duplicated_sku')
    return dict(rows=results,discount_rows=ready,price_changes=actions,
                counts=dict(Counter(r['disposition'] for r in results)),
                issue_counts=dict(Counter(e for r in results for e in r['issues'])),
                signup_rows=[],signup_reason='全部为既有异常报名记录；不得作为新报名重传。价格修改须原位操作，当前没有已验证的批量修改导入能力。')


ABSENCE_JOB='e1a092c171a372431ee0023ba27c8cc95e830442d5d43e09bcfe9334d42d3b4c'
ABSENCE_SHA='45513073a59a516489fcc25f68c4158368d74fda7582278d0e8cba546f8700cc'
ABSENCE_REQUEST_SHA='57b566a8e08fcb2b5a920d4c8df0fce22abb4606983fffdca622ac7a204b6f21'
RESUMED_SHA='19ed12a531e5e0f4ff7925699203873d2e25156063635905e17b81a393a128bf'
REMOVED_IDS={'145761399121','145807488351','145812384556'}


def current_batch_absence(payload,receipt):
    """This batch's already completed exact-ID proof, not the old registry alone.

    Does not reinterpret an old 30-minute availability receipt as fresh. Combines
    the pinned Sep27 batch's exact-ID terminal with its own later complete item
    lists. Not a general age bypass or an activity cancellation receipt.
    """
    if receipt['sha256']!=RESUMED_SHA:
        raise ValueError('same_batch_absence_requires_exact_resumed_terminal')
    root=PROJECT/'Web-Agent程序/data/output/campaign-transfers'
    raw=pinned(root/ABSENCE_JOB/'discount-readback.json',ABSENCE_SHA)
    data=json.loads(raw)
    db=readonly(root/'jobs.sqlite')
    try:
        row=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(ABSENCE_JOB,)).fetchone()
        batch=db.execute('SELECT plan FROM campaign_discount_batch_read_resume WHERE id=?',(JOB,)).fetchone()
    finally:db.close()
    if not row or row['state']!='finished' or row['operation']!='discount_readback':
        raise ValueError('same_batch_exact_id_read_not_finished')
    request=json.loads(row['request']);r=request['payload'];result=json.loads(row['result'] or '{}')
    if (row['request_sha']!=ABSENCE_REQUEST_SHA
            or fingerprint(dict(operation=row['operation'],request=request))!=ABSENCE_REQUEST_SHA
            or request['request_sha']!=fingerprint(r)
            or request['action_id']!=ABSENCE_JOB or r.get('offer_status_only') is not True
            or r['identity']!=payload['identity'] or r['price_window']!=WINDOW
            or data['price_window']!=WINDOW or data['shop_name']!='畔色木作'
            or data['read_request_id']!=r['read_request_id']
            or data['state']!='offer_status_readback' or data['platform_write'] is not False
            or any(result.get(k)!=v for k,v in data.items())):
        raise ValueError('same_batch_exact_id_identity_or_receipt_changed')
    rows=data['rows']
    if len({x['offer_id'] for x in rows})!=len(rows) or {x['offer_id'] for x in rows}!={x['offer_id'] for x in r['offers']}:
        raise ValueError('same_batch_exact_id_scope_changed')
    accepted={x['offer_id'] for x in rows if x.get('observed_state')=='not_found'
              and x.get('unfiltered_exact_query') is True and x.get('search_value')==x['offer_id']
              and x.get('evidence_kind')=='official_exact_id_empty_result' and x.get('platform_write') is False}
    if accepted!=REMOVED_IDS:raise ValueError('same_batch_exact_absence_not_proven')
    if not batch or datetime.fromisoformat(json.loads(batch['plan'])['original_read_finished_at']) < max(datetime.fromisoformat(x['observed_at']) for x in rows if x['offer_id'] in accepted):
        raise ValueError('same_batch_terminal_must_follow_exact_absence')
    recording=result.get('recording') or {}
    if recording.get('active') is not False or recording.get('frames',0)<=0 or recording.get('capture_errors')!=0 or recording.get('error'):
        raise ValueError('same_batch_exact_id_recording_incomplete')
    video=Path(recording['video']).resolve(strict=True)
    if not video.is_relative_to((root/ABSENCE_JOB/'recording').resolve()):
        raise ValueError('same_batch_recording_path_changed')
    pinned(video,recording['video_sha256'])
    # Already documented user removal instruction is bound to precisely these
    # IDs. It is NOT a current-state proof, and is never used alone to release rows.
    instruction=json.loads(pinned(PROJECT/'outputs/campaign-national-20260920/verified-current-offer-availability-20260921.json',
                                  'ceeff0ddf1b34168f9d0f23c83914a67eafbedb875a9ce3037aa610493b70eb5'))
    if instruction.get('user_removed_old_offers') is not True or set(instruction['offer_ids'])!=accepted:
        raise ValueError('exact_old_offer_removal_instruction_missing')
    return dict(job_id=ABSENCE_JOB,sha256=ABSENCE_SHA,offer_ids=sorted(accepted),
                observed_at={x['offer_id']:x['observed_at'] for x in rows if x['offer_id'] in accepted},
                terminal_sha256=receipt['sha256'],scope='same_super43_prepare_only',platform_write=False)


def filter_batch_inactive(offers,lists,proof):
    remaining,resolved=[],[]
    for offer in offers:
        platform=offer.get('platform_offer_id',offer['offer_id'])
        if platform not in proof['offer_ids'] or dict(start=offer['start'],end=offer['end'])!=WINDOW:
            remaining.append(offer);continue
        kept=[]
        for item in offer['items']:
            evidence=[lists.get((item['item'],mode),{}) for mode in ('商品级','SKU级')]
            if (all(e.get('complete') is True for e in evidence)
                    and all(o['offer_id']!=platform for e in evidence for o in e['offers'])):
                resolved.append(dict(item=item['item'],offer_id=platform,
                    historical_status=item['status'],claim_unchanged=True,
                    reason='same_batch_exact_id_absence_and_complete_later_item_lists'))
            else:kept.append(item)
        if kept:remaining.append(dict(offer,items=kept))
    return remaining,resolved


def authority_state(snapshot,window,*,lists=None,absence=None):
    from campaign_entry_authority import Authority, MANIFEST, STATE
    from campaign_discount_availability import inactive_for_window
    a = Authority.__new__(Authority)
    a.config=json.loads(MANIFEST.read_text(encoding='utf-8-sig'))
    a.db=readonly(STATE)
    try:
        a.db.execute('BEGIN')
        bases=a.bases(snapshot)
        offers=[]
        raw=a.discount_offers()
        resolved=[]
        if absence is not None:raw,resolved=filter_batch_inactive(raw,lists,absence)
        for o in raw:
            try:inactive=inactive_for_window(o,CAMPAIGN,window['start'],window['end'])
            except ValueError as exc:
                if str(exc)!='offer_availability_readback_stale':raise
                inactive=False  # Stale receipt blocks this offer, not unrelated items.
            if not inactive:offers.append(o)
        # The same filtered offer set owns protection. Re-adding blocked() here
        # would revive an independently verified inactive old ID. History is unchanged.
        protected={v['item']:v['status'] for o in offers
                   if o['start']<=window['end'] and window['start']<=o['end']
                   for v in o['items'] if v['status'] in ('success','unknown')}
        return (bases,protected,offers,resolved) if absence is not None else (bases,protected,offers)
    finally:
        a.close()


def report_text(result):
    lines=['# 超级立减43件集中处理结果','',
           '本地制表，不是报名成功。43件403SKU全部保留在 result.json。活动仍由用户上传。',
           '原只读窗口：'+str(WINDOW),'本次新优惠生效窗口：'+str(result['effective_window']),
           '上传时开始时间必须仍在未来；不能回填已过去的时间。',
           '只上传本目录实际生成的单品立减文件；该文件只包含逐行核验通过且无旧优惠/未知记录的普通SKU。',
           result['signup_reason'],'', '## 统计',json.dumps(result['counts'],ensure_ascii=False),'',
           '## 既有活动价原位修改（不是新报名）',
           '在超级立减长期活动中按商品ID找到对应营销记录，进入修改活动价，按下表精确SKU修改并保存。',
           '若页面不允许原位修改，保留记录并反馈；不得撤销重报。修改后导出结果，核对已保存价格，再生成依赖这些价格的单品立减。',
           '|商品ID|SKU ID|营销ID|原活动价|改为|类型|','|---|---|---|---:|---:|---|']
    for r in result['price_changes']:
        lines.append('|'+ '|'.join(str(r[k]) for k in ('item','sku','marketing_id','old_price','new_price'))+'|'+('定制' if r['custom'] else '普通')+'|')
    lines += ['', '## 旧优惠精确处理清单',
              '下列不是撤出完成回执，也不授权撤销整场。先按活动ID核对商品/SKU范围与起止时间；只处理重叠范围，保存后取得官方读回。',
              '若整场还含其他商品或其他时段，不能删除整场。已成功、未知及在途记录不重传；历史账本与当前列表矛盾时先核清，不能按空列表清除保护。']
    seen=set()
    for r in result['rows']:
        for o in r.get('overlapping_offers',[])+r.get('registered_offers',[]):
            key=(r['item'],o['offer_id'],o['start'],o['end'])
            if key in seen:continue
            seen.add(key)
            affected=[x['sku'] for x in result['rows'] if x['item']==r['item']]
            lines.append(f"- 商品 {r['item']}；优惠 {o['offer_id']}；{o['start']} 至 {o['end']}；SKU核对范围：{','.join(affected)}。列表未证明逐SKU实际加入情况时，不能把核对范围当成已加入范围。")
    lines += ['', '## 全部未解项（按SKU隔离）','|商品ID|SKU ID|问题|','|---|---|---|']
    labels={
        'both_complete_offer_lists_missing':'商品级或SKU级优惠列表未读全，不能断定没有旧优惠',
        'registered_success_unknown_or_inflight_do_not_replay':'历史成功/未知/在途记录仍受保护，先核清，不重传',
        'overlapping_offer_requires_exact_resolution_before_import':'存在重叠优惠，先按精确范围处理并读回',
        'existing_activity_price_must_be_corrected_in_place':'需按上表原位修改现有活动价',
        'current_platform_cap_missing':'缺本场平台卡控价格',
        'custom_current_price_or_cap_missing':'定制SKU缺当前活动价或本场卡控价格',
        'ordinary_current_activity_price_missing':'普通SKU缺平台当前活动价',
        'platform_cap_exceeds_frozen_target_tolerance_requires_rotation':'超过原ERP目标2元，待确认是否轮换',
        'current_export_conflicts_with_erp_binding':'当前导出编码与ERP绑定冲突，不能猜配',
        'fixed_custom_original_basis_missing':'缺明确的首次定制原价基线',
        'custom_below_first_original_twenty_percent_requires_rotation':'定制修正低于固定原价20%，待确认轮换',
    }
    for r in result['rows']:
        if r['issues']:lines.append(f"|{r['item']}|{r['sku']}|{'；'.join(labels.get(e,e) for e in r['issues'])}|")
    return '\n'.join(lines)+'\n'


def prepare(effective_start,output_dir):
    output_dir=Path(output_dir)
    if output_dir.exists():raise ValueError('output_exists_do_not_overwrite')
    window=future_window(effective_start,datetime.now(TZ))
    sources={k:pinned(PROJECT/path,digest) for k,(path,digest) in INPUTS.items()}
    docs={k:json.loads(v) for k,v in sources.items() if not k.endswith('_master')}
    calc,inv,snapshot,payload=docs['calculation'],docs['inventory'],docs['snapshot'],docs['request']['payload']
    if calc['scope'] != payload['items'] or len(calc['scope']) != 43:
        raise ValueError('exact_43_item_scope_required')
    identities=[r for r in inv['rows'] if r['item'] in calc['scope']]
    pairs={(r['item'],r['sku']) for r in identities}
    expected={(i,s) for i,skus in payload['sku_scope'].items() for s in skus}
    if len(identities)!=403 or len(pairs)!=403 or pairs!=expected:
        raise ValueError('exact_403_sku_inventory_required')
    pinned(inv['source']['path'],inv['source']['sha256'])
    for ref in calc['sources']:pinned(ref['path'],ref['sha256'])
    data,receipt=terminal(PROJECT/'Web-Agent程序/data/output/campaign-transfers',payload)
    lists=coverage(data,payload,load_checker())
    absence=current_batch_absence(payload,receipt)
    bases,protected,offers,resolved=authority_state(snapshot,window,lists=lists,absence=absence)
    activity,discounts,errors=build_rows(snapshot,identities,Decimal('.10'),'medium',bases,
        signup_items=set(calc['scope']),discount_items=set(calc['scope']),
        platform_caps={(r['item'],r['sku']):r.get('min_final') for r in identities})
    # Price restoration is still a precise user action when a separate cap
    # blocks the discount; don't silently drop an independently known daily.
    baseline,_,_=build_rows(snapshot,identities,Decimal('.10'),'medium',bases,
        signup_items=set(calc['scope']),discount_items=set())
    by_pair={(r['item'],r['sku']):r for r in baseline}
    by_pair.update({(r['item'],r['sku']):r for r in activity})
    activity=list(by_pair.values())
    result=partition(identities,activity,discounts,errors,lists,protected,offers,window)
    result.update(schema='super43-prepare-only-v1',items=43,skus=403,effective_window=window,
        original_read_window=WINDOW,terminal=receipt,platform_write=False,database_write=False,
        business_acceptance=False,upload_owner='user',partial_terminal=data['state']=='partial_readback',
        verified_lists=sum(r['complete'] for r in lists.values()),
        sources={k:dict(path=str(PROJECT/v[0]),sha256=v[1]) for k,v in INPUTS.items()},files=[],
        same_batch_absence=absence,resolved_old_offer_scope=resolved)
    outputs={}
    if result['discount_rows']:
        outputs['单品立减-已核验无重叠范围.xlsx']=fill_single_discount_rows(sources['discount_master'],result['discount_rows'])
    # All 43 have existing abnormal registrations: intentionally no signup XLSX.
    # Do not use the old master's 575 reference rows as current SKU membership.
    for name,raw in outputs.items():
        result['files'].append(dict(name=name,sha256=sha(raw),skus=len(result['discount_rows']),upload_ready=True))
    future_window(effective_start,datetime.now(TZ))  # Don't hand off already-past activation.
    output_dir.mkdir(parents=True,exist_ok=False)
    for name,raw in outputs.items():
        with (output_dir/name).open('xb') as f:f.write(raw)
    with (output_dir/'result.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    with (output_dir/'集中处理说明.md').open('x',encoding='utf-8') as f:f.write(report_text(result))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--effective-start',required=True,help='Future Beijing time inside the original verified window')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    result=prepare(args.effective_start,args.output_dir)
    print(json.dumps({k:result[k] for k in ('items','skus','counts','issue_counts','verified_lists','files','platform_write','business_acceptance')},ensure_ascii=False))
