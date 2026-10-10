"""Synthetic-data browser checks. All API traffic intercepted; no real quote/sends."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:4179');p.add_argument('--output',required=True);a=p.parse_args()
out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
skus=[dict(sku_code='P0116',sku_name='樱桃木款式一',price=2510,confidence=.9,original_material='樱桃木'),dict(sku_code='P0117',sku_name='樱桃木款式二',price=2734.69,confidence=.8,original_material='樱桃木')]
calls=[];checks=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    page=browser.new_page(viewport=dict(width=1440,height=1000))
    page.on('pageerror',lambda e:print('PAGE_ERROR',e))
    page.add_init_script("localStorage.setItem('panse_token','isolated-ui-test')")
    def route(r):
        path=r.request.url.split('/api/',1)[1];body=[]
        if path=='auth/me':body=dict(id=1,username='test',role='admin',is_active=True)
        if 'part-options' in path:body=dict(parts=[],materials=['樱桃木-2.2cm','黑胡桃木-2.2cm'],woods=['樱桃木','黑胡桃'])
        if path.endswith('/classify'):
            body=dict(customization_type='普通定制',base_product_code='P',base_product_name='测试床',confidence=.9,reasoning='isolated',
                target_length_m=1.5,target_material='樱桃木',sku_candidates=skus,candidates=[dict(product_code='P',product_name='测试床',confidence=.9)])
        if 'sku-candidates' in path:body=dict(product_code='P',items=skus)
        if path.endswith('quote-both'):
            payload=r.request.post_data_json;calls.append(payload)
            assert payload['confirmation']['inputs']=={k:v for k,v in payload.items() if k!='confirmation'}
            body=dict(spec=dict(final_price=3210.55,anchor=2510,base_product_name='测试床',category='卧室-床',breakdown=[],parts_detail=[],
                specification=dict(price_tier=payload['price_tier'],price_tier_label='已确认口径',target_material=payload['target_material'])),custom=None,custom_boards=[])
        r.fulfill(status=200,content_type='application/json',body=json.dumps(body,ensure_ascii=False))
    page.route('**/api/**',route)
    page.goto(a.url+'/custom-quote-v2');page.wait_for_load_state('networkidle')
    print('INITIAL',page.url,page.inner_text('body')[:1000])
    page.screenshot(path=str(out/'initial.png'),full_page=True)
    expect(page.get_by_role('heading',name='定制报价',exact=True)).to_be_visible()
    page.get_by_placeholder('例如：榉木餐桌改2米，宽90cm，台面改白色岩板').fill('樱桃木床1.5米')
    page.get_by_role('button',name='生成报价',exact=True).click()
    dialog=page.get_by_role('dialog');expect(dialog).to_be_visible()
    button=dialog.get_by_role('button',name='确认本次信息并计算')
    expect(button).to_be_disabled();assert not calls
    assert '¥2510' not in page.inner_text('body')
    checks.append('识别后无计算请求/无候选金额/确认无默认选择')
    def choose(label,text):
        dialog.get_by_label(label,exact=True).click()
        page.locator('.ant-select-item-option-content').get_by_text(text,exact=True).click()
    def complete(tier='大促到手价（买家最终到手口径）',sku='樱桃木款式一 · P0116'):
        choose('确认精确款式',sku)
        choose('确认价格口径',tier)
        expect(button).to_be_disabled()
        choose('确认具体主材','樱桃木（沿用已核对原材）')
        expect(button).to_be_disabled()
        dialog.get_by_role('checkbox').check()
        expect(button).to_be_enabled()
    complete()
    page.screenshot(path=str(out/'confirmation.png'),full_page=True)
    button.click();expect(page.locator('.tq-price-value')).to_contain_text('3210.55')
    assert len(calls)==1 and calls[-1]['price_tier']=='big_buyer' and calls[-1]['base_sku_code']=='P0116'
    checks.append('精确SKU/主材/到手口径全部确认才计算')
    page.get_by_role('button',name='算价',exact=True).click();expect(button).to_be_disabled()
    expect(page.locator('.tq-price-value')).to_have_count(0);assert len(calls)==1
    dialog.get_by_role('button',name='取消，不算价').click();assert len(calls)==1
    checks.append('重算必须重新选择；取消无计算')
    page.get_by_role('button',name='算价',exact=True).click()
    complete('定价表大促价（不是大促到手价）','樱桃木款式二 · P0117')
    choose('确认价格口径','中促到手价（买家最终到手口径）')
    expect(button).to_be_disabled();dialog.get_by_role('checkbox').check();button.click()
    expect(page.locator('.tq-price-value')).to_be_visible()
    assert calls[-1]['price_tier']=='mid_buyer' and calls[-1]['base_sku_code']=='P0117'
    checks.append('弹窗更换口径撤销勾选，明确区分定价表和到手价')
    page.get_by_placeholder('例如：榉木餐桌改2米，宽90cm，台面改白色岩板').fill('新的樱桃木床需求')
    expect(page.locator('.tq-price-value')).to_have_count(0);assert len(calls)==2
    page.get_by_role('button',name='生成报价',exact=True).click();expect(button).to_be_disabled()
    assert len(calls)==2;dialog.get_by_role('button',name='取消，不算价').click()
    checks.append('新问价清空旧金额和确认')
    # Hold a response while the user changes the request, then ensure it cannot restore a stale price.
    held=[]
    page.route('**/api/customization/v2/quote-both',lambda r: held.append(r))
    page.get_by_role('button',name='算价',exact=True).click();complete();button.click()
    page.wait_for_timeout(150)
    assert len(held)==1
    page.get_by_placeholder('例如：榉木餐桌改2米，宽90cm，台面改白色岩板').fill('计算中修改了需求')
    held[0].fulfill(status=200,content_type='application/json',body=json.dumps(dict(spec=dict(final_price=99999,breakdown=[]),custom=None)))
    page.wait_for_timeout(150);expect(page.locator('.tq-price-value')).to_have_count(0)
    checks.append('旧响应晚到不能恢复失效金额')
    page.screenshot(path=str(out/'invalidated.png'),full_page=True)
    page.set_viewport_size(dict(width=390,height=844))
    page.get_by_role('button',name='算价',exact=True).click();expect(button).to_be_disabled()
    page.screenshot(path=str(out/'mobile-confirmation.png'),full_page=True)
    checks.append('移动端确认弹窗可见且无默认授权')
    browser.close()
(out/'result.json').write_text(json.dumps(dict(checks=checks,passed=len(checks),api_intercepted=True,business_writes=0),ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(passed=len(checks),checks=checks),ensure_ascii=False))
