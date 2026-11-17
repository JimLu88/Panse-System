"""Independent late-warning-aware submit; original program remains frozen."""
import asyncio,time
from decimal import Decimal
from app.engine.campaign_page_io import SurfaceError
from app.engine.campaign_remaining_readonly import save
from app.engine.campaign_colleague_rejection import reason as sku_validation_rejection
from app.engine.campaign_price_warning import ordinary_warning,ordinary_title
from app.engine.campaign_nov14_restore62 import AUTH,TARGETS,legacy,visible_form_errors,form_rejection,validate,payload
async def current_values(page,expected):
 from app.engine.campaign_completion_enrollment import inventory
 observed=await inventory(page,expected)
 return {r['sku_id']:format(Decimal(r['price']),'.2f') for r in observed['rows']}
async def submit_once(page,io,folder,before,after,*,bound_target=None,scope_validator=None,inspection_only=False,authorization=None,pending_final=False):
    import re
    after={sku:format(Decimal(value),'.2f') for sku,value in after.items()}
    if authorization is not None and (bound_target is None or not callable(scope_validator) or not isinstance(authorization,str) or not 1<=len(authorization)<=160):raise SurfaceError('restore62_explicit_authorization_requires_bound_target')
    effective_authorization=AUTH if authorization is None else authorization
    item=bound_target.get('item') if bound_target is not None else folder.name
    if not isinstance(item,str) or not item.isdigit():raise SurfaceError('restore62_invalid_explicit_item_binding')
    if bound_target is None:
        if scope_validator is not None:raise SurfaceError('restore62_incomplete_scope_binding')
        mid=TARGETS[item][0]
    else:
        if not callable(scope_validator) or bound_target.get('item')!=item:raise SurfaceError('restore62_incomplete_scope_binding')
        scope_validator();mid=bound_target['marketing_id']
    next_clicked=False;risk_clicked=False;final_clicked=False
    confirmed_warning_types=[]
    if pending_final:
        from repair_remaining import pending_seed
        pending_seed(folder,bound_target,after)
        final_clicked=True
    baseline_errors=None
    deadline=time.monotonic()+45
    async def visible(label):
        return [x for x in await page.get_by_role('button',name=label,exact=True).all() if await x.is_visible()]
    while time.monotonic()<deadline:
        body=await page.locator('body').inner_text(timeout=4000)
        gate=legacy.classify_submission(body)
        if gate=='login_required':raise SurfaceError('human_login_or_security_gate')
        exact=bool(re.search(r'商品\s*ID\s*[:：]?\s*'+item+r'(?!\d)',body) and re.search(r'营销\s*ID\s*[:：]?\s*'+mid+r'(?!\d)',body))
        errors=await visible_form_errors(page)
        if baseline_errors is None:baseline_errors=set(errors)
        rejection=None;rejection_text=None
        if exact:
            for error in errors:
                rejection=sku_validation_rejection(error,action_attempted=next_clicked or risk_clicked or final_clicked,fresh=error not in baseline_errors) or form_rejection(error, action_attempted=next_clicked or risk_clicked or final_clicked, fresh=error not in baseline_errors)
                if rejection:rejection_text=error;break
        if rejection:
            save(folder/'official-constraint-rejection.json',dict(reason=rejection,text=body,error_panel=rejection_text,visible_error_panels=errors,baseline_error_panels=sorted(baseline_errors),expected=after,next_clicked=next_clicked,risk_clicked=risk_clicked,final_clicked=final_clicked))
            raise SurfaceError(rejection)
        if gate=='success_message' and (risk_clicked or final_clicked):
            # The permanent page instructions also say 报名成功. Only a native
            # visible success notice can end the wait; late risk dialogs win.
            notices=page.locator('.next-message-success,.next-feedback-success,.tbd-message-notice-success,.ant-message-success,[role=\"status\"]')
            for notice in await notices.all():
                if await notice.is_visible() and re.search('提交成功|报名成功|保存成功|修改成功|设置成功',await notice.inner_text()):return
        if gate=='risk_confirmation_required':
            if inspection_only:raise SurfaceError('restore62_inspection_will_not_confirm_risk')
            warning_type=ordinary_warning(body,exact=exact,bound_target=bound_target)
            if not warning_type:
                save(folder/'unrecognized-price-warning.json',dict(text=body,exact_identity=exact,next_clicked=next_clicked,risk_clicked=risk_clicked,final_clicked=final_clicked))
                if ordinary_title(body,exact=exact,bound_target=bound_target) and not risk_clicked and not final_clicked and not (folder/'submission-claimed.json').exists():
                    raise SurfaceError('ordinary_price_warning_unrecognized_before_submit')
                raise SurfaceError('restore62_warning_identity_or_type_changed')
            if warning_type in confirmed_warning_types:
                await asyncio.sleep(.3);continue
            if len(confirmed_warning_types)>=2:raise SurfaceError('ordinary_warning_sequence_limit')
            if scope_validator is None:validate(payload())
            else:scope_validator()
            current=await current_values(page,after)
            if not current or current!=after:raise SurfaceError('restore62_warning_inputs_changed')
            controls=await visible('确认无风险，继续提交')
            if len(controls)!=1:raise SurfaceError('restore62_warning_control_not_unique')
            if not (folder/'submission-claimed.json').exists():save(folder/'submission-claimed.json',dict(before=before,expected=after,authorization=effective_authorization,warning_can_submit=True))
            confirmation=dict(text=body,warning_type=warning_type,expected=after,item=item,marketing_id=mid,authorization=effective_authorization)
            save(folder/('price-risk-confirmation-'+str(len(confirmed_warning_types)+1)+'.json'),confirmation)
            if not (folder/'price-risk-confirmation.json').exists():save(folder/'price-risk-confirmation.json',confirmation)
            confirmed_warning_types.append(warning_type)
            risk_clicked=True;await controls[0].click(timeout=5000);await asyncio.sleep(.5);continue
        if final_clicked:
            await asyncio.sleep(.3);continue
        nxt=await visible('下一步')
        previous=await visible('上一步')
        final=[]
        for label in ('提交报名','保存并发布','确认报名','提交'):final+=await visible(label)
        if not exact:raise SurfaceError('restore62_editor_identity_changed')
        if len(nxt)==1 and not next_clicked and not risk_clicked:
            if scope_validator:scope_validator()
            if await current_values(page,after)!=after:raise SurfaceError('restore62_pre_next_prices_changed')
            if inspection_only:
                await nxt[0].click(trial=True,timeout=4000)
                return dict(state='ready_before_next',item=item,marketing_id=mid,trial_passed=True,platform_write=False)
            next_clicked=True;await io._pace('next:exact-existing-item');await nxt[0].click(timeout=4000);await asyncio.sleep(.5);continue
        # Both step headings appear on the price page; text alone never proves
        # the material stage. Require its actual previous/final footer controls.
        material=not nxt and len(previous)==1 and len(final)==1 and '商品素材设置' in body
        direct=not nxt and len(final)==1 and not risk_clicked and not next_clicked
        if material or direct:
            if inspection_only:
                if direct and authorization is not None:
                    if await current_values(page,after)!=after:raise SurfaceError('restore62_inspection_prices_changed')
                    await final[0].click(trial=True,timeout=4000)
                    return dict(state='ready_before_direct_submit',item=item,marketing_id=mid,trial_passed=True,platform_write=False)
                raise SurfaceError('restore62_inspection_requires_initial_price_step')
            if scope_validator:scope_validator()
            if direct and await current_values(page,after)!=after:raise SurfaceError('restore62_pre_submit_prices_changed')
            if not (folder/'submission-claimed.json').exists():save(folder/'submission-claimed.json',dict(before=before,expected=after,authorization=effective_authorization))
            save(folder/'final-submit-claimed.json',dict(expected=after,authorization=effective_authorization,text=body))
            final_clicked=True;await io._pace('submit:exact-existing-item-once');await final[0].click(timeout=5000);await asyncio.sleep(.5);continue
        await asyncio.sleep(.3)
    save(folder/'form-stage-timeout.json',dict(next_clicked=next_clicked,risk_clicked=risk_clicked,final_clicked=final_clicked,confirmed_warning_types=confirmed_warning_types))
    raise SurfaceError('restore62_form_stage_or_submission_unknown')
