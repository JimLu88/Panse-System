"""Exact continuation of the original Nov88 attempt; confirmed writes never repeat."""
import asyncio
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import repair_core as c
import repair_runtime as original
from app.engine.campaign_colleague_enrollment import EDITOR_SHAPE, DOUBLE_ROWS

ROOT = c.ROOT / 'resume-1'
FIRST = '1036273574687'
REMAINING = ('1038087212258', '717418169535', '717434309002', '717809819543', '793202812082', '918340407291')
# Read disabled fields as values only. Never remove disabled/readonly or fill them.
READ_ROWS = DOUBLE_ROWS.replace('vis(e)&&!e.disabled&&', 'vis(e)&&')
require = original.require

def prices(observed):
    rows = observed['rows']
    result = {r['sku_id']:c.fmt(Decimal(r['price'])) for r in rows}
    require(len(result) == len(rows), 'resume_duplicate_sku')
    return result

def confirmed_first():
    request = c.validate_request()
    plan = c.pinned(c.load(c.ROOT/'prepare/terminal.json')['plan'])
    prior = c.load(c.ROOT/'apply/terminal.json')
    original.require(prior['failed_items'] == [dict(item=FIRST, reason='original_editor_step_not_ready', state='unknown_no_replay')], 'resume_original_failure_changed')
    folder = c.ROOT/'apply'/FIRST/'original'
    intent = c.load(folder/'write-intent.json')
    submitted = c.load(folder/'submission-claimed.json')
    final = c.load(folder/'final-submit-claimed.json')
    record = next(r for r in request['records'] if r['item'] == FIRST)
    original.require(intent['marketing_id'] == record['marketing_id'] and intent['after'] == submitted['expected'] == final['expected'], 'resume_submit_identity_or_price_changed')
    original.require(intent['authorization'] == submitted['authorization'] == final['authorization'] == c.AUTH, 'resume_authorization_changed')
    exported = c.load(c.ROOT/'apply/final-inventory/result.json')
    rows = c.export_rows(exported['file']); c.verify_export(exported, rows)
    own = [r for r in rows if r['item'] == FIRST]
    expected = {r['sku']:c.fmt(Decimal(r['activity_price'])) for r in record['rows']}
    for d in plan['decisions']:
        if d['item'] == FIRST and d['action'] == 'original_activity_price':
            expected[d['sku']] = d['new_price']
            row = next(r for r in own if r['sku'] == d['sku'])
            original.require(row['coupon_price'] and Decimal(row['coupon_price']) == Decimal(d['final']), 'resume_first_coupon_not_confirmed')
    original.require(expected == intent['after'], 'resume_first_scope_or_plan_changed')
    original.require(len(own) == len(expected) and {r['sku']:c.fmt(Decimal(r['activity_price'])) for r in own} == expected, 'resume_first_prices_not_confirmed')
    original.require(all(r['marketing_id'] == record['marketing_id'] and r['state'] == '活动中' for r in own), 'resume_first_mid_status_not_confirmed')
    listed = c.load(folder/'readback/row.json')['rows']
    original.require(len(listed) == 1 and FIRST in listed[0]['text'] and record['marketing_id'] in listed[0]['text'] and listed[0]['cells'][-2] == '活动中', 'resume_first_list_not_confirmed')
    return dict(item=FIRST, marketing_id=record['marketing_id'], state='official_export_confirmed', prices=expected,
                sku_count=len(own), platform_write=False, no_resubmission=True, export=exported['file'])

def remaining_groups(plan):
    groups = {}
    for d in plan['decisions']:
        if d['action'] in ('original_discount', 'original_activity_price') and d['item'] != FIRST:
            groups.setdefault(d['item'], []).append(d)
    require(tuple(groups) == REMAINING and sum(map(len, groups.values())) == 13, 'resume_remaining_scope_changed')
    return groups

