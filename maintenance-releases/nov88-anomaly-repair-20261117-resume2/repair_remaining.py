"""Resume only the three unresolved original records; four successes are immutable."""
import asyncio
import re
import sys
from pathlib import Path
from decimal import Decimal
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).parent))
import repair_core as c
import repair_runtime as original
import repair_resume as prior
from app.engine.campaign_discount_page import DiscountPage
from app.engine.campaign_price_warning import ordinary_warning

ROOT=c.ROOT/'resume-2'
LIGHT='1038087212258';LIFT='793202812082';STONE='918340407291'
SUCCESSES=(prior.FIRST,'717418169535','717434309002','717809819543')
require=original.require

class ReadyDiscount(DiscountPage):
    def __init__(self,*a,evidence,**kw):
        super().__init__(*a,**kw);self.evidence=Path(evidence);self.stable_read_drawer=True;self.notice_count=0

    async def close_balance_notice(self):
        notices=self.page.locator('[class^="notify_body__"]:visible').filter(has_text='重要消息')
        if not await notices.count():return
        require(await notices.count()==1,'balance_notice_not_unique')
        text=await notices.inner_text()
        if not reviewed_balance_notice(text):return
        control=notices.locator('[class^="notify_head__"] i.next-icon-close_blod')
        require(await control.count()==1 and await control.is_visible(),'balance_notice_close_not_unique')
        self.notice_count+=1
        c.save(self.evidence/('balance-notice-'+str(self.notice_count)+'.json'),dict(text=text,action='close_native_notice_only',financial_action=False))
        await control.click(timeout=3000);await notices.wait_for(state='hidden',timeout=3000)

    async def edit_action(self,item,offer_id):
        await self.close_balance_notice()
        return await super().edit_action(item,offer_id)

    async def guard(self):
        url=urlsplit(self.page.url)
        require(url.scheme=='https' and url.hostname in ('myseller.taobao.com','qn.taobao.com') and not url.username and not url.password and url.port in (None,443) and url.path.endswith('/mkt-tools-reduce/home'),'single_discount_page_identity_mismatch')
        text=await self.page.locator('body').inner_text(timeout=3000)
        if re.search(r'扫码登录|请先登录|安全验证|拖动.{0,12}滑块|输入验证码',text):raise original.SurfaceError('human_login_or_security_gate')
        await self.close_balance_notice()
        if '远程模块加载失败' in text and re.search(r'Loading chunk \d+ failed',text):
            require(not self.frontend_reloaded and not self.attempted_items and not await self.page.locator('.tbd-drawer-body:visible,.next-dialog:visible,[role="dialog"]:visible').count(),'official_frontend_chunk_failed_no_repeat')
            self.frontend_reloaded=True
            c.save(self.evidence/'frontend-load-failure.json',dict(text=text,url=self.page.url,platform_write=False))
            await self.pace();await self.page.reload(wait_until='domcontentloaded',timeout=15000)
            await asyncio.sleep(2.5)
        await super().guard()

def reviewed_balance_notice(text):
    return bool('预警通知' in text and '万相台无界版账户余额不足800.00元' in text and not re.search(r'扫码登录|请先登录|安全验证|身份验证|验证码|滑块|修改密码|支付密码',text))

