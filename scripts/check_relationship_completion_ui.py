"""Isolated UI acceptance: local source metadata only; prohibit every business write."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.business_relationship_service import snapshot, review_changes
from app.services.business_relationship_index import source_index

OUT = ROOT.parents[1] / 'outputs' / 'relationship-completion-20260921'
OUT.mkdir(parents=True, exist_ok=True)
data = snapshot()
index = source_index(ROOT / 'backend' / 'app')
review = review_changes(['backend/app/services/factory_sheet.py', 'unknown.py'])
checks, errors, requests = [], [], []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page.on('pageerror', lambda error: errors.append(str(error)))
    def intercept(route):
        req = route.request
        if not req.url.startswith('http://127.0.0.1:5189/api/'):
            route.continue_(); return
        requests.append({'url': req.url.split('/api/')[-1], 'method': req.method})
        if req.method != 'GET':
            errors.append('write forbidden'); route.abort(); return
        if '/source-index' in req.url: payload = index
        elif '/changes?' in req.url: payload = review
        elif req.url.endswith('/business-relationships'): payload = data
        else:
            errors.append('unexpected request'); route.abort(); return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
    page.route('**/api/**', intercept)
    page.goto('http://127.0.0.1:5189/e2e/relationship-harness.html')
    page.wait_for_load_state('networkidle')
    page.get_by_role('heading', name='关系流程', exact=True).wait_for()
    checks.append('default page renders')
    with page.expect_download() as event:
        page.get_by_role('button', name='导出关系目录', exact=True).click()
    assert json.loads(Path(event.value.path()).read_text('utf-8'))['version'] == data['version']
    checks.append('catalog export contains bound version and metadata')
    page.get_by_role('tab', name='流程泳道', exact=True).click()
    page.get_by_text('流程条件、分支及跨流程影响', exact=True).wait_for()
    assert page.get_by_text('跨流程影响', exact=True).count() > 0
    checks.append('real conditional and cross-flow relations displayed')
    page.get_by_role('tab', name='改动影响核查', exact=True).click()
    page.get_by_label('搜索程序或函数').wait_for()
    page.get_by_label('搜索程序或函数').fill('campaign_prior_failure_import.py')
    page.get_by_role('button', name='加入检查', exact=True).first.wait_for()
    page.get_by_role('button', name='加入检查', exact=True).first.click()
    assert 'campaign_prior_failure_import.py' in page.get_by_label('本次修改文件').input_value()
    checks.append('campaign script included in full index and selectable')
    page.get_by_label('本次修改文件').fill('backend/app/services/factory_sheet.py\nunknown.py')
    page.get_by_role('button', name='生成影响检查清单', exact=True).click()
    page.get_by_role('button', name='导出本次检查清单', exact=True).wait_for()
    assert 'unknown.py' in page.locator('body').inner_text()
    checks.append('unknown paths remain explicit')
    with page.expect_download() as event:
        page.get_by_role('button', name='导出本次检查清单', exact=True).click()
    exported = json.loads(Path(event.value.path()).read_text('utf-8'))
    assert exported['checklist'] == review['checklist'] and not exported['runtime_verified']
    checks.append('review download contains all checks without runtime claim')
    page.screenshot(path=str(OUT / 'change-review-desktop.png'), full_page=True)
    for name in ('业务地图', '流程泳道', '字段影响', '遗漏与过期检查', '改动影响核查'):
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('tab', name=name, exact=True).click()
        page.wait_for_timeout(200)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), name
        checks.append('mobile containment: ' + name)
    page.screenshot(path=str(OUT / 'change-review-mobile.png'), full_page=True)
    page.unroute('**/api/**')
    page.route('http://127.0.0.1:5189/api/**', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps({**data, 'nodes': [], 'flows': []})))
    page.reload(); page.get_by_text('关系目录不完整，暂不展示可能误导的关系图', exact=True).wait_for()
    checks.append('empty graph fails visibly, no crash')
    page.unroute('http://127.0.0.1:5189/api/**')
    page.route('http://127.0.0.1:5189/api/**', lambda route: route.fulfill(status=403, content_type='application/json', body='{}'))
    page.reload(); page.get_by_text('关系目录暂时无法读取（可能是权限或连接问题）', exact=True).wait_for()
    checks.append('permission denial does not invoke fallback writes')
    browser.close()
result = {'scope': 'isolated local UI, mocked readonly metadata; no production acceptance', 'checks': checks, 'errors': errors, 'requests': requests}
(OUT / 'ui-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'checks': len(checks), 'errors': errors}, ensure_ascii=False))
assert not errors