def checked(request_sha, *, fresh=True):
    release = c.load(ROOT/'release.json')
    require(release['request_sha256'] == request_sha, 'resume_release_request_changed')
    for ref in release['files']:
        require(c.sha(ref['path']) == ref['sha256'], 'resume_release_source_changed:'+Path(ref['path']).name)
    binding = c.pinned(release['binding'])
    old_release = c.pinned(binding['original_release'])
    host = str(c.B/'Web-Agent程序/app/engine/campaign_nov88_anomaly.py')
    for ref in old_release['files']:
        path = binding['original_host']['path'] if Path(ref['path']) == Path(host) else ref['path']
        require(c.sha(path) == ref['sha256'], 'resume_original_dependency_changed:'+Path(ref['path']).name)
    for ref in binding['evidence']:
        require(c.sha(ref['path']) == ref['sha256'], 'resume_original_evidence_changed')
    request = c.validate_request(request_sha, fresh=fresh)
    require(old_release['request_sha256'] == request_sha, 'resume_original_request_changed')
    plan = c.pinned(c.load(c.ROOT/'prepare/terminal.json')['plan'])
    require(plan['request_sha256'] == request_sha and plan['authorization'] == c.AUTH, 'resume_plan_binding_changed')
    capture = c.pinned(plan['capture']); offers = c.pinned(plan['offers'])
    issues = [d for r in capture['rows'] if r['state']=='official_errors_read' for d in r['issues']]
    recomputed = c.plan_rows(c.pinned(request['snapshot']), c.export_rows(request['inventory']), c.pinned(request['bases']), issues, offers)
    require(all(recomputed[k] == plan[k] for k in ('decisions','exceptions','summary')), 'resume_plan_decisions_changed')
    proof = confirmed_first()
    require(proof == c.pinned(binding['confirmed_first']), 'resume_first_confirmation_changed')
    groups = remaining_groups(plan)
    require(list(groups) == binding['remaining_items'], 'resume_bound_scope_changed')
    require(not any((c.ROOT/'apply'/item).exists() for item in groups), 'resume_original_remaining_attempt_exists')
    return request, plan, proof, groups

def accepted_inspection(request_sha):
    inspected=c.load(ROOT/'inspect/terminal.json')
    require(inspected['state']=='readonly_saved_prices_verified' and inspected['submitted'] is False,
            'resume_readback_not_accepted')
    require(inspected['request_sha256']==request_sha and inspected['release']==c.reference(ROOT/'release.json'),
            'resume_readback_release_changed')
    return inspected

async def readonly_inventory(page, expected):
    merged = {}
    for step in range(60):
        for row in (await page.evaluate(READ_ROWS))['rows']:
            if row['sku_id'] in merged:
                require(row['price'] == merged[row['sku_id']]['price'] and row['other_inputs'] == merged[row['sku_id']]['other_inputs'], 'resume_readback_changed_during_paging')
            merged[row['sku_id']] = row
        require(set(merged) <= set(expected), 'resume_readback_unexpected_sku')
        if set(merged) == set(expected):
            return {'rows':list(merged.values())}
        await page.evaluate("f=>{for(const e of document.querySelectorAll('*')){if(e.scrollHeight>e.clientHeight+80&&['auto','scroll'].includes(getComputedStyle(e).overflowY))e.scrollTop=(e.scrollHeight-e.clientHeight)*f;}}", (step%9)/8)
        await asyncio.sleep(.4)
    raise original.SurfaceError('resume_readback_incomplete_sku_scope')

