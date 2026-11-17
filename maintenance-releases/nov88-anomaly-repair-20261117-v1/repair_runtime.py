"""Retained-session anomaly capture/apply; separate from normal enrollment."""
import asyncio,importlib,json,re,sys
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal

sys.path.insert(0,str(Path(__file__).parent))
import repair_core as c
c=importlib.reload(c)
from app.engine.campaign_page_io import SurfaceError,one_visible
from app.engine.campaign_enamel88_completion import EnamelPageIO
from app.engine.campaign_nov14_restore62 import source_url
from app.engine.campaign_completion_search import inspect
from app.engine.campaign_super_sku_status import row_control
from app.engine.campaign_colleague_enrollment import inventory,ready_editor
from app.recorder.campaign_retained import RetainedCampaignRecorder

def require(value,reason):
    if not value:raise SurfaceError(reason)

async def locate(page,record,folder):
    folder.mkdir(parents=True,exist_ok=True)
    url=source_url('november88');await page.goto(url,wait_until='domcontentloaded',timeout=30000)
    io=EnamelPageIO(page,c.IDENTITY,roots=[],output=folder);io.late_handlers=[]
    observed=await inspect(page,dict(record,identity=c.IDENTITY,open_draft=False),folder,io)
    rows=[r for r in await page.locator('tbody tr').filter(has_text=record['item']).filter(has_text=record['marketing_id']).all() if await r.is_visible() and await r.locator('td').count()>=6]
    require(len(rows)==1,'anomaly_original_row_not_unique')
    row=rows[0];await row.scroll_into_view_if_needed(timeout=5000)
    return io,row,observed

TABLES=r'''e=>{const vis=n=>!!n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';return [...e.querySelectorAll('table')].filter(vis).map(t=>({headers:[...t.querySelectorAll('thead th')].filter(vis).map(n=>n.innerText.trim()),rows:[...t.querySelectorAll('tbody tr')].filter(vis).map(r=>[...r.querySelectorAll(':scope > td')].map(n=>n.innerText.trim())).filter(r=>r.length)}));}'''

async def read_error_panel(page,panel,record,folder):
    all_rows=[];tabs=[t for t in await panel.get_by_role('tab').all() if await t.is_visible()]
    groups=[t for t in tabs if any(s in await t.inner_text() for s in ('券后价','标价'))] if tabs else [None]
    require(bool(groups),'anomaly_supported_error_tabs_missing')
    for index,tab in enumerate(groups):
        if tab and await tab.get_attribute('aria-selected')!='true':await tab.click(timeout=5000);await asyncio.sleep(.5)
        seen={};pages=[];total=None
        for n in range(100):
            deadline=asyncio.get_running_loop().time()+15;previous=None;stable=0
            while True:
                tables=await panel.evaluate(TABLES);text=await panel.inner_text()
                counts=re.findall(r'共\s*(\d+)\s*条(?:数据)?',text)
                signature=c.digest([tables,text])
                stable=stable+1 if signature==previous else 0;previous=signature
                if counts and stable>=2:break
                if asyncio.get_running_loop().time()>=deadline:break
                await asyncio.sleep(.5)
            c.save(folder/('error-observed-'+str(index)+'-'+str(n)+'.json'),dict(tables=tables,text=text))
            current=[]
            for t in tables:
                if not any(re.sub(r'\s+','',h).lower()=='skuid' for h in t['headers']):continue
                current+=c.parse_error_table(t['headers'],t['rows'],record['item'],record['marketing_id'])
            counts=re.findall(r'共\s*(\d+)\s*条(?:数据)?',text)
            page_total=sum(map(int,counts)) if counts else 0 if '没有数据' in text or '暂无数据' in text else None
            require(page_total is not None,'anomaly_error_total_missing')
            if total is None:total=page_total
            require(page_total==total,'anomaly_error_total_changed')
            signature=c.digest(current);require(signature not in [x['signature'] for x in pages],'anomaly_error_page_did_not_advance')
            pages.append(dict(signature=signature,tables=tables,text=text))
            for r in current:
                key=(r['sku'],r['kind'])
                require(key not in seen or seen[key]==r,'anomaly_error_changed_during_paging');seen[key]=r
            if len(seen)==total:break
            require(len(seen)<total,'anomaly_error_count_exceeded')
            nxt=panel.locator('button.next-pagination-item.next-next,button[aria-label="下一页"],li.next-pagination-item.next-next')
            controls=[x for x in await nxt.all() if await x.is_visible() and await x.is_enabled() and 'disabled' not in (await x.get_attribute('class') or '')]
            require(len(controls)==1,'anomaly_error_next_page_unavailable')
            await controls[0].click(timeout=5000);await asyncio.sleep(.5)
        require(total==len(seen),'anomaly_error_pagination_incomplete')
        c.save(folder/('error-pages-'+str(index)+'.json'),dict(pages=pages,total=total,complete=True))
        all_rows+=list(seen.values())
    return all_rows