def original_state():
    request=c.validate_request();plan=c.pinned(c.load(c.ROOT/'prepare/terminal.json')['plan'])
    terminal=c.load(c.ROOT/'resume-1/apply/terminal.json')
    require(terminal['state']=='stopped_unknown_no_replay' and terminal['completed_items']==list(SUCCESSES[1:]),'remaining_predecessor_changed')
    require(terminal['failed_items']==[dict(item=LIGHT,reason='surface_not_unique:0',state='blocked_before_write'),dict(item=LIFT,reason='resume_readback_record_not_effective',state='unknown_no_replay')],'remaining_failure_set_changed')
    require(not list((c.ROOT/'resume-1/apply'/LIGHT).rglob('write-intent.json')),'light_prior_write_exists')
    require(not (c.ROOT/'resume-1/apply'/STONE).exists(),'stone_prior_attempt_exists')
    export=c.load(c.ROOT/'resume-1/apply/final-inventory/result.json')
    rows=c.export_rows(export['file']);c.verify_export(export,rows)
    records={r['item']:r for r in request['records']}
    for item in SUCCESSES:
        own=[r for r in rows if r['item']==item]
        expected={r['sku']:c.fmt(Decimal(r['activity_price'])) for r in records[item]['rows']}
        for d in plan['decisions']:
            if d['item']==item and d['action']=='original_activity_price':expected[d['sku']]=d['new_price']
        require(len(own)==len(expected) and {r['sku']:c.fmt(Decimal(r['activity_price'])) for r in own}==expected and all(r['marketing_id']==records[item]['marketing_id'] and r['state']=='活动中' for r in own),'prior_success_not_fully_confirmed')
        for d in plan['decisions']:
            if d['item']==item and d['action']=='original_activity_price':
                require(Decimal(next(r for r in own if r['sku']==d['sku'])['coupon_price'])==Decimal(d['final']),'prior_success_coupon_changed')
    lift=c.load(c.ROOT/'resume-1/apply'/LIFT/'original/write-intent.json')
    expected={r['sku']:c.fmt(Decimal(r['activity_price'])) for r in records[LIFT]['rows']}
    for d in plan['decisions']:
        if d['item']==LIFT and d['action']=='original_activity_price':expected[d['sku']]=d['new_price']
    require(lift['after']==expected and lift['authorization']==c.AUTH and lift['marketing_id']==records[LIFT]['marketing_id'],'lift_pending_plan_changed')
    for name in ('submission-claimed.json','final-submit-claimed.json'):
        claimed=c.load(c.ROOT/'resume-1/apply'/LIFT/'original'/name)
        require(claimed['expected']==expected and claimed['authorization']==c.AUTH,'lift_original_submit_changed')
    require(not list((c.ROOT/'resume-1/apply'/LIFT/'original').glob('price-risk-confirmation*.json')),'lift_warning_already_attempted')
    own=[r for r in rows if r['item']==LIFT]
    require(len(own)==len(lift['before']) and {r['sku']:c.fmt(Decimal(r['activity_price'])) for r in own}==lift['before'] and all(r['marketing_id']==records[LIFT]['marketing_id'] for r in own),'lift_original_saved_state_changed')
    return request,plan,rows,lift

def checked(request_sha):
    release=c.load(ROOT/'release.json')
    require(release['request_sha256']==request_sha,'remaining_request_changed')
    for ref in release['files']:require(c.sha(ref['path'])==ref['sha256'],'remaining_dependency_changed:'+Path(ref['path']).name)
    binding=c.pinned(release['binding'])
    host=c.B/'Web-Agent程序/app/engine/campaign_nov88_anomaly.py'
    for name in ('original','resume1'):
        previous=c.pinned(binding[name+'_release'])
        for ref in previous['files']:
            path=binding[name+'_host']['path'] if Path(ref['path'])==host else ref['path']
            require(c.sha(path)==ref['sha256'],'remaining_predecessor_dependency_changed:'+Path(ref['path']).name)
    for ref in binding['evidence']:require(c.sha(ref['path'])==ref['sha256'],'remaining_evidence_changed')
    c.validate_request(request_sha,fresh=True)
    request,plan,rows,lift=original_state()
    capture=c.pinned(plan['capture']);offers=c.pinned(plan['offers'])
    issues=[x for r in capture['rows'] if r['state']=='official_errors_read' for x in r['issues']]
    actual=c.plan_rows(c.pinned(request['snapshot']),c.export_rows(request['inventory']),c.pinned(request['bases']),issues,offers)
    require(all(actual[k]==plan[k] for k in ('decisions','exceptions','summary')),'remaining_original_plan_changed')
    groups={item:[d for d in plan['decisions'] if d['item']==item and d['action'] in ('original_discount','original_activity_price')] for item in (STONE,LIGHT,LIFT)}
    require([len(groups[i]) for i in (STONE,LIGHT,LIFT)]==[1,7,1],'remaining_scope_changed')
    require(binding['remaining_items']==[STONE,LIGHT,LIFT] and binding['inherited_successes']==list(SUCCESSES),'remaining_bound_scope_changed')
    return request,plan,rows,lift,groups

