"""Exercise the real App menu and route, not the isolated OpsTools harness.

All API traffic is intercepted; no production login or business writes.
Optional BASE_URL permits testing the released static frontend in isolation.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.business_relationship_service import snapshot

BASE = os.environ.get('BASE_URL', 'http://127.0.0.1:5189').rstrip('/')
OUT = Path(os.environ.get('MENU_TEST_OUT', str(ROOT.parents[1] / 'outputs' / 'relationship-menu-20260921')))
OUT.mkdir(parents=True, exist_ok=True)
catalog = snapshot()
checks, errors = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for restricted, mobile in [(False, False), (True, False), (False, True), (True, True)]:
        context = browser.new_context(viewport={'width': 390 if mobile else 1920, 'height': 950})
        context.add_init_script("localStorage.setItem('panse_token', 'isolated-test-not-a-credential')")
        user = {'id': 1, 'username': 'menu-test', 'display_name': '菜单验收',
                'role': 'operator' if restricted else 'admin', 'is_active': True,
                'page_perms': ['importer'] if restricted else None}
        relationship_requests = []
        def intercept(route):
            request = route.request
            path = urlsplit(request.url).path
            if not path.startswith('/api/'):
                route.continue_()
                return
            if request.method != 'GET':
                errors.append('unexpected write: ' + path)
                route.abort()
                return
            if path == '/api/auth/me': payload = user
            elif path == '/api/admin/business-relationships':
                relationship_requests.append(path)
                payload = catalog
            elif path == '/api/alerts/active': payload = []
            elif path == '/api/version': payload = {'commit': 'menu-test'}
            elif path.endswith('/count'): payload = {'count': 0}
            else:
                route.fulfill(status=404, content_type='application/json', body='{}')
                return
            route.fulfill(status=200, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
        context.route('**/*', intercept)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE + '/importer')
        page.wait_for_load_state('networkidle')
        if mobile:
            page.get_by_role('button', name='菜单', exact=True).click()
            tools = page.get_by_role('menuitem', name='工具', exact=True)
            # Importer belongs to Tools, already expanded in mobile navigation.
            assert tools.get_attribute('aria-expanded') == 'true'
        else:
            page.get_by_role('menuitem', name='工具', exact=True).hover()
        link = page.get_by_role('link', name='关系流程', exact=True)
        label = ('restricted' if restricted else 'admin') + (' mobile' if mobile else ' desktop')
        if restricted:
            assert link.count() == 0, label
            page.goto(BASE + '/ops-tools')
            page.get_by_text('程序错误', exact=True).wait_for()
            assert not relationship_requests
            checks.append(label + ': menu hidden and direct route blocked')
        else:
            link.wait_for(state='visible')
            assert link.get_attribute('href') == '/ops-tools'
            page.screenshot(path=str(OUT / ('menu-mobile.png' if mobile else 'menu-desktop.png')))
            link.click()
            page.wait_for_url('**/ops-tools')
            page.get_by_role('heading', name='关系流程', exact=True).wait_for()
            assert relationship_requests
            page.screenshot(path=str(OUT / ('page-mobile.png' if mobile else 'page-desktop.png')))
            checks.append(label + ': Tools link navigates from importer to relationship page')
            page.get_by_role('button', name='活动相关关系', exact=True).click()
            page.get_by_role('button', name='Web Agent固定程序边界', exact=False).wait_for()
            page.get_by_role('button', name='活动程序全链路', exact=True).click()
            page.get_by_role('button', name='活动报名入口与官方发现', exact=False).click()
            drawer = page.get_by_role('dialog', name='活动报名入口与官方发现', exact=True)
            drawer.get_by_text('03｜畔色活动报名 / checked_snapshot', exact=True).wait_for()
            page.keyboard.press('Escape')
            drawer.wait_for(state='hidden')
            page.get_by_role('button', name='飞书相关关系', exact=True).click()
            assert page.get_by_role('tab', name='业务地图', exact=True).get_attribute('aria-selected') == 'true'
            for text in ['飞书报表口令与解密结果', '飞书活动终态通知', '真实回复与身份核验']:
                page.get_by_role('button', name=text, exact=False).wait_for()
            page.get_by_role('heading', name='关系流程', exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / ('feishu-mobile.png' if mobile else 'feishu-desktop.png')))
            page.get_by_role('button', name='工厂表同步流程', exact=True).click()
            page.get_by_role('button', name='工厂表管理字段', exact=False).wait_for()
            checks.append(label + ': campaign, Feishu search, ownership drawer and factory flow visible')
        context.close()
    browser.close()
result = {'base_url': BASE, 'scope': 'real App navigation with isolated mocked APIs', 'checks': checks, 'errors': errors}
(OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
assert not errors