async def capture_item(session,record,folder):
    folder.mkdir(parents=True,exist_ok=False)
    page=await session.context.new_page();rec=RetainedCampaignRecorder(page,root=folder/'recording',duration=300,pending_navigation_url=source_url('november88'))
    await rec.start();result=dict(item=record['item'],marketing_id=record['marketing_id'],state='blocked',platform_write=False)
    io=None
    try:
        async with asyncio.timeout(240):
            io,row,observed=await locate(page,record,folder/'list')
            status=observed['row']['cells'][-2].strip();result['status']=status
            require(not any(x in status for x in ('撤销','暂停','草稿')),'anomaly_record_not_current')
            control=await row_control(page,row,'查看原因')
            if control is None:
                if '异常' in status:raise SurfaceError('anomaly_reason_control_missing')
                result.update(state='healthy_current_record',issues=[],all_rows=record['rows']);return result
            await control.click(timeout=5000)
            panels=page.locator('.next-dialog:visible,.luna-exception-info-dialog:visible,[role="dialog"]:visible')
            candidates=[]
            for p in await panels.all():
                if '异常提醒' in await p.inner_text() and await p.locator('table').count():candidates.append(p)
            # Nested wrappers may describe the same dialog; choose the outer role root.
            unique=page.get_by_role('dialog').filter(has_text='异常提醒')
            visible=[p for p in await unique.all() if await p.is_visible()]
            panel=visible[0] if len(visible)==1 else candidates[0] if len(candidates)==1 else None
            require(panel is not None,'anomaly_error_panel_not_unique')
            result['issues']=await read_error_panel(page,panel,record,folder)
            require(all(r['sku'] in {x['sku'] for x in record['rows']} for r in result['issues']),'anomaly_error_sku_not_in_original_record')
            result.update(state='official_errors_read',all_rows=record['rows'])
    except Exception as exc:result['reason']=str(exc)[:500]
    finally:
        if io:
            for handler in getattr(io,'late_handlers',[]):await page.remove_locator_handler(handler)
        result['recording']=await rec.stop();c.save(folder/'terminal.json',result)
        if not any(x in result.get('reason','').lower() for x in ('login','security','human_')):await page.close()
    return result

async def capture(session,request_sha):
    request=c.validate_request(request_sha);root=c.ROOT/'capture';result=dict(state='partial',platform_write=False,request_sha256=request_sha)
    async with session.lock:
        rows=[]
        try:
            for record in request['records']:
                target=root/record['item'];existing=target/'terminal.json'
                observed=c.load(existing) if existing.exists() else await capture_item(session,record,target)
                rows.append(observed)
                if any(x in observed.get('reason','').lower() for x in ('login','security','human_')):break
            result.update(rows=rows,state='captured' if len(rows)==len(request['records']) and all(r['state'] in ('official_errors_read','healthy_current_record') for r in rows) else 'partial')
        except Exception as exc:result['reason']=str(exc)[:500]
        finally:c.save(root/'terminal.json',result)
    return result

async def capture_resume(session,request_sha):
    request=c.validate_request(request_sha);binding=c.load(c.ROOT/'capture-recovery-binding.json')
    require(binding['request_sha256']==request_sha,'anomaly_capture_resume_request_changed')
    old=c.pinned(binding['original']);allowed={'anomaly_error_total_missing','anomaly_error_next_page_unavailable'}
    prior={r['item']:r for r in old['rows']};root=c.ROOT/'capture-recovery-1';rows=[]
    async with session.lock:
        for record in request['records']:
            original=prior[record['item']]
            if original['state'] in ('official_errors_read','healthy_current_record'):rows.append(original);continue
            require(original.get('reason') in allowed and original['platform_write'] is False,'anomaly_capture_resume_not_loading_stop')
            rows.append(await capture_item(session,record,root/record['item']))
        result=dict(state='captured' if all(r['state'] in ('official_errors_read','healthy_current_record') for r in rows) else 'partial',rows=rows,platform_write=False,request_sha256=request_sha,original_capture=binding['original'])
        c.save(root/'terminal.json',result);return result