def pending_seed(folder,record,after):
    require(Path(folder)==ROOT/'apply'/LIFT/'original' and record['item']==LIFT,'pending_seed_exact_original_required')
    proof=c.load(ROOT/'diagnose/lift/pending.json')
    require(proof['marketing_id']==record['marketing_id'] and prior.prices(proof['inventory'])==after and proof['ordinary_warning']=='商品设价风险提醒','pending_seed_proof_changed')
    claim=c.load(Path(folder)/'write-intent.json')
    require(claim['action']=='continue_existing_ordinary_warning_only' and claim['original_final_submit']==c.reference(c.ROOT/'resume-1/apply'/LIFT/'original/final-submit-claimed.json'),'pending_seed_claim_changed')
    require(not (Path(folder)/'final-submit-claimed.json').exists(),'pending_seed_must_not_repeat_final_submit')

def accepted(request_sha):
    doc=c.load(ROOT/'inspect/terminal.json')
    require(doc['state']=='ready_readonly' and doc['request_sha256']==request_sha and doc['release']==c.reference(ROOT/'release.json'),'remaining_inspection_not_accepted')
    return doc

async def pending_page(session,record,expected):
    pages=[p for p in session.context.pages if not p.is_closed() and getattr(p,'_completion_editor_owned',None)==dict(item=LIFT,marketing_id=record['marketing_id'])]
    require(len(pages)==1,'lift_original_pending_page_unavailable')
    page=pages[0]
    io=original.EnamelPageIO(page,c.IDENTITY,roots=[],output=ROOT/'diagnose/lift');io.late_handlers=[]
    await io.guard()
    body=await page.locator('body').inner_text(timeout=4000)
    exact=bool(re.search(r'商品\s*ID\s*[:：]?\s*'+LIFT+r'(?!\d)',body) and re.search(r'营销\s*ID\s*[:：]?\s*'+record['marketing_id']+r'(?!\d)',body))
    require(ordinary_warning(body,exact=exact,bound_target=record)=='商品设价风险提醒','lift_pending_ordinary_warning_changed')
    buttons=[n for n in await page.get_by_role('button',name='确认无风险，继续提交',exact=True).all() if await n.is_visible()]
    require(len(buttons)==1,'lift_pending_confirmation_not_unique')
    observed=await original.inventory(page,expected)
    require(prior.prices(observed)==expected,'lift_pending_full_prices_changed')
    before=c.load(c.ROOT/'resume-1/apply'/LIFT/'original/before/editor.json')
    others={r['sku_id']:r['other_inputs'] for r in before['rows']}
    require(all(r['other_inputs']==others[r['sku_id']] for r in observed['rows']),'lift_pending_other_inputs_changed')
    return page,io,dict(text=body,inventory=observed,item=LIFT,marketing_id=record['marketing_id'],ordinary_warning='商品设价风险提醒',platform_write=False)

async def diagnose(session,request_sha):
    c.validate_request(request_sha);request,plan,rows,lift=original_state()
    records={r['item']:r for r in request['records']};result=dict(platform_write=False,confirmed_successes=list(SUCCESSES))
    async with session.lock:
        try:
            page,io,proof=await pending_page(session,records[LIFT],lift['after'])
            c.save(ROOT/'diagnose/lift/pending.json',proof)
            result['lift']='original_warning_pending_original_prices_unchanged'
        except Exception as exc:result['lift_error']=str(exc)
        page=None
        try:
            from app.engine.uploader import SINGLE_ITEM_HOME_URL
            page=await session.context.new_page()
            await page.goto(SINGLE_ITEM_HOME_URL,wait_until='domcontentloaded',timeout=30000)
            driver=ReadyDiscount(page,shop=c.IDENTITY['shop_name'],**c.WINDOW,evidence=ROOT/'diagnose/light')
            observed=await driver.read(item=LIGHT,offer_id='153606663877',sku_ids=[r['sku'] for r in records[LIGHT]['rows']])
            old={d['sku']:d['old_deduct'] for d in plan['decisions'] if d['item']==LIGHT and d['action']=='original_discount'}
            require(all(Decimal(observed['values'][k])==Decimal(v) for k,v in old.items()),'light_original_discount_changed')
            c.save(ROOT/'diagnose/light/readback.json',observed)
            result['light']='original_offer_seven_sku_read_verified'
        except Exception as exc:result['light_error']=str(exc)
        finally:
            if page and not any(w in page.url for w in ('login','captcha','punish')):await page.close()
        result['state']='diagnosed' if 'lift' in result and 'light' in result else 'partial'
        c.save(ROOT/'diagnose/terminal.json',result)
    return result

