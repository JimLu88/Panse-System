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