def capture_path():
    recovery=c.ROOT/'capture-recovery-1/terminal.json'
    return recovery if recovery.exists() else c.ROOT/'capture/terminal.json'

async def capture_offers(session,request,issues,root):
    from app.engine.campaign_discount_batch_read import read
    from app.engine.uploader import SINGLE_ITEM_HOME_URL
    scope={}
    for r in issues:scope.setdefault(r['item'],set()).add(r['sku'])
    scope={k:sorted(v) for k,v in scope.items()}
    if not scope:return dict(rows=[],platform_write=False,coverage_verified=True)
    page=await session.context.new_page();rec=RetainedCampaignRecorder(page,root=root/'recording',duration=1800,pending_navigation_url=SINGLE_ITEM_HOME_URL);await rec.start()
    try:
        await page.goto(SINGLE_ITEM_HOME_URL,wait_until='domcontentloaded',timeout=30000)
        payload=dict(identity=c.IDENTITY,items=sorted(scope),sku_scope=scope,price_window=c.WINDOW,read_request_id=c.digest(['nov88-anomaly-offers',request['snapshot'],scope]))
        result=await read(page,payload,root)
        c.save(root/'terminal.json',result);return result
    finally:
        c.save(root/'recording-result.json',await rec.stop())
        await page.close()

async def prepare(session,request_sha):
    request=c.validate_request(request_sha,fresh=True);root=c.ROOT/'prepare';root.mkdir(exist_ok=True)
    captured=c.load(capture_path());require(captured['request_sha256']==request_sha,'capture_request_changed')
    issues=[x for r in captured.get('rows',[]) if r['state']=='official_errors_read' for x in r['issues']]
    async with session.lock:
        offers=await capture_offers(session,request,issues,root/'offers')
    plan=c.plan_rows(c.pinned(request['snapshot']),c.export_rows(request['inventory']),c.pinned(request['bases']),issues,offers)
    plan.update(request_sha256=request_sha,capture=c.reference(capture_path()),offers=c.reference(root/'offers/terminal.json') if issues else None,
        unresolved_records=[r for r in captured.get('rows',[]) if r['state']=='blocked'],authorization=c.AUTH)
    c.save(root/'plan.json',plan)
    c.save(root/'terminal.json',dict(state='prepared',plan=c.reference(root/'plan.json'),summary=plan['summary'],exceptions=plan['exceptions'],platform_write=False))
    return plan

def checked_plan(request_sha):
    release=c.load(c.ROOT/'release.json')
    require(release.get('request_sha256')==request_sha,'anomaly_release_request_changed')
    for ref in release['files']:
        require(c.sha(ref['path'])==ref['sha256'],'anomaly_release_source_changed')
    request=c.validate_request(request_sha,fresh=True)
    terminal=c.load(c.ROOT/'prepare/terminal.json');plan=c.pinned(terminal['plan'])
    require(plan['request_sha256']==request_sha and plan['authorization']==c.AUTH,'anomaly_plan_binding_changed')
    capture=c.pinned(plan['capture']);offers=c.pinned(plan['offers']) if plan['offers'] else dict(rows=[])
    issues=[x for r in capture.get('rows',[]) if r['state']=='official_errors_read' for x in r['issues']]
    actual=c.plan_rows(c.pinned(request['snapshot']),c.export_rows(request['inventory']),c.pinned(request['bases']),issues,offers)
    require(all(actual[k]==plan[k] for k in ('decisions','exceptions','summary')),'anomaly_recomputed_decisions_changed')
    return request,plan

async def open_editor(session,record,folder):
    page=await session.context.new_page()
    try:
        io,row,observed=await locate(page,record,folder)
        status=observed['row']['cells'][-2].strip()
        require(not any(x in status for x in ('撤销','暂停','草稿')),'anomaly_record_status_changed')
        control=await row_control(page,row,'编辑商品')
        if control is None:
            from app.engine.campaign_original_control import expose
            await expose(page,row,'编辑商品');control=await row_control(page,row,'编辑商品')
        require(control is not None,'anomaly_original_edit_control_missing')
        page._completion_editor_owned=dict(item=record['item'],marketing_id=record['marketing_id'])
        await control.click(timeout=5000);await ready_editor(page)
        observed=await inventory(page,{r['sku'] for r in record['rows']});c.save(folder/'editor.json',observed)
        return page,io,observed,status
    except Exception as exc:
        if not any(x in str(exc).lower() for x in ('login','security','human_','captcha')):await page.close()
        raise