async def inspection(session,request_sha):
    request,plan,rows,lift,groups=checked(request_sha)
    record=next(r for r in request['records'] if r['item']==LIGHT)
    folder=ROOT/'inspect';page=None
    result=dict(state='blocked',request_sha256=request_sha,release=c.reference(ROOT/'release.json'),platform_write=False)
    async with session.lock:
        try:
            from app.engine.uploader import SINGLE_ITEM_HOME_URL
            page=await session.context.new_page();await page.goto(SINGLE_ITEM_HOME_URL,wait_until='domcontentloaded',timeout=30000)
            driver=ReadyDiscount(page,shop=c.IDENTITY['shop_name'],**c.WINDOW,evidence=folder)
            observed=await driver.read(item=LIGHT,offer_id='153606663877',sku_ids=[r['sku'] for r in record['rows']])
            require(all(Decimal(observed['values'][d['sku']])==Decimal(d['old_deduct']) for d in groups[LIGHT] if d['action']=='original_discount'),'light_original_discount_changed')
            c.save(folder/'light-readback.json',observed)
            result.update(state='ready_readonly',light_full_membership=observed,filled=False,submitted=False)
        except Exception as exc:
            result['reason']=str(exc)
            if page:c.save(folder/'failed-surface.json',dict(url=page.url,text=(await page.locator('body').inner_text())[-16000:]))
        finally:
            if page and not any(x in page.url for x in ('login','captcha','punish')):await page.close()
            c.save(folder/'terminal.json',result)
    return result

async def continue_lift(session,record,folder,validator):
    from remaining_submit import submit_once
    validator();_,_,_,lift=original_state()
    page,io,proof=await pending_page(session,record,lift['after'])
    folder.mkdir(parents=True)
    c.save(folder/'pending-live.json',proof)
    c.save(folder/'write-intent.json',dict(action='continue_existing_ordinary_warning_only',item=LIFT,marketing_id=record['marketing_id'],original_final_submit=c.reference(c.ROOT/'resume-1/apply'/LIFT/'original/final-submit-claimed.json'),state='unknown_no_replay'))
    recorder=original.RetainedCampaignRecorder(page,root=folder/'recording',duration=360);await recorder.start();saved=False
    try:
        try:
            await submit_once(page,io,folder,lift['before'],lift['after'],bound_target=record,scope_validator=validator,authorization=c.AUTH,pending_final=True)
        except original.SurfaceError as exc:
            if str(exc) in ('official_sku_price_validation_rejected','official_activity_price_constraint_rejected','official_sales_admission_notice_blocks_edit','official_form_submission_failed'):
                result=dict(state='official_rejected',item=LIFT,reason=str(exc),automatic_retry=False)
                c.save(folder/'terminal.json',result);return result
            if str(exc)!='restore62_form_stage_or_submission_unknown':raise
        check,_,observed,status=await prior.open_readback(session,record,folder/'readback')
        try:
            require(prior.prices(observed)==lift['after'],'lift_continued_prices_not_saved')
            others={r['sku_id']:r['other_inputs'] for r in proof['inventory']['rows']}
            require(all(r['other_inputs']==others[r['sku_id']] for r in observed['rows']),'lift_continued_other_fields_changed')
            result=dict(state='saved_prices_verified',status=status,prices=lift['after'],continued_original_warning_only=True,no_refill=True,no_final_submit_replay=True,platform_write=True)
            c.save(folder/'terminal.json',result);saved=True;return result
        finally:await check.close()
    finally:
        c.save(folder/'recording-result.json',await recorder.stop())
        if saved:await page.close()

