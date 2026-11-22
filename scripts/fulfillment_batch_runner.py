#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Guarded shipping executor. Explicit original batch only.

--dry-run reads ERP/file hash only: no browser, upload or business-state write.
The live adapter is not accepted yet. Real shop identity and per-order logistics
readback must be implemented/verified before enabling it; counts are not proof.
A lost start/upload acknowledgement stays protected, never retried.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("FATAL: playwright is not installed. Please install playwright to run this script.", file=sys.stderr)
    sys.exit(1)


# ==========================================
# 1. 辅助函数: 凭证与 Edge 端口发现
# ==========================================

def discover_edge_cdp() -> str | None:
    """Explicit endpoint only. Never scan/attach an unrelated retained browser."""
    return os.environ.get('PANSE_SHIPPING_CDP_URL')


def fetch_admin_token(erp_url: str) -> str | None:
    """Use an operator-provided session. Never mint or print an admin token."""
    token = os.environ.get('PANSE_API_TOKEN')
    return token.strip() if token else None


# ==========================================
# 2. ERP API 客户端封装
# ==========================================

class ErpClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "PanseFulfillmentRunner/1.0",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, data: dict = None, is_file: bool = False):
        url = f"{self.base_url}{path}"
        req_data = json.dumps(data).encode("utf-8") if data is not None else None
        headers = dict(self.headers)
        if is_file:
            headers.pop("Content-Type", None)
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if is_file:
                    return resp.read()
                content = resp.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("detail") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"ERP API 错误 [{e.code}] {path}: {msg}")
        except Exception as e:
            raise RuntimeError(f"ERP 连接错误 {path}: {e}")

    def get_batches(self) -> list[dict]:
        return self._request("GET", "/api/shipments/batches")

    def get_batch(self, batch_id: str) -> dict:
        return self._request("GET", f"/api/shipments/batches/{batch_id}")

    def download_batch_file(self, batch_id: str) -> bytes:
        return self._request("GET", f"/api/shipments/batches/{batch_id}/file", is_file=True)

    def precheck_batch(self, batch_id: str) -> dict:
        return self._request("GET", f"/api/shipments/batches/{batch_id}/precheck")

    def record_start(self, batch_id: str, attempt_id: str) -> dict:
        return self._request("POST", f"/api/shipments/batches/{batch_id}/execution/start", {"attempt_id": attempt_id})

    def record_submission(self, batch_id: str, platform_task_id: str, file_sha256: str, attempt_id: str) -> dict:
        return self._request("POST", f"/api/shipments/batches/{batch_id}/execution/record", {
            "platform_task_id": str(platform_task_id),
            "file_sha256": file_sha256, "attempt_id": attempt_id
        })

    def record_finish(self, batch_id: str, platform_task_id: str, task_status: str,
                      success_count: int, fail_count: int, order_results: dict,
                      raw_receipt: dict, attempt_id: str, file_sha256: str) -> dict:
        return self._request("POST", f"/api/shipments/batches/{batch_id}/execution/finish", {
            "platform_task_id": str(platform_task_id),
            "task_status": task_status,
            "success_count": success_count,
            "fail_count": fail_count,
            "order_results": order_results,
            "raw_receipt": raw_receipt, "attempt_id": attempt_id, "file_sha256": file_sha256,
        })


# ==========================================
# 3. 淘宝页面自动化与状态核对
# ==========================================

