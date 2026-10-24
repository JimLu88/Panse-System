"""Local browser contract tests with explicit mocked API; not production acceptance."""
import json
from playwright.sync_api import sync_playwright, expect

ref = dict(product_code='BED', product_name='榉木柔光床', sku_code='BED14',
           sku_name='榉木柔光床-1.8米-榉木铺板', material='榉木', wood='榉木',
           size_info='宽度1850mm 长度2100mm 高度880mm', size_status='待确定',
           length_cm=210, width_cm=185, source='PricingSku.size_info', buyer_price=4030.61,
           fees=dict(packaging_cost=150, logistics_cost=300, install_cost=50), error=None,
           parts=[dict(name='榉木柔光床小夜灯', qty=1, unit='套', price=100)])
requests = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.add_init_script("localStorage.setItem('panse_token','local-mock-only')")
    def handle(route):
        path = route.request.url
        out = []
        if '/auth/me' in path:
            out = dict(id=1, username='test', role='admin', is_active=True)
        elif '/bed-references' in path:
            out = dict(items=[ref, {**ref, 'sku_code':'BED15', 'sku_name':'不同铺板-缺价', 'buyer_price':None, 'error':'原SKU指定到手价须为已确认的正数'}],
                       materials=[dict(name='榉木-2.2cm', unit='平方米', price=240)])
        elif '/part-options' in path:
            out = dict(parts=[], materials=[], woods=[])
        elif '/quote-light' in path:
            body = route.request.post_data_json
            requests.append(body)
            out = dict(final_price=3379.73, selected_sku_code='BED14',
                       outer_dimension_basis=dict(buyer_price=4030.61, fees=ref['fees']), breakdown=[])
        elif '/quote-both' in path or '/classify' in path:
            raise AssertionError('Bed form must not use AI or unverified quote-both')
        route.fulfill(status=200, content_type='application/json', body=json.dumps(out))
    page.route('**/api/**', handle)
    page.goto('http://127.0.0.1:4173/custom-quote-v2')
    page.wait_for_load_state('networkidle')
    page.get_by_role('button', name='床定制（外尺寸）', exact=True).click()
    page.get_by_role('combobox', name='床产品', exact=True).fill('BED')
    page.get_by_role('combobox', name='床产品', exact=True).press('Enter')
    page.get_by_role('combobox', name='原款规格', exact=True).fill('BED14')
    page.get_by_role('combobox', name='原款规格', exact=True).press('Enter')
    page.get_by_label('目标床体外长', exact=True).fill('200')
    page.get_by_label('目标床体外宽', exact=True).fill('170')
    page.get_by_role('checkbox', name='去掉床头板', exact=False).check()
    page.get_by_label('床头板高度', exact=True).fill('70.5')
    page.get_by_role('checkbox', name='去掉 榉木柔光床小夜灯', exact=False).check()
    button = page.get_by_role('button', name='计算床定制估算价', exact=True)
    expect(button).to_be_disabled()
    page.get_by_role('checkbox', name='我确认本次材质', exact=False).check()
    button.click()
    expect(page.get_by_test_id('bed-quote-result')).to_contain_text('3379.73')
    assert requests[-1]['outer_dimensions'] == dict(length_cm=200, width_cm=170)
    assert requests[-1]['price_tier'] == 'big_buyer'
    assert requests[-1]['remove_parts'][0] == dict(material='榉木柔光床小夜灯', qty=1)
    assert requests[-1]['remove_parts'][1]['width_cm'] == 70.5
    page.get_by_text('厘米 cm', exact=True).click()
    page.get_by_text('毫米 mm', exact=True).click()
    expect(page.get_by_label('目标床体外长', exact=True)).to_have_value('2000')
    expect(button).to_be_disabled()
    page.get_by_role('checkbox', name='我确认本次材质', exact=False).check()
    button.click()
    expect(page.get_by_test_id('bed-quote-result')).to_contain_text('3379.73')
    assert requests[0] == requests[1], 'cm/mm must produce identical payloads'
    page.get_by_role('combobox', name='原款规格', exact=True).fill('BED15')
    page.locator('.ant-select-item-option-content').filter(has_text='不同铺板-缺价').click()
    expect(button).to_be_disabled()
    expect(page.get_by_test_id('bed-quote-result')).to_have_count(0)
    assert len(requests) == 2
    browser.close()
print(json.dumps(dict(passed=True, cases=['exact_identity','confirmation','single_lamp_set','cm_mm_equivalence','stale_result_reset','missing_price_block'], writes='mocked only')))