async def amend_discount(session,record,changes,folder,validator):
    from app.engine.uploader import SINGLE_ITEM_HOME_URL
    ids={r['offer_id'] for r in changes};require(len(ids)==1,'anomaly_multiple_offer_groups')
    offer=next(iter(ids));folder.mkdir(parents=True)
    page=await session.context.new_page();rec=original.RetainedCampaignRecorder(page,root=folder/'recording',duration=360,pending_navigation_url=SINGLE_ITEM_HOME_URL);await rec.start()
    try:
        await page.goto(SINGLE_ITEM_HOME_URL,wait_until='domcontentloaded',timeout=30000)
        driver=ReadyDiscount(page,shop=c.IDENTITY['shop_name'],**c.WINDOW,evidence=folder)
        scope=[r['sku'] for r in record['rows']]
        before=await driver.read(item=record['item'],offer_id=offer,sku_ids=scope);c.save(folder/'before.json',before)
        wanted={r['sku']:r['new_deduct'] for r in changes};old={r['sku']:r['old_deduct'] for r in changes}
        async def claim(values,window):
            validator()
            require(all(window.get(k)==v for k,v in dict(c.WINDOW,offer_id=offer).items()),'anomaly_discount_window_changed')
            c.save(folder/'write-intent.json',dict(item=record['item'],offer_id=offer,old=old,wanted=wanted,authorization=c.AUTH,state='unknown_no_replay'))
            return True
        saved=await driver.amend_once(item=record['item'],offer_id=offer,expected_old=old,wanted=wanted,verify_claim=claim)
        after=await driver.read(item=record['item'],offer_id=offer,sku_ids=scope)
        expected={s:c.fmt(Decimal(v)) for s,v in before['values'].items()};expected.update(wanted)
        require({s:c.fmt(Decimal(v)) for s,v in after['values'].items()}==expected,'anomaly_unrelated_discount_changed')
        require(before.get('not_enrolled',[])==after.get('not_enrolled',[]) and before['window']==after['window'],'anomaly_discount_membership_or_window_changed')
        c.save(folder/'terminal.json',dict(state='saved_verified',saved=saved,full_readback=after,platform_write=True))
    finally:
        c.save(folder/'recording-result.json',await rec.stop())
        # Keep an uncertain submitted editor visible for reconciliation.
        if (folder/'terminal.json').exists() or not (folder/'write-intent.json').exists():await page.close()

async def amend_original(session, record, changes, folder, validator):
    from remaining_submit import submit_once
    require(record['item'] in (LIGHT,STONE), 'resume_item_not_authorized')
    folder.mkdir(parents=True)
    page, io, observed, status = await original.open_editor(session, record, folder/'before')
    recorder = original.RetainedCampaignRecorder(page, root=folder/'recording', duration=360)
    await recorder.start(); saved=False
    try:
        before = prior.prices(observed)
        require(before == {r['sku']:c.fmt(Decimal(r['activity_price'])) for r in record['rows']}, 'anomaly_original_price_baseline_changed')
        after = dict(before)
        for change in changes:
            if change['action']=='original_activity_price': after[change['sku']]=change['new_price']
        for sku, value in after.items():
            if value != before[sku]:
                field = await original.exact_field(page, sku)
                await io._pace('fill:authorized-anomaly-price'); await field.fill(value, timeout=4000); await field.press('Tab')
        filled = await original.inventory(page, after)
        require(prior.prices(filled)==after, 'anomaly_filled_price_mismatch')
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
        check, _, readback, new_status = await prior.open_readback(session, record, folder/'readback')
        try:
            require(prior.prices(readback)==after, 'anomaly_original_saved_prices_unknown')
            require(all(r['other_inputs']==other[r['sku_id']] for r in readback['rows']), 'anomaly_saved_unrelated_fields_changed')
            result = dict(state='saved_prices_verified', prices=after, status=new_status, platform_write=True, original_marketing_id=record['marketing_id'], needs_final_export=True)
            c.save(folder/'terminal.json', result); saved=True
            return result
        finally: await check.close()
    finally:
        c.save(folder/'recording-result.json', await recorder.stop())
        if saved or not (folder/'write-intent.json').exists(): await page.close()

