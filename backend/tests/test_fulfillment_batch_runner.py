# -*- coding: utf-8 -*-
"""Unit and contract tests for fulfillment_batch_runner.py"""
import hashlib
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import sys
from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fulfillment_batch_runner import (
    reconcile_order_results,
    run_fulfillment,
    ErpClient,
    TaobaoConsignAutomation,
)


def test_reconcile_all_success():
    batch_rows = [
        {"order_no": "202611180001", "carrier": "中通快运", "tracking_no": "ZT111"},
        {"order_no": "202611180002", "carrier": "德邦快递", "tracking_no": "DB222"},
    ]
    task = {
        "taskId": 1001,
        "taskStatus": "FINISHED",
        "successCount": 2,
        "failCount": 0,
    }
    error_resp = {}
    order_results, final_state = reconcile_order_results(batch_rows, task, error_resp, expected_task_id=str(task["taskId"]))
    assert final_state == "unknown"
    assert len(order_results) == 2
    assert order_results["202611180001"]["status"] == "unknown"
    assert "缺少" in order_results["202611180001"]["detail"]
    assert order_results["202611180002"]["status"] == "unknown"


def test_reconcile_partial_failure():
    batch_rows = [
        {"order_no": "202611180001", "carrier": "中通快运", "tracking_no": "ZT111"},
        {"order_no": "202611180002", "carrier": "德邦快递", "tracking_no": "DB222"},
    ]
    task = {
        "taskId": 1002,
        "taskStatus": "PART_FAILED",
        "successCount": 1,
        "failCount": 1,
    }
    error_resp = {
        "success": True,
        "data": [
            {"orderNo": "202611180002", "errorMessage": "运单号已被使用"}
        ]
    }
    order_results, final_state = reconcile_order_results(batch_rows, task, error_resp, expected_task_id=str(task["taskId"]))
    assert final_state == "unknown"
    assert order_results["202611180001"]["status"] == "unknown"
    assert order_results["202611180002"]["status"] == "failed"
    assert "运单号已被使用" in order_results["202611180002"]["detail"]


def test_reconcile_complete_failure():
    batch_rows = [
        {"order_no": "202611180001", "carrier": "中通快运", "tracking_no": "ZT111"},
    ]
    task = {
        "taskId": 1003,
        "taskStatus": "ERROR",
        "successCount": 0,
        "failCount": 1,
        "errorMessage": "买家已发起退款拦截发货",
    }
    error_resp = {
        "success": True,
        "data": [
            {"orderNo": "202611180001", "errorMessage": "买家已发起退款"}
        ]
    }
    order_results, final_state = reconcile_order_results(batch_rows, task, error_resp, expected_task_id=str(task["taskId"]))
    assert final_state == "failed"
    assert order_results["202611180001"]["status"] == "failed"
    assert "买家已发起退款" in order_results["202611180001"]["detail"]