async def inspection(session,request_sha):
    from app.engine.campaign_completion_submit import submit_once
    request=c.validate_request(request_sha);root=c.ROOT/'inspection';result=dict(state='blocked',platform_write=False)
    record=next(r for r in request['records'] if r['item']=='1038087212258')
    async with session.lock:
        page=None;rec=None
        try:
            page,io,before,status=await open_editor(session,record,root/'original')
            rec=RetainedCampaignRecorder(page,root=root/'recording',duration=120);await rec.start()
            actual={r['sku_id']:c.fmt(Decimal(r['price'])) for r in before['rows']}
            expected={r['sku']:c.fmt(Decimal(r['activity_price'])) for r in record['rows']}
            require(actual==expected,'anomaly_inspection_baseline_changed')
            trial=await submit_once(page,io,root,actual,actual,bound_target=record,scope_validator=lambda:c.validate_request(request_sha),inspection_only=True,authorization=c.AUTH)
            result.update(state='ready_before_write',item=record['item'],marketing_id=record['marketing_id'],all_seven_prices_match=True,trial=trial,price_fields_filled=False)
        except Exception as exc:result['reason']=str(exc)[:600]
        finally:
            if rec:result['recording']=await rec.stop()
            if page:await page.close()
            c.save(root/'terminal.json',result)
    return result

async def exact_field(page,sku):
    from app.engine.campaign_colleague_enrollment import DOUBLE_ROWS
    for step in range(10):
        await page.evaluate(DOUBLE_ROWS)
        field=page.locator('input[data-double11-price-sku="'+sku+'"]')
        if await field.count()==1 and await field.is_visible() and await field.is_editable():return field
        await page.evaluate('''f=>{for(const e of document.querySelectorAll('*')){if(e.scrollHeight>e.clientHeight+80&&['auto','scroll'].includes(getComputedStyle(e).overflowY))e.scrollTop=(e.scrollHeight-e.clientHeight)*f;}}''',step/9)
        await asyncio.sleep(.15)
    raise SurfaceError('anomaly_exact_sku_field_unavailable')

async def amend_discount(session,record,changes,folder,validator):
    from app.engine.campaign_discount_page import DiscountPage
    from app.engine.uploader import SINGLE_ITEM_HOME_URL
    ids={r['offer_id'] for r in changes};require(len(ids)==1,'anomaly_multiple_offer_groups')
    offer=next(iter(ids));folder.mkdir(parents=True)
    page=await session.context.new_page();rec=RetainedCampaignRecorder(page,root=folder/'recording',duration=360,pending_navigation_url=SINGLE_ITEM_HOME_URL);await rec.start()
    try:
        await page.goto(SINGLE_ITEM_HOME_URL,wait_until='domcontentloaded',timeout=30000)
        driver=DiscountPage(page,shop=c.IDENTITY['shop_name'],**c.WINDOW)
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

async def amend_original(session,record,changes,folder,validator):
    from app.engine.campaign_completion_submit import submit_once
    folder.mkdir(parents=True)
    page,io,observed,status=await open_editor(session,record,folder/'before')
    rec=RetainedCampaignRecorder(page,root=folder/'recording',duration=360);await rec.start();saved=False
    try:
        before={r['sku_id']:c.fmt(Decimal(r['price'])) for r in observed['rows']}
        original={r['sku']:c.fmt(Decimal(r['activity_price'])) for r in record['rows']}
        require(before==original,'anomaly_original_price_baseline_changed')
        after=dict(before)
        for change in changes:
            if change['action']=='original_activity_price':after[change['sku']]=change['new_price']
        for sku,price in after.items():
            if price!=before[sku]:
                field=await exact_field(page,sku);await io._pace('fill:authorized-anomaly-price');await field.fill(price,timeout=4000);await field.press('Tab')
        filled=await inventory(page,after)
        require({r['sku_id']:c.fmt(Decimal(r['price'])) for r in filled['rows']}==after,'anomaly_filled_price_mismatch')
        other={r['sku_id']:r['other_inputs'] for r in observed['rows']}
        require(all(r['other_inputs']==other[r['sku_id']] for r in filled['rows']),'anomaly_unrelated_form_fields_changed')
        validator();c.save(folder/'write-intent.json',dict(item=record['item'],marketing_id=record['marketing_id'],before=before,after=after,authorization=c.AUTH,state='unknown_no_replay'))
        try:
            await submit_once(page,io,folder,before,after,bound_target=record,scope_validator=validator,authorization=c.AUTH)
        except SurfaceError as exc:
            if str(exc) in ('official_sku_price_validation_rejected','official_activity_price_constraint_rejected','official_sales_admission_notice_blocks_edit','official_form_submission_failed'):
                rejected=dict(state='official_rejected',reason=str(exc),item=record['item'],marketing_id=record['marketing_id'],saved_prices_verified=False,automatic_retry=False)
                c.save(folder/'terminal.json',rejected);await page.close();return rejected
            if str(exc)!='restore62_form_stage_or_submission_unknown':raise
            require((folder/'final-submit-claimed.json').exists() or (folder/'price-risk-confirmation.json').exists(),'anomaly_submission_not_proven')
        check,check_io,readback,new_status=await open_editor(session,record,folder/'readback')
        try:
            require({r['sku_id']:c.fmt(Decimal(r['price'])) for r in readback['rows']}==after,'anomaly_original_saved_prices_unknown')
            require(all(r['other_inputs']==other[r['sku_id']] for r in readback['rows']),'anomaly_saved_unrelated_fields_changed')
            c.save(folder/'terminal.json',dict(state='saved_prices_verified',prices=after,status=new_status,platform_write=True,original_marketing_id=record['marketing_id'],needs_final_export=True))
            saved=True
        finally:await check.close()
        return dict(state='saved_prices_verified')
    finally:
        c.save(folder/'recording-result.json',await rec.stop())
        if saved or not (folder/'write-intent.json').exists():await page.close()