async def apply(session,request_sha):
    request,plan,before,lift,groups=checked(request_sha);accepted(request_sha)
    records={r['item']:r for r in request['records']};root=ROOT/'apply'
    result=dict(state='stopped_preserve_checkpoints',inherited_completed_items=list(SUCCESSES),request_sha256=request_sha,automatic_retry=False)
    completed=[];failures=[];validator=lambda:checked(request_sha)
    async with session.lock:
        try:
            for item,changes in groups.items():
                folder=root/item;folder.mkdir(parents=True)
                try:
                    async with asyncio.timeout(900):
                        if item==LIFT:outcome=await continue_lift(session,records[item],folder/'original',validator)
                        else:
                            discounts=[r for r in changes if r['action']=='original_discount']
                            if discounts:await amend_discount(session,records[item],discounts,folder/'discount',validator)
                            outcome=await amend_original(session,records[item],changes,folder/'original',validator)
                        if outcome['state']=='official_rejected':failures.append(outcome)
                        else:completed.append(item)
                except Exception as exc:
                    attempts=list(folder.rglob('write-intent.json'));reason=str(exc) or type(exc).__name__
                    failures.append(dict(item=item,reason=reason[:600],state='unknown_no_replay' if attempts else 'blocked_before_write'))
                    if attempts or any(w in reason.lower() for w in ('login','security','human_','captcha','punish')):break
            gate=any(any(w in f['reason'].lower() for w in ('login','security','human_','captcha','punish')) for f in failures)
            if any(root.rglob('write-intent.json')) and not gate:
                async with asyncio.timeout(480):
                    exported,rows=await original.final_export(session,dict(request,remaining_binding=c.reference(ROOT/'binding.json')),root/'final-inventory')
                # Latest official checkpoint already has all 505 coupon cells;
                # old blank cells must not be treated as immutable zero prices.
                audit=c.audit(before,rows,plan);c.save(root/'audit.json',audit)
                result.update(final_export=exported,audit=c.reference(root/'audit.json'),full_store_verified=audit['all_verified'],all_final_prices_known=audit['all_final_prices_known'])
                result['state']='resolved_with_small_table_exceptions' if len(completed)==3 and not failures and audit['all_verified'] else 'verification_incomplete'
            if failures:result['state']='stopped_unknown_no_replay' if any(f.get('state')=='unknown_no_replay' for f in failures) else 'finished_with_failures'
        except Exception as exc:result.update(state='stopped_preserve_checkpoints',reason=str(exc) or type(exc).__name__)
        finally:
            result.update(completed_items=completed,failed_items=failures,exceptions=plan['exceptions'],deferred=[d for d in plan['decisions'] if d['action']=='deferred_same_record_exception'],platform_write=any(root.rglob('write-intent.json')))
            c.save(root/'terminal.json',result)
    return result

async def handle(session,operation,options):
    require(set(options)=={'request_sha256'},'remaining_exact_options_required')
    request_sha=options['request_sha256'];c.validate_request(request_sha)
    stage=operation.removeprefix('program_nov88_anomaly_remaining_')
    require(stage in ('diagnose','inspect','verify','status','apply'),'remaining_stage_not_supported')
    if stage=='status':
        task=getattr(session,'rotation_workflow_task',None)
        return dict(ok=True,active=bool(task and not task.done()),started=(ROOT/'apply/intent.json').exists(),terminal=c.load(ROOT/'apply/terminal.json') if (ROOT/'apply/terminal.json').exists() else None)
    if stage=='verify':
        checked(request_sha);accepted(request_sha)
        return dict(ok=True,ready=True,remaining_items=[STONE,LIGHT,LIFT],inherited_successes=list(SUCCESSES),release=c.reference(ROOT/'release.json'),platform_write=False)
    folder=ROOT/stage
    if (folder/'terminal.json').exists():return dict(ok=True,already_completed_no_replay=True,terminal=c.load(folder/'terminal.json'))
    if (folder/'intent.json').exists():return dict(ok=True,state='already_started_no_replay')
    from app.engine.campaign_enamel88_reason_read import idle
    idle(session);require(session.context is not None,'dedicated_browser_not_connected')
    if stage!='diagnose':checked(request_sha)
    if stage=='apply':accepted(request_sha)
    c.save(folder/'intent.json',dict(request_sha256=request_sha,automatic_retry=False,release=c.reference(ROOT/'release.json') if stage!='diagnose' else None))
    async def run():
        try:await {'diagnose':diagnose,'inspect':inspection,'apply':apply}[stage](session,request_sha)
        except Exception as exc:
            if not (folder/'terminal.json').exists():c.save(folder/'terminal.json',dict(state='stopped_preserve_checkpoints',reason=str(exc),platform_write=any(folder.rglob('write-intent.json')),automatic_retry=False))
    session.rotation_workflow_job='nov88-remaining-'+stage
    session.rotation_workflow_task=asyncio.create_task(run())
    return dict(ok=True,state='running',terminal=str(folder/'terminal.json'))