def test_reconcile_timeout_unknown():
    batch_rows = [
        {"order_no": "202611180001", "carrier": "中通快运", "tracking_no": "ZT111"},
    ]
    task = {
        "taskId": 1004,
        "taskStatus": "EXECUTING",
        "timeout": True,
    }
    error_resp = {}
    order_results, final_state = reconcile_order_results(batch_rows, task, error_resp, expected_task_id=str(task["taskId"]))
    assert final_state == "unknown"
    assert order_results["202611180001"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_shop_mismatch_blocks_execution():
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-1",
        "state": "prepared",
        "shop": "畔色店", # Batch is for 畔色店
        "count": 1,
        "sha256": sha,
        "rows": [{"order_no": "999", "carrier": "中通", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file
    mock_erp.precheck_batch.return_value = {"ok": True, "shop": "畔色店", "count": 1}

    mock_tb = AsyncMock()
    mock_tb.inspect_shop.return_value = {
        "nick": "tb697331180593",
        "userid": "2215699812811",
        "shopName": "畔色木作" # Edge is 畔色木作 (mismatch!)
    }

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:62739"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb):

        with pytest.raises(RuntimeError, match="店铺身份缺失或不匹配"):
            await run_fulfillment(batch_id="b-1",cdp_url="mock-only")

    # Verify no start or submission was recorded
    assert mock_erp.record_start.call_count == 0
    assert mock_tb.upload_and_submit.call_count == 0


@pytest.mark.asyncio
async def test_sha256_mismatch_blocks_execution():
    fake_file = b"PK\x03\x04test"

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-2",
        "state": "prepared",
        "shop": "畔色木作",
        "count": 1,
        "sha256": "0" * 64, # Wrong SHA
        "rows": [{"order_no": "999", "carrier": "中通", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:62739"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp):

        with pytest.raises(RuntimeError, match="批次文件SHA256"):
            await run_fulfillment(batch_id="b-2")


@pytest.mark.asyncio
async def test_precheck_failure_blocks_execution():
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-3",
        "state": "prepared",
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{"order_no": "999", "carrier": "中通", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file
    mock_erp.precheck_batch.side_effect = RuntimeError("ERP API 错误 [409] /api/shipments/batches/b-3/precheck: 订单已申请退款")

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:62739"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp):

        with pytest.raises(RuntimeError, match="订单已申请退款"):
            await run_fulfillment(batch_id="b-3")


@pytest.mark.asyncio
async def test_idempotent_resumption_skips_upload():
    """断线续接验证: 平台已存在匹配任务时，跳过 upload_and_submit，直接轮询并核对写回"""
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-4",
        "state": "submitting", # Interrupted in submitting
        "platform_task_id": "778899",
        "execution": {"attempt_id":"original", "started_at":"2026-01-01T00:00:00+00:00"},
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{"order_no": "999", "carrier": "中通快运", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file
    mock_erp.precheck_batch.return_value = {"ok": True, "shop": "畔色木作", "count": 1}

    mock_tb = AsyncMock()
    mock_tb.inspect_shop.return_value = {
        "nick": "tb697331180593",
        "userid": "2215699812811",
        "shopName": "畔色木作"
    }
    # 模拟平台查到了既有任务 778899
    mock_tb.find_existing_task.return_value = {
        "taskId": "778899",
        "taskStatus": "EXECUTING",
    }
    # 轮询终态返回 FINISHED
    mock_tb.read_shipping_records.return_value = []
    mock_tb.poll_task_result.return_value = {
        "taskId": "778899",
        "taskStatus": "FINISHED",
        "successCount": 1,
        "failCount": 0,
    }

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:62739"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb):

        receipt = await run_fulfillment(batch_id="b-4",cdp_url="mock-only")

    assert receipt["status"] == "needs_review"
    assert receipt["platform_task_id"] == "778899"
    # 验证没有调用 upload_and_submit (防重复上传!)
    assert mock_tb.upload_and_submit.call_count == 0
    # 验证写回了 finish
    assert mock_erp.record_finish.call_count == 1
    assert mock_erp.record_finish.call_args.args[1] == "778899"
    assert mock_erp.record_finish.call_args.args[2] == "FINISHED"
    assert mock_erp.record_finish.call_args.args[5]["999"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_dry_run_mode():
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-dry",
        "state": "prepared",
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{"order_no": "999", "carrier": "中通快运", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file
    mock_erp.precheck_batch.return_value = {"ok": True, "shop": "畔色木作", "count": 1}

    mock_tb = AsyncMock()
    mock_tb.inspect_shop.return_value = {
        "nick": "tb697331180593",
        "userid": "2215699812811",
        "shopName": "畔色木作"
    }
    mock_tb.find_existing_task.return_value = None
    mock_tb.upload_and_submit.return_value = "DRY_RUN_TASK_ID"

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:62739"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb):

        receipt = await run_fulfillment(batch_id="b-dry", dry_run=True)

    assert receipt["status"] == "dry_run_completed"
    assert receipt["platform_validated"] is False
    # In dry-run mode, upload_and_submit was called with dry_run=True
    assert mock_tb.upload_and_submit.call_count == 0
    mock_erp.record_start.assert_not_called()
    mock_tb.connect.assert_not_called()
    # In dry-run mode, finish writeback is NOT called
    assert mock_erp.record_finish.call_count == 0


@pytest.mark.asyncio
async def test_inspect_shop_real_config():
    tb = TaobaoConsignAutomation("mock-cdp")
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = {
        "shopName": "畔色木作",
        "shopId": "431991266",
        "nick": "tb697331180593",
        "userid": "2215699812811",
        "shopStatus": "open",
        "sub": False,
    }
    shop = await tb.inspect_shop()
    assert shop["shopName"] == "畔色木作"
    assert shop["shopId"] == "431991266"
    assert shop["userid"] == "2215699812811"


@pytest.mark.asyncio
async def test_inspect_shop_missing_raises():
    tb = TaobaoConsignAutomation("mock-cdp")
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = None
    with pytest.raises(RuntimeError, match="无法从千牛工作台配置"):
        await tb.inspect_shop()


@pytest.mark.parametrize('missing', ['status', 'confirmation', 'trade_id', 'wrong_trade_id', 'false_confirmation'])
@pytest.mark.asyncio
async def test_reader_cannot_fill_missing_platform_facts(missing):
    tb = TaobaoConsignAutomation('mock-only')
    tb.inspect_shop = AsyncMock(return_value={'shopName': 'test', 'shopId': 'test-shop', 'userid': 'test-user'})
    row = {'order_no': '9999999999999991', 'carrier': 'TEST', 'tracking_no': 'TEST001',
           'shop': 'test', 'sub_order_nos': ['synthetic-child']}
    pkg = {'tradeId': row['order_no'], 'tradeStatus': 'WAIT_BUYER_CONFIRM_GOODS', 'sellerConfirm': True,
           'logisticsInfos': [{'label': 'TEST', 'value': 'TEST001'}],
           'children': [{'orderGoodsId': 'synthetic-child'}]}
    if missing == 'status': pkg.pop('tradeStatus')
    if missing == 'confirmation': pkg.pop('sellerConfirm')
    if missing == 'trade_id': pkg.pop('tradeId')
    if missing == 'wrong_trade_id': pkg['tradeId'] = '9999999999999992'
    if missing == 'false_confirmation': pkg['sellerConfirm'] = False
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = {'success': True, 'data': [pkg]}
    assert await tb.read_shipping_records([row]) == []


@pytest.mark.asyncio
async def test_reader_rejects_duplicate_matching_packages():
    tb = TaobaoConsignAutomation('mock-only')
    tb.inspect_shop = AsyncMock(return_value={'shopName': 'test'})
    row = {'order_no': '9999999999999991', 'carrier': 'TEST', 'tracking_no': 'TEST001'}
    pkg = {'tradeId': row['order_no'], 'tradeStatus': 'WAIT_BUYER_CONFIRM_GOODS', 'sellerConfirm': True,
           'logisticsInfos': [{'label': 'TEST', 'value': 'TEST001'}]}
    tb.page = AsyncMock(); tb.page.evaluate.return_value = {'success': True, 'data': [pkg, pkg]}
    assert await tb.read_shipping_records([row]) == []


@pytest.mark.asyncio
async def test_read_shipping_records_matching_produces_confirmed_proof():
    from app.services.fulfillment_receipts import reconcile
    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={
        "shopName": "畔色木作", "shopId": "431991266", "userid": "2215699812811"
    })
    row = {
        "order_no": "5127598658673000805",
        "shop": "畔色木作",
        "carrier": "圆通速递",
        "tracking_no": "YT2596963266216",
        "sub_order_nos": ["5127598658673046814", "5127598658673073642"]
    }
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = {
        "success": True,
        "data": [{
            # Synthetic candidate schema, not evidence of live platform fields.
            "tradeStatus": "WAIT_BUYER_CONFIRM_GOODS", "sellerConfirm": True,
            "baseInfo": {"fields": [{"name": "tradeId", "value": "5127598658673000805"}, {"name": "orderCode", "value": "LP001"}]},
            "logisticsInfos": [
                {"label": "圆通速递", "value": "YT2596963266216"},
                {"label": "发货方式", "value": "自己联系物流"}
            ],
            "children": [
                {"orderGoodsId": "5127598658673046814"},
                {"orderGoodsId": "5127598658673073642"}
            ]
        }]
    }
    proofs = await tb.read_shipping_records([row])
    assert len(proofs) == 1
    p = proofs[0]
    assert p["order_no"] == "5127598658673000805"
    assert p["carrier"] == "圆通速递"
    assert p["tracking_no"] == "YT2596963266216"
    assert p["shop"] == "畔色木作"
    assert p["seller_confirm"] is True
    assert p["trade_status"] == "WAIT_BUYER_CONFIRM_GOODS"

    # Now verify it satisfies reconcile contract to 'confirmed'
    task = {"taskId": "T1", "taskStatus": "FINISHED", "successCount": 1, "failCount": 0}
    results, batch_st = reconcile([row], task, {}, proofs, expected_task_id="T1")
    assert batch_st == "confirmed"
    assert results[row["order_no"]]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_read_shipping_records_mismatched_children_stays_unknown():
    from app.services.fulfillment_receipts import reconcile
    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={
        "shopName": "畔色木作", "shopId": "431991266", "userid": "2215699812811"
    })
    row = {
        "order_no": "5127598658673000805",
        "shop": "畔色木作",
        "carrier": "圆通速递",
        "tracking_no": "YT2596963266216",
        "sub_order_nos": ["5127598658673046814", "5127598658673073642", "MISSING_SUB"]
    }
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = {
        "success": True,
        "data": [{
            # Synthetic candidate schema, not evidence of live platform fields.
            "tradeStatus": "WAIT_BUYER_CONFIRM_GOODS", "sellerConfirm": True,
            "baseInfo": {"fields": [{"name": "tradeId", "value": "5127598658673000805"}, {"name": "orderCode", "value": "LP001"}]},
            "logisticsInfos": [
                {"label": "圆通速递", "value": "YT2596963266216"}
            ],
            "children": [
                {"orderGoodsId": "5127598658673046814"},
                {"orderGoodsId": "5127598658673073642"}
            ]
        }]
    }
    proofs = await tb.read_shipping_records([row])
    assert len(proofs) == 1

    # In reconcile, since sub_order_nos does not match expected_children, status must remain unknown
    task = {"taskId": "T1", "taskStatus": "FINISHED", "successCount": 1, "failCount": 0}
    results, batch_st = reconcile([row], task, {}, proofs, expected_task_id="T1")
    assert batch_st == "unknown"
    assert results[row["order_no"]]["status"] == "unknown"


@pytest.mark.asyncio
async def test_read_shipping_records_timestamp_only_schema_stays_unknown():
    from app.services.fulfillment_receipts import reconcile
    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={
        "shopName": "畔色木作", "shopId": "test-shop", "userid": "test-user"
    })
    row = {
        "order_no": "9999999999999991",
        "shop": "畔色木作",
        "carrier": "中通快运",
        "tracking_no": "TEST001",
        "sub_order_nos": ["9999999999999992", "9999999999999993"]
    }
    tb.page = AsyncMock()
    # Reported platform shape; no current tradeStatus/sellerConfirm facts.
    # This fixture is not independent live-platform acceptance evidence.
    tb.page.evaluate.return_value = {
        "success": True,
        "data": [{
            "baseInfo": {
                "fields": [
                    {"name": "orderId", "value": "123456789012"},
                    {"name": "tradeId", "value": "9999999999999991"},
                    {"name": "orderCode", "value": "LP00123456789012"}
                ],
                "left": [{"label": "订单编号", "value": "9999999999999991"}],
                "right": [
                    {"label": "发货时间", "value": "2026-11-16 11:25:33"},
                    {"label": "支付时间", "value": "2026-11-11 22:54:22"}
                ]
            },
            "logisticsInfos": [
                {"label": "中通快运", "value": "TEST001"},
                {"label": "发货方式", "value": "自己联系物流"}
            ],
            "children": [
                {"orderGoodsId": "9999999999999992", "orderGoodsName": "合成测试商品"},
                {"orderGoodsId": "9999999999999993", "orderGoodsName": "合成测试商品"}
            ]
        }]
    }
    proofs = await tb.read_shipping_records([row])
    assert proofs == []

    task = {"taskId": "GEI@consign2_batch_offline_consign@123", "taskStatus": "FINISHED", "successCount": 1, "failCount": 0}
    results, batch_st = reconcile([row], task, {}, proofs, expected_task_id="GEI@consign2_batch_offline_consign@123")
    assert batch_st == "unknown"
    assert results[row["order_no"]]["status"] == "unknown"


@pytest.mark.parametrize('ship_time', ['--', '未发货', '2026-11-16 11:25:33'])
@pytest.mark.parametrize('missing', ['status', 'confirmation', 'both'])
@pytest.mark.asyncio
async def test_timestamp_never_supplies_missing_shipping_facts(ship_time, missing):
    tb = TaobaoConsignAutomation('offline-only')
    tb.inspect_shop = AsyncMock(return_value={'shopName': 'TEST'})
    row = {'order_no': '9999999999999991', 'shop': 'TEST', 'carrier': 'TEST',
           'tracking_no': 'TEST001', 'sub_order_nos': ['synthetic-child']}
    pkg = {'tradeId': row['order_no'], 'tradeStatus': 'WAIT_BUYER_CONFIRM_GOODS',
           'sellerConfirm': True, 'baseInfo': {'right': [{'label': '发货时间', 'value': ship_time}]},
           'logisticsInfos': [{'label': 'TEST', 'value': 'TEST001'}],
           'children': [{'orderGoodsId': 'synthetic-child'}]}
    if missing in ('status', 'both'):
        pkg.pop('tradeStatus')
    if missing in ('confirmation', 'both'):
        pkg.pop('sellerConfirm')
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = {'success': True, 'data': [pkg]}
    proofs = await tb.read_shipping_records([row])
    assert proofs == []
    task = {'taskId': 'T1', 'taskStatus': 'FINISHED', 'successCount': 1, 'failCount': 0}
    results, state = reconcile_order_results([row], task, {}, proofs, expected_task_id='T1')
    assert state == 'unknown'
    assert results[row['order_no']]['status'] == 'unknown'


@pytest.mark.parametrize('field,value', [('tradeStatus', 'WAIT_SELLER_SEND_GOODS'),
                                        ('sellerConfirm', False), ('sellerConfirm', 'true')])
@pytest.mark.asyncio
async def test_reader_conflicting_shipping_sources_stay_unknown(field, value):
    tb = TaobaoConsignAutomation('offline-only')
    tb.inspect_shop = AsyncMock(return_value={'shopName': 'TEST'})
    row = {'order_no': '9999999999999991', 'carrier': 'TEST', 'tracking_no': 'TEST001'}
    pkg = {'tradeId': row['order_no'], 'tradeStatus': 'WAIT_BUYER_CONFIRM_GOODS',
           'sellerConfirm': True, 'baseInfo': {'fields': [{'name': field, 'value': value}]},
           'logisticsInfos': [{'label': 'TEST', 'value': 'TEST001'}]}
    tb.page = AsyncMock()
    tb.page.evaluate.return_value = {'success': True, 'data': [pkg]}
    assert await tb.read_shipping_records([row]) == []


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_live_adapter_verified_defaults_to_true_after_acceptance():
    tb = TaobaoConsignAutomation('offline-only')
    assert tb.live_adapter_verified is True


@pytest.mark.asyncio
async def test_unaccepted_adapter_cannot_upload():
    tb = TaobaoConsignAutomation('offline-only')
    tb.live_adapter_verified = False
    tb.page = AsyncMock()
    with pytest.raises(RuntimeError, match='尚未经过真实页面验收'):
        await tb.upload_and_submit('unused.xlsx')
    tb.page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_upload_and_submit_intercepts_offline_consign_batch_upload():
    """核对上传接口：确保准确拦截 offline_consign_batch_upload.do 并解析出 Task ID"""
    tb = TaobaoConsignAutomation('offline-only')
    assert tb.live_adapter_verified is True

    mock_page = AsyncMock()
    mock_file_input = AsyncMock()
    mock_ok_btn = AsyncMock()
    mock_ok_btn.count = AsyncMock(return_value=1)
    mock_ok_btn.is_disabled = AsyncMock(return_value=False)
    mock_ok_btn.first = mock_ok_btn
    mock_ok_btn.click = AsyncMock()

    dialog_locator = AsyncMock()
    dialog_locator.count = AsyncMock(return_value=1)

    import_btn = AsyncMock()
    import_btn.count = AsyncMock(return_value=1)
    import_btn.first = import_btn
    import_btn.click = AsyncMock()

    def mock_locator(sel):
        if 'button:has-text("批量导入")' in sel:
            return import_btn
        if sel == '.next-dialog':
            return dialog_locator
        if 'input[type="file"]' in sel:
            return mock_file_input
        if 'button.next-btn-primary' in sel or 'button:has-text("确认")' in sel:
            return mock_ok_btn
        if 'button:has-text("取消")' in sel:
            cancel = AsyncMock()
            cancel.count = AsyncMock(return_value=0)
            cancel.first = cancel
            return cancel
        m = AsyncMock()
        m.count = AsyncMock(return_value=0)
        m.first = m
        return m

    mock_page.locator = MagicMock(side_effect=mock_locator)

    # 模拟 expect_response
    real_task_id = "GEI@consign2_batch_offline_consign@261121145759@3798"
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value={
        "data": {"taskId": real_task_id},
        "success": True
    })

    async def _get_resp():
        return mock_resp

    class DummyRespInfo:
        @property
        def value(self):
            return _get_resp()

    class DummyExpectResponse:
        def __init__(self, predicate, timeout):
            self.predicate = predicate
            # 验证 predicate 能够匹配真实请求 offline_consign_batch_upload.do
            dummy_req = MagicMock()
            dummy_req.url = "https://wuliu2.taobao.com/gei/import/offline_consign_batch_upload.do"
            dummy_req.request.method = "POST"
            assert self.predicate(dummy_req) is True

            # 也兼容 consign2_batch_offline_consign
            dummy_req2 = MagicMock()
            dummy_req2.url = "https://wuliu2.taobao.com/gei/import/consign2_batch_offline_consign"
            dummy_req2.request.method = "POST"
            assert self.predicate(dummy_req2) is True

            # 非 POST 或无关 URL 不匹配
            dummy_get = MagicMock()
            dummy_get.url = "https://wuliu2.taobao.com/gei/import/offline_consign_batch_upload.do"
            dummy_get.request.method = "GET"
            assert self.predicate(dummy_get) is False

        async def __aenter__(self):
            return DummyRespInfo()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_page.expect_response = MagicMock(side_effect=lambda pred, timeout: DummyExpectResponse(pred, timeout))
    tb.page = mock_page
    tb.get_task_list = AsyncMock(return_value=[{
        "taskId": real_task_id,
        "taskCode": "consign2_batch_offline_consign",
        "taskStatus": "FINISHED"
    }])

    task_id = await tb.upload_and_submit("dummy.xlsx")
    assert task_id == real_task_id


@pytest.mark.parametrize('success', [True, False, None, 'true'])
@pytest.mark.asyncio
async def test_nested_failure_details_require_explicit_success(success):
    tb = TaobaoConsignAutomation('offline-only')
    tb.page = AsyncMock()
    errors = [{'orderNo': 'synthetic-order', 'errorMessage': 'rejected'}]
    response = {'data': {'errors': errors}}
    if success is not None:
        response['success'] = success
    tb.page.evaluate.return_value = response
    result = await tb.fetch_error_details('task/one')
    assert result == {'success': success is True, 'data': errors}
    assert 'task%2Fone' in tb.page.evaluate.call_args.args[0]


@pytest.mark.asyncio
async def test_run_fulfillment_normal_submission_success():
    """正常提交流程测试: 首次提交 -> 上传 -> 受理TaskId -> 轮询成功 -> 逐单核对一致 -> 回写 confirmed"""
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()
    task_id = "GEI@consign2_batch_offline_consign@261119121825@6097"

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-normal-1",
        "state": "prepared",
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{
            "order_no": "5127439575180167827",
            "shop": "畔色木作",
            "carrier": "圆通速递",
            "tracking_no": "YT2551171304891",
            "sub_order_nos": ["5127439575180167827"]
        }]
    }
    mock_erp.download_batch_file.return_value = fake_file
    mock_erp.precheck_batch.return_value = {"ok": True, "shop": "畔色木作", "count": 1}
    mock_erp.record_start.return_value = {
        "upload_allowed": True,
        "execution": {"started_at": "2026-11-19T12:00:00+00:00"}
    }
    mock_erp.record_submission.return_value = {"ok": True}
    mock_erp.record_finish.return_value = {"ok": True}

    mock_tb = AsyncMock()
    mock_tb.inspect_shop.return_value = {
        "shopName": "畔色木作",
        "shopId": "431991266",
        "userid": "2215699812811"
    }
    mock_tb.upload_and_submit.return_value = task_id
    mock_tb.poll_task_result.return_value = {
        "taskId": task_id,
        "taskStatus": "FINISHED",
        "successCount": 1,
        "failCount": 0
    }
    mock_tb.read_shipping_records.return_value = [{
        "order_no": "5127439575180167827",
        "observed_at": "2026-11-19T12:05:00+00:00",
        "source": "taobao_logistics_readback",
        "evidence_ref": "wuliu2:queryOrderList:sha256:fake",
        "shop": "畔色木作",
        "carrier": "圆通速递",
        "tracking_no": "YT2551171304891",
        "seller_confirm": True,
        "trade_status": "WAIT_BUYER_CONFIRM_GOODS",
        "sub_order_nos": ["5127439575180167827"],
        "raw_order": {"tid": "5127439575180167827"}
    }]

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:54986"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb), \
         patch.object(TaobaoConsignAutomation, "live_adapter_verified", True):

        receipt = await run_fulfillment(batch_id="b-normal-1", cdp_url="http://127.0.0.1:54986")

    assert receipt["status"] == "completed"
    assert receipt["platform_task_id"] == task_id
    assert receipt["final_batch_state"] == "confirmed"
    mock_erp.record_start.assert_called_once()
    mock_tb.upload_and_submit.assert_called_once()
    mock_erp.record_submission.assert_called_once()
    assert mock_erp.record_submission.call_args.args[1] == task_id
    mock_erp.record_finish.assert_called_once()
    assert mock_erp.record_finish.call_args.args[1] == task_id
    assert mock_erp.record_finish.call_args.args[2] == "FINISHED"
    assert mock_erp.record_finish.call_args.args[3] == 1  # success_count
    assert mock_erp.record_finish.call_args.args[4] == 0  # fail_count
    assert mock_erp.record_finish.call_args.args[5]["5127439575180167827"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_run_fulfillment_poll_timeout_unknown():
    """超时保护测试: 任务轮询超时 -> 保持 unknown，不判定成功，写回原始凭证并保留批次"""
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()
    task_id = "GEI@consign2_batch_offline_consign@timeout_task"

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-timeout-1",
        "state": "prepared",
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{
            "order_no": "5127439575180167827",
            "shop": "畔色木作",
            "carrier": "圆通速递",
            "tracking_no": "YT2551171304891",
            "sub_order_nos": ["5127439575180167827"]
        }]
    }
    mock_erp.download_batch_file.return_value = fake_file
    mock_erp.precheck_batch.return_value = {"ok": True, "shop": "畔色木作", "count": 1}
    mock_erp.record_start.return_value = {
        "upload_allowed": True,
        "execution": {"started_at": "2026-11-19T12:00:00+00:00"}
    }
    mock_erp.record_submission.return_value = {"ok": True}
    mock_erp.record_finish.return_value = {"ok": True}

    mock_tb = AsyncMock()
    mock_tb.inspect_shop.return_value = {
        "shopName": "畔色木作",
        "shopId": "431991266",
        "userid": "2215699812811"
    }
    mock_tb.upload_and_submit.return_value = task_id
    mock_tb.poll_task_result.return_value = {
        "taskId": task_id,
        "taskStatus": "EXECUTING",
        "timeout": True
    }
    mock_tb.read_shipping_records.return_value = []

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:54986"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb), \
         patch.object(TaobaoConsignAutomation, "live_adapter_verified", True):

        receipt = await run_fulfillment(batch_id="b-timeout-1", cdp_url="http://127.0.0.1:54986")

    assert receipt["status"] == "needs_review"
    assert receipt["platform_task_id"] == task_id
    assert receipt["final_batch_state"] == "unknown"
    mock_erp.record_finish.assert_called_once()
    assert mock_erp.record_finish.call_args.args[2] == "EXECUTING"
    assert mock_erp.record_finish.call_args.args[5]["5127439575180167827"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_run_fulfillment_repeated_start_already_confirmed():
    """重复启动防护: 已确认批次再次启动 -> 直接返回 already_confirmed，无任何浏览器或写操作"""
    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-conf-1",
        "state": "confirmed",
        "shop": "畔色木作",
        "count": 1,
        "rows": [{"order_no": "999"}]
    }

    mock_tb = AsyncMock()

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:54986"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb):

        receipt = await run_fulfillment(batch_id="b-conf-1")

    assert receipt["status"] == "already_confirmed"
    assert receipt["batch_id"] == "b-conf-1"
    mock_tb.connect.assert_not_called()
    mock_erp.record_start.assert_not_called()
    mock_tb.upload_and_submit.assert_not_called()
    mock_erp.record_finish.assert_not_called()


@pytest.mark.asyncio
async def test_run_fulfillment_submitting_without_task_id_blocks_reupload():
    """断线防护: 状态为 submitting 但无 platform_task_id -> 明确拒绝重新上传，保留原占位"""
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-lost-1",
        "state": "submitting",
        "platform_task_id": None,  # Lost submission response
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{"order_no": "999", "carrier": "圆通", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file

    mock_tb = AsyncMock()

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:54986"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb):

        with pytest.raises(RuntimeError, match="原批次提交结果未知且无已绑定平台任务"):
            await run_fulfillment(batch_id="b-lost-1", cdp_url="http://127.0.0.1:54986")

    mock_tb.upload_and_submit.assert_not_called()
    mock_erp.record_start.assert_not_called()


@pytest.mark.asyncio
async def test_real_order_readback_playback_confirms_with_live_responses():
    """
    实单回放测试: 使用本次 5127439575180167827 真实保存的千牛权威详情与物流中心真实报文，
    验证程序自主读取并核对交易状态(WAIT_BUYER_CONFIRM_GOODS)、圆通运单(YT2551171304891)及子单，
    经 reconcile 合同对账后产出 confirmed 终态。
    """
    from app.services.fulfillment_receipts import reconcile

    # 载入现场真实证据载荷
    real_qn_trade_detail = {
        "mainOrder": {
            "id": 5127439575180167827,
            "tradeStatus": "WAIT_BUYER_CONFIRM_GOODS",
            "statusInfo": {"text": "当前订单状态：商家已发货，等待买家确认", "type": "t0"},
            "seller": {"id": 2215699812811, "nick": "tb697331180593"},
            "subOrders": [
                {
                    "idStr": "5127439575180167827",
                    "quantity": 3,
                    "tradeStatus": [{"content": [{"value": "未确认收货"}]}]
                }
            ],
            "tabs": [
                {
                    "id": "logistics",
                    "content": {
                        "logisticsName": "圆通速递",
                        "logisticsNum": "YT2551171304891"
                    }
                }
            ]
        }
    }

    real_wuliu2_logistics = {
        "success": True,
        "pageIndex": 1,
        "pageSize": 10,
        "total": 1,
        "data": [
            {
                "baseInfo": {
                    "fields": [
                        {"name": "orderId", "value": "856480103183"},
                        {"name": "tradeId", "value": "5127439575180167827"},
                        {"name": "orderCode", "value": "LP00856480103183"}
                    ],
                    "left": [{"label": "订单编号", "value": "5127439575180167827"}],
                    "right": [
                        {"label": "发货时间", "value": "2026-11-21 14:58:00"},
                        {"label": "支付时间", "value": "2026-11-18 23:54:31"}
                    ]
                },
                "logisticsInfos": [
                    {"label": "圆通速递", "value": "YT2551171304891"},
                    {"label": "发货方式", "value": "自己联系物流"}
                ],
                "children": [
                    {"orderGoodsId": "5127439575180167827", "orderGoodsName": "定制实木样块"}
                ],
                "extraInfo": {"canReissueConsign": True, "canUpdateMailNo": True}
            }
        ]
    }

    row = {
        "order_no": "5127439575180167827",
        "shop": "畔色木作",
        "carrier": "圆通速递",
        "tracking_no": "YT2551171304891",
        "sub_order_nos": ["5127439575180167827"]
    }

    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={
        "shopName": "畔色木作", "shopId": "431991266", "userid": "2215699812811"
    })

    async def mock_evaluate(script):
        if "trade_order_detail_qian_niu" in script:
            return real_qn_trade_detail
        if "queryOrderList" in script:
            return real_wuliu2_logistics
        raise ValueError(f"Unexpected script: {script}")

    tb.page = AsyncMock()
    tb.page.evaluate.side_effect = mock_evaluate

    proofs = await tb.read_shipping_records([row])
    assert len(proofs) == 1
    p = proofs[0]
    assert p["order_no"] == "5127439575180167827"
    assert p["carrier"] == "圆通速递"
    assert p["tracking_no"] == "YT2551171304891"
    assert p["trade_status"] == "WAIT_BUYER_CONFIRM_GOODS"
    assert p["seller_confirm"] is True
    assert p["sub_order_nos"] == ["5127439575180167827"]
    assert p["source"] == "taobao_logistics_readback"
    assert "LP00856480103183" in p["evidence_ref"]

    # 结合本次真实平台任务编号回放
    real_task = {
        "taskId": "GEI@consign2_batch_offline_consign@261121145759@3798",
        "taskStatus": "FINISHED",
        "successCount": 1,
        "failCount": 0
    }
    results, batch_st = reconcile(
        [row], real_task, {}, proofs,
        expected_task_id="GEI@consign2_batch_offline_consign@261121145759@3798"
    )
    assert batch_st == "confirmed"
    assert results[row["order_no"]]["status"] == "confirmed"
    assert results[row["order_no"]]["detail"] == "官方逐单物流、店铺及全部子单已核对"


@pytest.mark.asyncio
async def test_real_order_readback_playback_carrier_or_tracking_mismatch_fails():
    """实单回放负向测试: 运单号或物流公司不匹配时，不能确认发货"""
    from app.services.fulfillment_receipts import reconcile

    real_qn_trade_detail = {
        "mainOrder": {
            "id": 5127439575180167827,
            "tradeStatus": "WAIT_BUYER_CONFIRM_GOODS",
            "statusInfo": {"text": "当前订单状态：商家已发货，等待买家确认"},
            "subOrders": [{"idStr": "5127439575180167827"}]
        }
    }
    real_wuliu2_logistics = {
        "success": True,
        "data": [{
            "baseInfo": {"fields": [{"name": "orderCode", "value": "LP00856480103183"}, {"name": "tradeId", "value": "5127439575180167827"}]},
            "logisticsInfos": [{"label": "圆通速递", "value": "YT2551171304891"}],
            "children": [{"orderGoodsId": "5127439575180167827"}]
        }]
    }

    # 期望的 ERP 记录为顺丰速运或错误单号
    mismatched_row = {
        "order_no": "5127439575180167827",
        "shop": "畔色木作",
        "carrier": "顺丰速运",
        "tracking_no": "SF1234567890",
        "sub_order_nos": ["5127439575180167827"]
    }

    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={"shopName": "畔色木作", "shopId": "431991266", "userid": "2215699812811"})
    tb.page = AsyncMock()
    tb.page.evaluate.side_effect = lambda s: real_qn_trade_detail if "trade_order_detail_qian_niu" in s else real_wuliu2_logistics

    proofs = await tb.read_shipping_records([mismatched_row])
    assert proofs == []

    task = {"taskId": "GEI@consign2_batch_offline_consign@261121145759@3798", "taskStatus": "FINISHED", "successCount": 1, "failCount": 0}
    results, batch_st = reconcile([mismatched_row], task, {}, proofs, expected_task_id=task["taskId"])
    assert batch_st == "unknown"
    assert results[mismatched_row["order_no"]]["status"] == "unknown"



@pytest.mark.asyncio
async def test_run_fulfillment_disconnect_recovery_missing_task_blocks():
    """断线恢复验证: platform_task_id 在平台任务列表中不存在 -> 报错终止，严禁改用其他任务或重新上传"""
    fake_file = b"PK\x03\x04test"
    sha = hashlib.sha256(fake_file).hexdigest()

    mock_erp = MagicMock()
    mock_erp.get_batch.return_value = {
        "id": "b-recov-missing",
        "state": "submitting",
        "platform_task_id": "GEI@consign2_batch_offline_consign@99999",
        "execution": {"attempt_id": "att-1", "started_at": "2026-11-19T12:00:00+00:00"},
        "shop": "畔色木作",
        "count": 1,
        "sha256": sha,
        "rows": [{"order_no": "999", "carrier": "圆通", "tracking_no": "111"}]
    }
    mock_erp.download_batch_file.return_value = fake_file

    mock_tb = AsyncMock()
    mock_tb.inspect_shop.return_value = {
        "shopName": "畔色木作",
        "shopId": "431991266",
        "userid": "2215699812811"
    }
    # 模拟平台未找到该任务
    mock_tb.find_existing_task.return_value = None

    with patch("fulfillment_batch_runner.discover_edge_cdp", return_value="http://127.0.0.1:54986"), \
         patch("fulfillment_batch_runner.fetch_admin_token", return_value="mock-token"), \
         patch("fulfillment_batch_runner.ErpClient", return_value=mock_erp), \
         patch("fulfillment_batch_runner.TaobaoConsignAutomation", return_value=mock_tb), \
         patch.object(TaobaoConsignAutomation, "live_adapter_verified", True):

        with pytest.raises(RuntimeError, match="尚未读到原平台任务，禁止改用其他任务或重新上传"):
            await run_fulfillment(batch_id="b-recov-missing", cdp_url="http://127.0.0.1:54986")

    mock_tb.upload_and_submit.assert_not_called()
    mock_erp.record_start.assert_not_called()


@pytest.mark.asyncio
async def test_real_order_readback_playback_confirms_with_live_responses():
    """
    实单回放测试: 使用本次 5127439575180167827 真实保存的千牛权威详情与物流中心真实报文，
    验证程序自主读取并核对交易状态(WAIT_BUYER_CONFIRM_GOODS)、圆通运单(YT2551171304891)及子单，
    经 reconcile 合同对账后产出 confirmed 终态。
    """
    from app.services.fulfillment_receipts import reconcile

    # 载入现场真实证据载荷
    real_qn_trade_detail = {
        "mainOrder": {
            "id": 5127439575180167827,
            "tradeStatus": "WAIT_BUYER_CONFIRM_GOODS",
            "statusInfo": {"text": "当前订单状态：商家已发货，等待买家确认", "type": "t0"},
            "seller": {"id": 2215699812811, "nick": "tb697331180593"},
            "subOrders": [
                {
                    "idStr": "5127439575180167827",
                    "quantity": 3,
                    "tradeStatus": [{"content": [{"value": "未确认收货"}]}]
                }
            ],
            "tabs": [
                {
                    "id": "logistics",
                    "content": {
                        "logisticsName": "圆通速递",
                        "logisticsNum": "YT2551171304891"
                    }
                }
            ]
        }
    }

    real_wuliu2_logistics = {
        "success": True,
        "pageIndex": 1,
        "pageSize": 10,
        "total": 1,
        "data": [
            {
                "baseInfo": {
                    "fields": [
                        {"name": "orderId", "value": "856480103183"},
                        {"name": "tradeId", "value": "5127439575180167827"},
                        {"name": "orderCode", "value": "LP00856480103183"}
                    ],
                    "left": [{"label": "订单编号", "value": "5127439575180167827"}],
                    "right": [
                        {"label": "发货时间", "value": "2026-11-21 14:58:00"},
                        {"label": "支付时间", "value": "2026-11-18 23:54:31"}
                    ]
                },
                "logisticsInfos": [
                    {"label": "圆通速递", "value": "YT2551171304891"},
                    {"label": "发货方式", "value": "自己联系物流"}
                ],
                "children": [
                    {"orderGoodsId": "5127439575180167827", "orderGoodsName": "定制实木样块"}
                ],
                "extraInfo": {"canReissueConsign": True, "canUpdateMailNo": True}
            }
        ]
    }

    row = {
        "order_no": "5127439575180167827",
        "shop": "畔色木作",
        "carrier": "圆通速递",
        "tracking_no": "YT2551171304891",
        "sub_order_nos": ["5127439575180167827"]
    }

    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={
        "shopName": "畔色木作", "shopId": "431991266", "userid": "2215699812811"
    })

    async def mock_evaluate(script):
        if "trade_order_detail_qian_niu" in script:
            return real_qn_trade_detail
        if "queryOrderList" in script:
            return real_wuliu2_logistics
        raise ValueError(f"Unexpected script: {script}")

    tb.page = AsyncMock()
    tb.page.evaluate.side_effect = mock_evaluate

    proofs = await tb.read_shipping_records([row])
    assert len(proofs) == 1
    p = proofs[0]
    assert p["order_no"] == "5127439575180167827"
    assert p["carrier"] == "圆通速递"
    assert p["tracking_no"] == "YT2551171304891"
    assert p["trade_status"] == "WAIT_BUYER_CONFIRM_GOODS"
    assert p["seller_confirm"] is True
    assert p["sub_order_nos"] == ["5127439575180167827"]
    assert p["source"] == "taobao_logistics_readback"
    assert "LP00856480103183" in p["evidence_ref"]

    # 结合本次真实平台任务编号回放
    real_task = {
        "taskId": "GEI@consign2_batch_offline_consign@261121145759@3798",
        "taskStatus": "FINISHED",
        "successCount": 1,
        "failCount": 0
    }
    results, batch_st = reconcile(
        [row], real_task, {}, proofs,
        expected_task_id="GEI@consign2_batch_offline_consign@261121145759@3798"
    )
    assert batch_st == "confirmed"
    assert results[row["order_no"]]["status"] == "confirmed"
    assert results[row["order_no"]]["detail"] == "官方逐单物流、店铺及全部子单已核对"


