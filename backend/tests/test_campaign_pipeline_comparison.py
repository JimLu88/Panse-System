from io import BytesIO
import openpyxl
import pytest
from app.services.campaign_pipeline_comparison import compare_export, current_maps


def workbook(status='已发布设定', actual=100, sku='s1'):
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ['商品ID', '商品名称', '营销ID', '商品状态', 'SKUID', 'SKU名称',
               '一口价', '最低标价', '最低普惠券后价要求', '当前活动普惠券后价',
               '', '', '', '', '', '本次活动报名的商品价格']
    ws.append([]); ws.append([]); ws.append(headers)
    ws.append(['i1', '产品', 'm1', status, sku, '规格', 120, 0, 0, actual,
               None, None, None, None, None, 120])
    out = BytesIO(); wb.save(out); wb.close()
    return out.getvalue()


def maps(**kwargs):
    entry = {'target': 100, 'daily': 120, 'sku_code': 'code', **kwargs}
    return {'s1': entry}, {('i1', 's1'): entry}


def test_active_is_not_silently_successful():
    result = compare_export(workbook('活动中', 105), *maps())
    assert result['pipeline']['erp_checked_skus'] == 0
    assert result['pipeline_skip_reasons'] == {'非已发布设定状态': 1}
    assert result['legacy']['rows'][0]['verdict'] == '超2元报警'
    assert result['business_complete'] is False


@pytest.mark.parametrize('entries,actual,reason', [
    (({}, {}), 100, 'ERP 无精确映射'),
    (maps(is_placeholder=True), 100, '定制占位 SKU'),
    (maps(target=None), 100, '缺 ERP 目标价'),
    (maps(), None, '缺官方券后价'),
])
def test_skipped_rows_are_explained(entries, actual, reason):
    result = compare_export(workbook(actual=actual), *entries)
    assert result['pipeline_skip_reasons'][reason] == 1
    assert result['pipeline']['erp_checked_skus'] == 0


def test_matching_export_is_comparison_only():
    result = compare_export(workbook(), *maps())
    assert result['pipeline']['erp_checked_skus'] == 1
    assert result['pipeline']['erp_mismatches_count'] == 0
    assert not result['business_complete']
    assert not result['platform_writes_enabled']


def test_wrong_item_does_not_borrow_sku_price():
    prices, _ = maps()
    result = compare_export(workbook(), prices, {('other', 's1'): prices['s1']})
    assert result['legacy']['rows'][0]['verdict'] == '无映射'


@pytest.mark.parametrize('data', [b'', b'not xlsx', b'x' * (10 * 1024 * 1024 + 1)], ids=['empty', 'invalid', 'too_large'])
def test_invalid_file_rejected(data):
    with pytest.raises(ValueError):
        compare_export(data, {}, {})


def test_duplicate_mapping_rejected(monkeypatch):
    from types import SimpleNamespace as NS
    from app.services import campaign_service as svc
    monkeypatch.setattr(svc, 'target_prices', lambda *_: maps()[0])
    pair = (NS(sku_code='code'), NS(taobao_item_id='i1', taobao_sku_id='s1', alt_taobao_sku_ids=[]))
    monkeypatch.setattr(svc, '_mapped_pairs', lambda _: [pair, pair])
    with pytest.raises(ValueError, match='重复'):
        current_maps(None, None)


def test_api_uses_same_export_and_retains_receipt_on_comparison_failure(monkeypatch):
    import asyncio
    from fastapi import UploadFile
    from app.api import campaigns
    from app.services import campaign_pipeline_comparison as comparison
    from app.services import campaign_recon_service
    plan = object()
    monkeypatch.setattr(campaigns, '_get_plan', lambda *_: plan)
    monkeypatch.setattr(comparison, 'current_maps', lambda *_: maps())
    captured = []
    def reconcile(*args, **kwargs):
        captured.append(kwargs['activity_bytes'])
        return {'ok': True, 'report_id': 42}
    monkeypatch.setattr(campaign_recon_service, 'reconcile', reconcile)
    data = workbook()
    def run():
        return asyncio.run(campaigns.recon(1, UploadFile(file=BytesIO(data)), None, None, None, None))
    result = run()
    assert captured == [data]
    assert result['report_id'] == 42
    assert result['pipeline_comparison']['pipeline']['erp_checked_skus'] == 1
    def fail(*_):
        raise ValueError('bad mapping')
    monkeypatch.setattr(comparison, 'current_maps', fail)
    result = run()
    assert result['report_id'] == 42
    assert result['pipeline_comparison_error']


def test_standalone_endpoint_is_read_only(monkeypatch):
    import asyncio
    from fastapi import UploadFile
    from app.api import campaigns
    from app.services import campaign_pipeline_comparison as comparison
    monkeypatch.setattr(campaigns, '_get_plan', lambda *_: object())
    monkeypatch.setattr(comparison, 'current_maps', lambda *_: maps())
    result = asyncio.run(campaigns.pipeline_comparison(1, UploadFile(file=BytesIO(workbook())), None, None))
    assert result['business_complete'] is False
