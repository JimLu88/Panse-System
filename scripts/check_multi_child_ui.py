"""Headless, local-only mocked GET readback. All external writes forbidden."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'outputs'/'relationship-implementation-20260920'
OUT.mkdir(parents=True,exist_ok=True)
rows=[dict(product_code='__MULTI_UNALLOCATED__',product_name='多商品订单（金额未分摊）',sku_code='UNALLOCATED',sku='整单',qty=0,revenue=10000,cost=6000,net_profit=4000,gross_profit_rate=.4,net_profit_rate=.4,money_pending=False),
      dict(product_code='PRODUCT-A',product_name='玄关柜',sku_code='SKU-A',sku='樱桃木',qty=2,revenue=0,cost=0,net_profit=0,gross_profit_rate=0,net_profit_rate=0,money_pending=True,unknown_quantity_count=1)]
rank=dict(granularity='month',metric='revenue',selected_period='2026-09',periods=[],ranking=[dict(rank=1,product_code='PRODUCT-A',product_name='玄关柜',qty=2,revenue=0,net_profit=0,order_count=0,unallocated_order_count=1)],excluded_non_product=0)
errors=[];requests=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1360,'height':1000})
    page.on('pageerror',lambda error:errors.append(str(error)))
    def intercept(route):
        request=route.request
        if not request.url.startswith('http://127.0.0.1:5189/api/'):route.continue_();return
        requests.append({'method':request.method,'url':request.url})
        if request.method!='GET':errors.append('forbidden write');route.abort();return
        payload=({'period_start':'2026-09-01','period_end':'2026-09-30','rows':rows} if '/sales/breakdown' in request.url
                 else rank if '/sales/ranking' in request.url else {})
        route.fulfill(status=200,content_type='application/json',body=json.dumps(payload,ensure_ascii=False))
    page.route('**/api/**',intercept)
    page.goto('http://127.0.0.1:5189/e2e/multi-child-harness.html')
    page.wait_for_load_state('networkidle')
    page.get_by_text('待分摊',exact=True).first.wait_for()
    assert page.get_by_text('待分摊',exact=True).count() >= 5
    assert page.get_by_text('2（另有数量待核实）',exact=True).is_visible()
    assert page.get_by_text('含金额待分摊子单',exact=True).is_visible()
    page.screenshot(path=str(OUT/'multi-child-report-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(250)
    assert page.get_by_text('含多商品子单：金额另列待分摊，当前金额仅含已归属部分',exact=True).is_visible()
    page.screenshot(path=str(OUT/'multi-child-report-mobile.png'),full_page=True)
    browser.close()
result={'scope':'local mocked GET only; no production write','checks':5,'errors':errors,'requests':requests}
(OUT/'multi-child-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':5,'errors':errors},ensure_ascii=False))
assert not errors