async def final_export(session,request,root):
    from app.engine.campaign_final_inventory import read
    root.mkdir(parents=True)
    page=await session.context.new_page();url=source_url('november88')
    rec=RetainedCampaignRecorder(page,root=root/'recording',duration=450,pending_navigation_url=url);await rec.start()
    try:
        await page.goto(url,wait_until='domcontentloaded',timeout=30000)
        payload=dict(identity=c.IDENTITY,price_window=c.WINDOW,read_request_id=c.digest(['nov88-anomaly-final',request]))
        c.save(root/'intent.json',payload)
        result=await read(page,payload,root);c.save(root/'result.json',result)
        rows=c.export_rows(result['file']);c.verify_export(result,rows);return result,rows
    finally:
        c.save(root/'recording-result.json',await rec.stop());await page.close()

async def apply(session,request_sha):
    request,plan=checked_plan(request_sha);root=c.ROOT/'apply';completed=[];failures=[]
    records={r['item']:r for r in request['records']};groups={}
    for d in plan['decisions']:
        if d['action'] in ('original_discount','original_activity_price'):groups.setdefault(d['item'],[]).append(d)
    result=dict(state='stopped_preserve_checkpoints',request_sha256=request_sha,automatic_retry=False)
    validator=lambda:checked_plan(request_sha)
    async with session.lock:
        try:
            for item,changes in groups.items():
                folder=root/item;folder.mkdir(parents=True)
                try:
                    discounts=[r for r in changes if r['action']=='original_discount']
                    if discounts:await amend_discount(session,records[item],discounts,folder/'discount',validator)
                    original=await amend_original(session,records[item],changes,folder/'original',validator)
                    if original['state']=='official_rejected':
                        failures.append(original);continue
                    completed.append(item)
                except Exception as exc:
                    attempts=list(folder.rglob('write-intent.json'));reason=str(exc)
                    failures.append(dict(item=item,reason=reason[:600],state='unknown_no_replay' if attempts else 'blocked_before_write'))
                    if attempts or any(x in reason.lower() for x in ('login','security','human_')):break
            human_gate=any(any(word in f['reason'].lower() for word in ('login','security','human_')) for f in failures)
            if (completed or any(root.rglob('write-intent.json'))) and not human_gate:
                exported,rows=await final_export(session,request,root/'final-inventory')
                audit=c.audit(c.export_rows(request['inventory']),rows,plan);c.save(root/'audit.json',audit)
                result.update(state='complete_verified' if audit['all_verified'] and audit['all_final_prices_known'] and not failures and not plan['exceptions'] and not plan['unresolved_records'] else 'complete_with_exceptions',final_export=exported,audit=c.reference(root/'audit.json'))
            else:result['state']='no_eligible_changes'
        except Exception as exc:result['reason']=str(exc)[:600]
        finally:
            result.update(completed_items=completed,failed_items=failures,exceptions=plan['exceptions'],deferred=[d for d in plan['decisions'] if d['action']=='deferred_same_record_exception'],platform_write=any(root.rglob('write-intent.json')))
            c.save(root/'terminal.json',result)
    return result