async def open_readback(session, record, folder):
    page = await session.context.new_page()
    try:
        io, row, observed = await original.locate(page, record, folder)
        status = observed['row']['cells'][-2].strip()
        require(status in ('活动中','已发布设定','已生效','审核通过'), 'resume_readback_record_not_effective')
        control = await original.row_control(page, row, '编辑商品')
        require(control is not None, 'resume_readback_edit_missing')
        page._completion_editor_owned = dict(item=record['item'], marketing_id=record['marketing_id'])
        await control.click(timeout=5000)
        deadline = asyncio.get_running_loop().time()+15
        previous_clicked = False
        while not (await page.evaluate(READ_ROWS))['rows']:
            await io.guard()
            previous = [n for n in await page.get_by_role('button', name='上一步', exact=True).all() if await n.is_visible()]
            require(len(previous)<=1, 'resume_readback_previous_not_unique')
            if previous and not previous_clicked:
                await previous[0].click(timeout=5000); previous_clicked=True
            require(asyncio.get_running_loop().time()<deadline, 'resume_readback_step_not_ready')
            await asyncio.sleep(.2)
        readback = await readonly_inventory(page, {r['sku'] for r in record['rows']})
        c.save(folder/'editor.json', readback)
        c.save(folder/'editor-shape.json', await page.evaluate(EDITOR_SHAPE))
        return page, io, readback, status
    except Exception as exc:
        if not any(x in str(exc).lower() or x in page.url for x in ('login','security','human_','captcha','punish')):
            await page.close()
        raise

async def inspect_readback(session, request_sha):
    request, plan, proof, groups = checked(request_sha)
    record = next(r for r in request['records'] if r['item']==FIRST)
    folder = ROOT/'inspect'
    result = dict(state='blocked', platform_write=False, request_sha256=request_sha, release=c.reference(ROOT/'release.json'))
    page = None
    async with session.lock:
        try:
            page, io, readback, status = await open_readback(session, record, folder/'readback')
            require(prices(readback) == proof['prices'], 'resume_inspection_saved_prices_mismatch')
            before = c.load(c.ROOT/'apply'/FIRST/'original/before/editor.json')
            others = {r['sku_id']:r['other_inputs'] for r in before['rows']}
            require(all(r['other_inputs']==others[r['sku_id']] for r in readback['rows']), 'resume_inspection_other_fields_changed')
            result.update(state='readonly_saved_prices_verified', item=FIRST, sku_count=len(readback['rows']),
                          status=status, disabled_prices_read=True, fields_filled=False, submitted=False)
        except Exception as exc:
            result['reason'] = str(exc)
        finally:
            if page: await page.close()
            c.save(folder/'terminal.json', result)
    return result

async def amend_original(session, record, changes, folder, validator):
    from app.engine.campaign_completion_submit import submit_once
    require(record['item'] in REMAINING, 'resume_item_not_authorized')
    folder.mkdir(parents=True)
    page, io, observed, status = await original.open_editor(session, record, folder/'before')
    recorder = original.RetainedCampaignRecorder(page, root=folder/'recording', duration=360)
    await recorder.start(); saved=False
    try:
        before = prices(observed)
        require(before == {r['sku']:c.fmt(Decimal(r['activity_price'])) for r in record['rows']}, 'anomaly_original_price_baseline_changed')
        after = dict(before)
        for change in changes:
            if change['action']=='original_activity_price': after[change['sku']]=change['new_price']
        for sku, value in after.items():
            if value != before[sku]:
                field = await original.exact_field(page, sku)
                await io._pace('fill:authorized-anomaly-price'); await field.fill(value, timeout=4000); await field.press('Tab')
        filled = await original.inventory(page, after)
        require(prices(filled)==after, 'anomaly_filled_price_mismatch')
        other = {r['sku_id']:r['other_inputs'] for r in observed['rows']}
        require(all(r['other_inputs']==other[r['sku_id']] for r in filled['rows']), 'anomaly_unrelated_form_fields_changed')
        validator()
        c.save(folder/'write-intent.json', dict(item=record['item'], marketing_id=record['marketing_id'], before=before, after=after, authorization=c.AUTH, state='unknown_no_replay'))
        try:
            await submit_once(page, io, folder, before, after, bound_target=record, scope_validator=validator, authorization=c.AUTH)
        except original.SurfaceError as exc:
            if str(exc) in ('official_sku_price_validation_rejected','official_activity_price_constraint_rejected','official_sales_admission_notice_blocks_edit','official_form_submission_failed'):
                result = dict(state='official_rejected', reason=str(exc), item=record['item'], marketing_id=record['marketing_id'], saved_prices_verified=False, automatic_retry=False)
                c.save(folder/'terminal.json', result); await page.close(); return result
            if str(exc)!='restore62_form_stage_or_submission_unknown': raise
            require((folder/'final-submit-claimed.json').exists() or (folder/'price-risk-confirmation.json').exists(), 'anomaly_submission_not_proven')
        check, _, readback, new_status = await open_readback(session, record, folder/'readback')
        try:
            require(prices(readback)==after, 'anomaly_original_saved_prices_unknown')
            require(all(r['other_inputs']==other[r['sku_id']] for r in readback['rows']), 'anomaly_saved_unrelated_fields_changed')
            result = dict(state='saved_prices_verified', prices=after, status=new_status, platform_write=True, original_marketing_id=record['marketing_id'], needs_final_export=True)
            c.save(folder/'terminal.json', result); saved=True
            return result
        finally: await check.close()
    finally:
        c.save(folder/'recording-result.json', await recorder.stop())
        if saved or not (folder/'write-intent.json').exists(): await page.close()