@pytest.mark.asyncio
async def test_real_order_readback_playback_carrier_or_tracking_mismatch_fails():
    """实单回放负向测试: 运单号或物流公司不匹配时，不能确认发货"""
    from app.services.fulfillment_receipts import reconcile

    real_qn_trade_detail = {
        "mainOrder": {
            "id": 5127439575180167827,
            "tradeStatus": "WAIT_BUYER_CONFIRM_GOODS",
            "statusInfo": {"text": "当前订单状态：商家已发货，等待买家确认"},
            "subOrders": [{"idStr": "5127439575180167827"}]
        }
    }
    real_wuliu2_logistics = {
        "success": True,
        "data": [{
            "baseInfo": {"fields": [{"name": "orderCode", "value": "LP00856480103183"}, {"name": "tradeId", "value": "5127439575180167827"}]},
            "logisticsInfos": [{"label": "圆通速递", "value": "YT2551171304891"}],
            "children": [{"orderGoodsId": "5127439575180167827"}]
        }]
    }

    # 期望的 ERP 记录为顺丰速运或错误单号
    mismatched_row = {
        "order_no": "5127439575180167827",
        "shop": "畔色木作",
        "carrier": "顺丰速运",
        "tracking_no": "SF1234567890",
        "sub_order_nos": ["5127439575180167827"]
    }

    tb = TaobaoConsignAutomation("mock-cdp")
    tb.inspect_shop = AsyncMock(return_value={"shopName": "畔色木作", "shopId": "431991266", "userid": "2215699812811"})
    tb.page = AsyncMock()
    tb.page.evaluate.side_effect = lambda s: real_qn_trade_detail if "trade_order_detail_qian_niu" in s else real_wuliu2_logistics

    proofs = await tb.read_shipping_records([mismatched_row])
    assert proofs == []

    task = {"taskId": "GEI@consign2_batch_offline_consign@261121145759@3798", "taskStatus": "FINISHED", "successCount": 1, "failCount": 0}
    results, batch_st = reconcile([mismatched_row], task, {}, proofs, expected_task_id=task["taskId"])
    assert batch_st == "unknown"
    assert results[mismatched_row["order_no"]]["status"] == "unknown"
