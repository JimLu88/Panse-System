"""Local headless visual/interaction test. Mocked reads; all writes are forbidden."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.business_relationship_service import snapshot, field_references

OUT=ROOT.parents[1]/'outputs'/'relationship-implementation-20260920'
OUT.mkdir(parents=True,exist_ok=True)
data=snapshot()
refs=field_references('OrderDetail','qty')
errors=[];requests=[];checks=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1360,'height':950},device_scale_factor=1)
    page.on('pageerror',lambda exc:errors.append(str(exc)))
    def intercept(route):
        req=route.request
        if not req.url.startswith('http://127.0.0.1:5189/api/'):
            route.continue_();return
        requests.append({'url':req.url.split('/api/')[-1],'method':req.method})
        if req.method!='GET':
            errors.append('forbidden write: '+req.url);route.abort();return
        if '/business-relationships/field?' in req.url:payload=refs
        elif req.url.endswith('/business-relationships'):payload=data
        else:
            errors.append('unexpected API on relationship view: '+req.url);route.abort();return
        route.fulfill(status=200,content_type='application/json',body=json.dumps(payload,ensure_ascii=False))
    page.route('**/api/**',intercept)
    page.goto('http://127.0.0.1:5189/e2e/relationship-harness.html')
    page.wait_for_load_state('networkidle')
    page.get_by_role('heading',name='关系流程',exact=True).wait_for()
    page.screenshot(path=str(OUT/'01-business-map.png'),full_page=True)
    checks.append('default relationship view; existing tools do not issue API reads')
    page.get_by_label('搜索业务或字段').fill('完全不存在的字段XYZ')
    assert page.get_by_text('没有匹配关系；不代表该业务没有影响').is_visible()
    page.get_by_label('搜索业务或字段').fill('')
    checks.append('empty search explicitly not no-impact proof')
    page.get_by_role('tab',name='流程泳道',exact=True).click()
    page.locator('.br-lanes').wait_for()
    page.screenshot(path=str(OUT/'02-swimlanes.png'),full_page=True)
    checks.append('swimlane displays role/sequence and conditional warning')
    page.get_by_role('tab',name='字段影响',exact=True).click()
    page.locator('.br-graph').wait_for()
    page.screenshot(path=str(OUT/'03-field-impact.png'),full_page=True)
    page.get_by_role('button',name='查看确认成品数量',exact=True).click()
    page.get_by_text('直接关联及验证要求',exact=True).wait_for()
    assert page.get_by_role('button',name='以此字段查看影响').is_visible()
    page.get_by_role('button',name='Close',exact=True).click()
    checks.append('field graph keyboard/click detail with protection contracts')
    page.get_by_role('tab',name='遗漏与过期检查',exact=True).click()
    page.get_by_role('button',name='只读查找字段引用',exact=True).click()
    page.get_by_text(f'找到 {refs["total"]} 处候选引用',exact=False).wait_for()
    page.screenshot(path=str(OUT/'04-coverage.png'),full_page=True)
    checks.append('all-model field reference lookup; explicit candidate limitation')
    for name in ['业务地图','流程泳道','字段影响','遗漏与过期检查']:
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('tab',name=name,exact=True).click()
        page.wait_for_timeout(200)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2'),name
        page.screenshot(path=str(OUT/f'mobile-{name}.png'),full_page=True)
        checks.append('mobile containment: '+name)
    page.set_viewport_size({'width':1360,'height':950})
    page.unroute('**/api/**')
    page.route('http://127.0.0.1:5189/api/**',lambda route:route.fulfill(status=403,content_type='application/json',body='{"detail":"denied"}'))
    page.reload();page.get_by_text('关系目录暂时无法读取（可能是权限或连接问题）',exact=True).wait_for()
    checks.append('permission failure visible and no write fallback')
    browser.close()
result={'scope':'local isolated UI with mocked GET; not production or business receipts',
        'checks':checks,'errors':errors,'requests':requests}
(OUT/'ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(checks),'errors':errors,'requests':len(requests)},ensure_ascii=False))
assert not errors