async def apply(session, request_sha):
    request, plan, proof, groups = checked(request_sha)
    accepted_inspection(request_sha)
    root = ROOT/'apply'; completed=[]; failures=[]
    records = {r['item']:r for r in request['records']}
    result = dict(state='stopped_preserve_checkpoints', request_sha256=request_sha, inherited_completed_items=[FIRST], automatic_retry=False)
    validator = lambda: checked(request_sha)
    async with session.lock:
        try:
            for item, changes in groups.items():
                folder=root/item
                # Exclusive directory creation also stops direct in-process re-entry.
                folder.mkdir(parents=True)
                try:
                    async with asyncio.timeout(900):
                        discounts=[r for r in changes if r['action']=='original_discount']
                        if discounts: await original.amend_discount(session, records[item], discounts, folder/'discount', validator)
                        outcome=await amend_original(session, records[item], changes, folder/'original', validator)
                        if outcome['state']=='official_rejected': failures.append(outcome)
                        else: completed.append(item)
                except Exception as exc:
                    attempts=list(folder.rglob('write-intent.json')); reason=str(exc) or type(exc).__name__
                    failures.append(dict(item=item, reason=reason[:600], state='unknown_no_replay' if attempts else 'blocked_before_write'))
                    if attempts or any(w in reason.lower() for w in ('login','security','human_','captcha','punish')): break
            human_gate=any(any(w in f['reason'].lower() for w in ('login','security','human_','captcha','punish')) for f in failures)
            if any(root.rglob('write-intent.json')) and not human_gate:
                export_request=dict(request, continuation_binding=c.reference(ROOT/'binding.json'))
                async with asyncio.timeout(480):
                    exported, rows=await original.final_export(session, export_request, root/'final-inventory')
                audit=c.audit(c.export_rows(request['inventory']), rows, plan); c.save(root/'audit.json', audit)
                scope_rows=[r for r in audit['rows'] if r['item'] in (*REMAINING,FIRST)]
                scope_verified=all(r['verified'] for r in scope_rows)
                result.update(final_export=exported, audit=c.reference(root/'audit.json'), resumed_scope_verified=scope_verified,
                              full_store_verified=audit['all_verified'], all_final_prices_known=audit['all_final_prices_known'])
                result['state']='resumed_scope_verified_with_known_exceptions' if len(completed)==6 and not failures and scope_verified and audit['all_verified'] else 'verification_incomplete'
            if failures:
                result['state']='stopped_unknown_no_replay' if any(f.get('state')=='unknown_no_replay' for f in failures) else 'finished_with_failures'
        except Exception as exc:
            result.update(state='stopped_preserve_checkpoints', reason=str(exc) or type(exc).__name__)
        finally:
            result.update(completed_items=completed, failed_items=failures, exceptions=plan['exceptions'],
                          deferred=[d for d in plan['decisions'] if d['action']=='deferred_same_record_exception'],
                          platform_write=any(root.rglob('write-intent.json')))
            c.save(root/'terminal.json', result)
    return result