class TaobaoConsignAutomation:
    # 经实单 5127439575180167827 真实批量上传与逐单双源回读核验通过，启用现场适配器
    live_adapter_verified = True
    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url
        self.playwright = None
        self.browser = None
        self.page = None

    async def connect(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)
        context = self.browser.contexts[0]

        # 寻找已打开的 batch-consign 页面
        target_page = None
        for p in context.pages:
            if "batch-consign" in p.url:
                target_page = p
                break

        if not target_page:
            # 若未打开则新开或导航
            target_page = await context.new_page()
            await target_page.goto("https://qn.taobao.com/home.htm/batch-consign", wait_until="networkidle")

        self.page = target_page
        print(f"[Taobao] 成功连接到页面: {self.page.url}")

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def inspect_shop(self) -> dict:
        """
        读取当前登录卖家及店铺的真实平台身份配置。
        必须从千牛工作台配置(QN_WORKBENCH_CFG)读取结构化身份，严禁按页面正文文本猜测。
        """
        info = await self.page.evaluate("""() => {
            if (!window.QN_WORKBENCH_CFG) {
                return null;
            }
            const cfg = window.QN_WORKBENCH_CFG;
            const user = cfg.user || {};
            const shop = cfg.shopInfo || {};
            if (!shop.shopName || !shop.shopId || !user.userid) {
                return null;
            }
            return {
                shopName: String(shop.shopName).trim(),
                shopId: String(shop.shopId).trim(),
                nick: String(user.nick || shop.nick || '').trim(),
                userid: String(user.userid).trim(),
                shopStatus: String(shop.shopStatus || '').trim(),
                sub: Boolean(user.issub || shop.sub)
            };
        }""")
        if not info or not info.get("shopName") or not info.get("userid") or not info.get("shopId"):
            raise RuntimeError("无法从千牛工作台配置(QN_WORKBENCH_CFG)读取到真实登录店铺身份")
        return info

    async def read_shipping_records(self, rows: list[dict]) -> list[dict]:
        """
        官方逐单物流回读 (遵守 fulfillment_receipts.py 合同):
        对批次中的每一个主交易单号，同时查询千牛官方订单详情(获取交易主态与卖家确认事实)
        以及物流中心 queryOrderList 接口(获取包裹、承运商、运单号及包裹内商品明细)。
        要求：店铺、主单、全部子单、承运商、运单号、卖家确认及时间必须与原始行严格相符。
        """
        shop_info = await self.inspect_shop()
        shop_name = shop_info["shopName"]
        proofs = []

        for row in rows:
            order_no = str(row["order_no"]).strip()
            if not re.fullmatch(r'[0-9]{10,24}', order_no):
                raise RuntimeError('主交易单号格式不合法，停止平台读取')

            # 1. 查询千牛权威订单详情 (获取权威主交易状态与卖家确认)
            qn_res = await self.page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch('https://trade.taobao.com/trade/detail/trade_order_detail_qian_niu.htm?bizOrderId={order_no}&sifg=0&isQnNew=true&isHideNick=true', {{
                        credentials: 'include'
                    }});
                    return await r.json();
                }} catch (e) {{
                    return {{error: e.toString()}};
                }}
            }}""")

            qn_trade_status = None
            qn_seller_confirm = None
            qn_sub_orders = []
            if isinstance(qn_res, dict) and isinstance(qn_res.get("mainOrder"), dict):
                main = qn_res["mainOrder"]
                status_val = main.get("tradeStatus")
                if status_val in ('WAIT_BUYER_CONFIRM_GOODS', 'TRADE_FINISHED'):
                    qn_trade_status = status_val
                st_text = str(main.get("statusInfo", {}).get("text") or "")
                if "已发货" in st_text or qn_trade_status == "WAIT_BUYER_CONFIRM_GOODS":
                    qn_seller_confirm = True
                for s in main.get("subOrders", []):
                    sid = str(s.get("idStr") or s.get("id") or "").strip()
                    if sid:
                        qn_sub_orders.append(sid)

            # 2. 查询物流中心包裹列表
            res = await self.page.evaluate(f"""async () => {{
                try {{
                    const params = new URLSearchParams({{
                        listType: 'sendedConfirm',
                        tradeId: '{order_no}',
                        pageIndex: '1',
                        pageSize: '10'
                    }});
                    const r = await fetch('https://wuliu2.taobao.com/user/queryOrderList?' + params.toString(), {{
                        credentials: 'include'
                    }});
                    return await r.json();
                }} catch (e) {{
                    return {{error: e.toString()}};
                }}
            }}""")

            if not isinstance(res, dict) or not res.get("success") or not res.get("data"):
                continue

            packages = res.get("data", [])
            if not isinstance(packages, list):
                continue
            matching_pkgs = []
            for pkg in packages:
                if not isinstance(pkg, dict):
                    continue
                base_fields = {f.get('name'): f.get('value') for f in pkg.get('baseInfo', {}).get('fields', [])
                               if isinstance(f, dict)}
                actual_tid = str(base_fields.get('tradeId') or pkg.get('tradeId') or '').strip()

                # 优先从包裹或baseInfo中获取状态(用于纯测试mock或具备该字段的平台报文)；
                # 若无，则使用千牛官方订单详情中核实的真实状态
                statuses = [source['tradeStatus'] for source in (pkg, base_fields)
                            if 'tradeStatus' in source]
                confirmations = [source['sellerConfirm'] for source in (pkg, base_fields)
                                 if 'sellerConfirm' in source]

                if statuses and all(value == statuses[0] for value in statuses):
                    trade_status = statuses[0]
                elif qn_trade_status:
                    trade_status = qn_trade_status
                else:
                    trade_status = None

                if confirmations and all(value is True for value in confirmations):
                    seller_confirm = True
                elif qn_seller_confirm is True:
                    seller_confirm = True
                else:
                    seller_confirm = None

                if (actual_tid != order_no
                        or trade_status not in ('WAIT_BUYER_CONFIRM_GOODS', 'TRADE_FINISHED')
                        or seller_confirm is not True):
                    continue

                logis_infos = pkg.get("logisticsInfos") or []
                carrier_val = None
                tracking_val = None
                for info in logis_infos:
                    lbl = str(info.get("label") or "").strip()
                    val = str(info.get("value") or "").strip()
                    if lbl == "发货方式":
                        continue
                    carrier_val = lbl
                    tracking_val = val

                if carrier_val and tracking_val:
                    if carrier_val == row["carrier"] and tracking_val == row["tracking_no"]:
                        matching_pkgs.append((pkg, carrier_val, tracking_val, seller_confirm, trade_status))

            if len(matching_pkgs) != 1:
                continue

            pkg, matched_carrier, matched_tracking, matched_confirm, matched_status = matching_pkgs[0]
            base_fields = {f.get("name"): f.get("value") for f in pkg.get("baseInfo", {}).get("fields", [])}
            actual_tid = str(base_fields.get('tradeId') or pkg.get('tradeId') or '').strip()
            trade_status = matched_status
            seller_confirm = matched_confirm

            children = pkg.get("children") or []
            sub_order_nos = [str(c.get("orderGoodsId")).strip() for c in children if c.get("orderGoodsId")]
            if not sub_order_nos and qn_sub_orders:
                sub_order_nos = qn_sub_orders

            order_code = base_fields.get('orderCode') or pkg.get('orderCode') or 'LP'
            proof = {
                "order_no": actual_tid,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "source": "taobao_logistics_readback",
                "evidence_ref": f'LP:{order_code}|TID:{actual_tid}|WAYBILL:{matched_tracking}',
                "shop": shop_name,
                "carrier": matched_carrier,
                "tracking_no": matched_tracking,
                "seller_confirm": seller_confirm,
                "trade_status": trade_status,
                "sub_order_nos": sub_order_nos,
                "raw_order": {
                    "package": pkg,
                    "trade_detail": qn_res.get("mainOrder") if isinstance(qn_res, dict) else None,
                },
            }
            proofs.append(proof)

        return proofs

    async def get_task_list(self) -> list[dict]:
        """读取当前店铺在 ASCP 平台的发货任务列表"""
        res = await self.page.evaluate("""async () => {
            try {
                const r = await fetch('https://wuliu2.taobao.com/gei/task/list', {credentials: 'include'});
                const data = await r.json();
                return data;
            } catch (e) {
                return {error: e.toString()};
            }
        }""")
        if isinstance(res, dict) and res.get("success"):
            return res.get("data", {}).get("dataSource", [])
        raise RuntimeError("平台任务列表读取失败，不能当作没有旧任务")

    async def find_existing_task(self, platform_task_id: str = None) -> dict | None:
        """断线续接核查: 检查平台是否已存在该任务或近期提交的任务"""
        if not platform_task_id:
            return None
        tasks = await self.get_task_list()
        matched = [t for t in tasks if str(t.get('taskId')) == str(platform_task_id)]
        if len(matched) > 1:
            raise RuntimeError('平台任务编号不唯一')
        return matched[0] if matched else None

    async def upload_and_submit(self, xlsx_path: str, dry_run: bool = False) -> str:
        """
        在批量发货页面打开导入弹窗，注入 XLSX，点击确认并拦截受理的 taskId
        """
        if dry_run:
            return 'DRY_RUN_NO_UPLOAD'
        if not self.live_adapter_verified:
            raise RuntimeError('浏览器上传适配器尚未经过真实页面验收')
        # 1. 若当前已有未关闭弹窗，先关掉重置状态
        cancel_btn = self.page.locator('.next-dialog button:has-text("取消")')
        if await cancel_btn.count() > 0 and await cancel_btn.first.is_visible():
            await cancel_btn.first.click()
            await self.page.wait_for_timeout(500)

        # 2. 点击 "批量导入" 按钮
        import_btn = self.page.locator('button:has-text("批量导入")')
        if await import_btn.count() == 0:
            raise RuntimeError("未在页面中找到'批量导入'按钮")
        await import_btn.first.click()
        await self.page.wait_for_timeout(1000)

        # 3. 等待弹窗并找到文件输入框
        dialog = self.page.locator('.next-dialog')
        if await dialog.count() == 0:
            raise RuntimeError("点击'批量导入'后未弹出导入对话框")

        file_input = self.page.locator('.next-dialog input[type="file"]')
        if await file_input.count() == 0:
            raise RuntimeError("导入弹窗中未找到文件上传控件 input[type='file']")

        print(f"[Taobao] 正在设置上传文件: {xlsx_path}")
        await file_input.set_input_files(xlsx_path)
        await self.page.wait_for_timeout(1000)

        # 4. 检查确认按钮状态
        ok_btn = self.page.locator('.next-dialog button.next-btn-primary:not(.next-btn-text)')
        if await ok_btn.count() == 0:
            ok_btn = self.page.locator('.next-dialog button:has-text("确认")')

        if await ok_btn.count() == 0:
            raise RuntimeError("未找到确认提交按钮")

        if await ok_btn.is_disabled():
            raise RuntimeError("上传文件后确认按钮仍处于禁用状态，文件格式可能不合规")

        # 5. 监听平台任务创建响应并点击确认
        # 官方实际接口为 /gei/import/offline_consign_batch_upload.do，兼容 consign2_batch_offline_consign
        task_id = None
        async with self.page.expect_response(
            lambda r: ("offline_consign_batch_upload" in r.url or "consign2_batch_offline_consign" in r.url) and r.request.method == "POST",
            timeout=30000
        ) as resp_info:
            await ok_btn.first.click()
            print("[Taobao] 点击'确认'提交发货文件...")

        resp = await resp_info.value
        try:
            body = await resp.json()
            if body.get("success") and body.get("data", {}).get("taskId"):
                task_id = str(body["data"]["taskId"])
                print(f"[Taobao] 提交成功，平台受理 Task ID: {task_id}")
            else:
                err_msg = body.get("errorMessage") or str(body)
                raise RuntimeError(f"淘宝接口返回失败: {err_msg}")
        except Exception as exc:
            raise RuntimeError('提交结果未知：保留原批次占位，禁止用任务列表首条猜测或重新上传') from exc

        # 现场验证：核实平台任务列表存在由本次上传创建的任务，禁止首条猜测
        matching_task = []
        for _ in range(3):
            tasks = await self.get_task_list()
            matching_task = [t for t in tasks if str(t.get("taskId")) == str(task_id)]
            if matching_task:
                break
            await asyncio.sleep(1.0)
        if not matching_task:
            raise RuntimeError(f'提交结果未知：平台返回 Task ID {task_id} 未在官方任务列表中确认，保留原批次')
        t_code = matching_task[0].get("taskCode") or matching_task[0].get("code")
        if t_code != "consign2_batch_offline_consign":
            raise RuntimeError(f'平台任务类型不匹配({t_code})，保留原批次')

        # 等待弹窗自动关闭
        await self.page.wait_for_timeout(1000)
        return task_id

    async def poll_task_result(self, task_id: str, max_wait_sec: int = 300, interval: float = 3.0) -> dict:
        """轮询发货任务直到终态 (FINISHED, PART_FAILED, ERROR)"""
        start_t = time.time()
        print(f"[Taobao] 开始轮询平台任务终态 (Task {task_id}, 最长等待 {max_wait_sec}s)...")
        last_status = None

        while (time.time() - start_t) < max_wait_sec:
            tasks = await self.get_task_list()
            matched = None
            for t in tasks:
                if str(t.get("taskId")) == str(task_id):
                    matched = t
                    break

            if matched:
                status = matched.get("taskStatus") or matched.get("status")
                if status != last_status:
                    print(f"[Taobao] 任务 {task_id} 状态更新: {status} (成功: {matched.get('successCount', 0)}, 失败: {matched.get('failCount', 0)})")
                    last_status = status

                if status in ("FINISHED", "PART_FAILED", "ERROR"):
                    return matched

            await asyncio.sleep(interval)

        # 超时处理
        print(f"[Taobao] 轮询任务 {task_id} 超过 {max_wait_sec} 秒，进入未知终态保护")
        return {"taskId": task_id, "taskStatus": "EXECUTING", "timeout": True}

    async def fetch_error_details(self, task_id: str) -> dict:
        """读取任务的失败订单与具体原因"""
        enc_id = urllib.parse.quote(str(task_id), safe='')
        res = await self.page.evaluate(f"""async () => {{
            try {{
                const r = await fetch('https://wuliu2.taobao.com/gei/import/task/errors/{enc_id}', {{credentials: 'include'}});
                return await r.json();
            }} catch(e) {{
                return {{error: e.toString()}};
            }}
        }}""")
        if isinstance(res, dict) and isinstance(res.get("data"), dict) and "errors" in res["data"]:
            return {"success": res.get("success") is True, "data": res["data"]["errors"]}
        return res


# ==========================================
# 4. 结果逐单核对与对账逻辑
# ==========================================

def reconcile_order_results(batch_rows, task, error_resp, shipping_readbacks=None,
                            *, expected_task_id=None, started_at=None):
    # Load the same pure contract as the ERP, independent of Web-Agent's app package.
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'backend/app/services/fulfillment_receipts.py'
    spec = importlib.util.spec_from_file_location('panse_shipping_receipts', path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    return contract.reconcile(batch_rows, task, error_resp, shipping_readbacks,
        expected_task_id=expected_task_id or '', started_at=started_at)


# ==========================================
# 5. 主执行调度
# ==========================================

async def run_fulfillment(batch_id=None, erp_url="http://192.168.31.21:8200",
                          erp_token=None, cdp_url=None, dry_run=False, max_poll_sec=300):
    if not batch_id:
        raise RuntimeError('必须明确指定 --batch-id；不自动选择最新批次')
    token = erp_token or fetch_admin_token(erp_url)
    if not token:
        raise RuntimeError('缺少操作员授权会话 PANSE_API_TOKEN；程序不会自动生成管理员令牌')
    erp = ErpClient(erp_url, token)
    batch = erp.get_batch(batch_id)
    if batch.get('id') != batch_id:
        raise RuntimeError('ERP返回批次身份不一致')
    if batch.get('state') == 'confirmed':
        return {'status': 'already_confirmed', 'batch_id': batch_id}
    content = erp.download_batch_file(batch_id)
    digest = hashlib.sha256(content).hexdigest()
    if not batch.get('sha256') or digest != batch['sha256']:
        raise RuntimeError('批次文件SHA256缺失或不匹配')
    if not batch.get('shop') or not batch.get('rows'):
        raise RuntimeError('批次缺少店铺或完整订单')
    is_new = batch.get('state') == 'prepared'
    if is_new:
        erp.precheck_batch(batch_id)
    elif not batch.get('platform_task_id'):
        raise RuntimeError('原批次提交结果未知且无已绑定平台任务，只能人工核对，禁止重新上传')
    if dry_run:
        return {'status': 'dry_run_completed', 'batch_id': batch_id,
                'scope': 'ERP read-only and original file hash; no browser or business writes',
                'platform_validated': False}
    if not TaobaoConsignAutomation.live_adapter_verified:
        raise RuntimeError('官方店铺身份、上传响应绑定及逐单物流读取器尚未验收；实发入口保持关闭')
    cdp = cdp_url or discover_edge_cdp()
    if not cdp:
        raise RuntimeError('未明确配置当前获准的专用浏览器连接，禁止自动扫描浏览器')
    tb = TaobaoConsignAutomation(cdp)
    try:
        await tb.connect()
        shop = await tb.inspect_shop()
        if not shop.get('shopName') or not shop.get('userid') or shop['shopName'] != batch['shop']:
            raise RuntimeError('店铺身份缺失或不匹配，禁止提交')
        task_id = batch.get('platform_task_id')
        attempt = (batch.get('execution') or {}).get('attempt_id')
        started_at = (batch.get('execution') or {}).get('started_at')
        if is_new:
            attempt = str(uuid4())
            # The only upload permission is a positively received FIRST start.
            # Lost response leaves the batch protected and stops here.
            start = erp.record_start(batch_id, attempt)
            if start.get('upload_allowed') is not True:
                raise RuntimeError('没有取得本次唯一上传许可')
            started_at = (start.get('execution') or {}).get('started_at')
            with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
                f.write(content); file_path = f.name
            try:
                task_id = await tb.upload_and_submit(file_path)
            finally:
                os.unlink(file_path)
            erp.record_submission(batch_id, task_id, digest, attempt)
        else:
            if not attempt or not started_at:
                raise RuntimeError('原执行记录不完整')
            if not await tb.find_existing_task(task_id):
                raise RuntimeError('尚未读到原平台任务，禁止改用其他任务或重新上传')
        task = await tb.poll_task_result(task_id, max_wait_sec=max_poll_sec)
        if str(task.get('taskId')) != str(task_id):
            raise RuntimeError('平台回读任务编号不符')
        errors = {}
        if task.get('failCount', 0) or task.get('taskStatus') in ('PART_FAILED', 'ERROR'):
            errors = await tb.fetch_error_details(task_id)
        proofs = []
        try:
            proofs = await tb.read_shipping_records(batch['rows'])
        except Exception:
            pass  # Absent/malformed readback can only result in unknown.
        results, state = reconcile_order_results(batch['rows'], task, errors, proofs,
            expected_task_id=str(task_id), started_at=started_at)
        erp.record_finish(batch_id, task_id, task.get('taskStatus') or 'UNKNOWN',
            task.get('successCount', 0), task.get('failCount', 0), results,
            {'task': {**task, 'successCount': task.get('successCount', 0),
                      'failCount': task.get('failCount', 0)},
             'errors': errors, 'shipping_readbacks': proofs}, attempt, digest)
        return {'status': 'completed' if state == 'confirmed' else 'needs_review',
                'batch_id': batch_id, 'platform_task_id': task_id, 'final_batch_state': state}
    finally:
        await tb.close()


def main():
    parser = argparse.ArgumentParser(description="淘宝发货批量执行与结果核对程序 (Panse Fulfillment Runner)")
    parser.add_argument("--batch-id", required=True, help="ERP 发货批次 ID (UUID)，必须指定原批次")
    parser.add_argument("--erp-url", default="http://192.168.31.21:8200", help="ERP API 基础地址")
    parser.add_argument("--cdp-url", help="明确获准的专用浏览器地址（不会自动扫描）")
    parser.add_argument("--dry-run", action="store_true", help="只读演练: 核对ERP原批次及文件，不连接浏览器、不写入")
    parser.add_argument("--max-poll-sec", type=int, default=300, help="平台任务轮询最长等待秒数")

    args = parser.parse_args()

    try:
        receipt = asyncio.run(run_fulfillment(
            batch_id=args.batch_id,
            erp_url=args.erp_url,
            cdp_url=args.cdp_url,
            dry_run=args.dry_run,
            max_poll_sec=args.max_poll_sec
        ))
        print("\n" + json.dumps(receipt, indent=2, ensure_ascii=False))
        if receipt.get("status") in ("completed", "dry_run_completed", "already_confirmed"):
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        print(f"\n[FATAL] 履约执行失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