async def diagnose(session, request_sha):
    request = c.validate_request(request_sha)
    proof = confirmed_first()
    folder = ROOT/'diagnose'; folder.mkdir(parents=True, exist_ok=True)
    result = dict(platform_write=False, confirmed_first=proof)
    record = next(r for r in request['records'] if r['item'] == FIRST)
    page = None
    async with session.lock:
        try:
            page = await session.context.new_page()
            io, row, observed = await original.locate(page, record, folder/'list')
            control = await original.row_control(page, row, '编辑商品')
            original.require(control is not None, 'resume_diagnosis_edit_missing')
            await control.click(timeout=5000)
            try:
                await original.ready_editor(page)
                result['inventory'] = await original.inventory(page, proof['prices'])
                result['state'] = 'readback_ready'
            except Exception as exc:
                result.update(state='readback_blocked', reason=str(exc))
            c.save(folder/'shape.json', await page.evaluate(EDITOR_SHAPE))
            await page.screenshot(path=str(folder/'page.png'), full_page=False)
        finally:
            if page and not any(x in page.url for x in ('login','punish','captcha')):
                await page.close()
            c.save(folder/'terminal.json', result)
    return result

async def handle(session, operation, options):
    original.require(set(options) == {'request_sha256'}, 'exact_resume_options_required')
    request_sha = options['request_sha256']; c.validate_request(request_sha)
    stage = operation.removeprefix('program_nov88_anomaly_resume_')
    original.require(stage in ('diagnose','inspect','verify','apply','status'), 'unsupported_resume_stage')
    if stage=='status':
        task=getattr(session,'rotation_workflow_task',None)
        return dict(ok=True, active=bool(task and not task.done()),
                    terminal=c.load(ROOT/'apply/terminal.json') if (ROOT/'apply/terminal.json').exists() else None,
                    started=(ROOT/'apply/intent.json').exists(), platform_write=False)
    if stage=='verify':
        checked(request_sha)
        accepted_inspection(request_sha)
        return dict(ok=True, ready=True, remaining_items=list(REMAINING), inherited_completed_items=[FIRST],
                    release=c.reference(ROOT/'release.json'), platform_write=False)
    folder = ROOT/stage
    if (folder/'terminal.json').exists():
        return dict(ok=True, terminal=c.load(folder/'terminal.json'), already_completed_no_replay=True)
    if (folder/'intent.json').exists():
        return dict(ok=True, state='already_started_no_replay')
    from app.engine.campaign_enamel88_reason_read import idle
    idle(session)
    original.require(session.context is not None, 'dedicated_browser_not_connected')
    if stage in ('inspect','apply'): checked(request_sha)
    if stage=='apply':
        accepted_inspection(request_sha)
    c.save(folder/'intent.json', dict(request_sha256=request_sha, stage=stage, automatic_retry=False,
                                    release=c.reference(ROOT/'release.json') if stage!='diagnose' else None))
    async def run():
        try:
            action={'diagnose':diagnose,'inspect':inspect_readback,'apply':apply}[stage]
            await action(session, request_sha)
        except Exception as exc:
            if not (folder/'terminal.json').exists():
                c.save(folder/'terminal.json', dict(state='stopped_preserve_checkpoints', reason=str(exc),
                       platform_write=any(folder.rglob('write-intent.json')), automatic_retry=False))
    session.rotation_workflow_job = 'nov88-anomaly-resume-'+stage
    session.rotation_workflow_task = asyncio.create_task(run())
    return dict(ok=True, state='running', terminal=str(folder/'terminal.json'))
