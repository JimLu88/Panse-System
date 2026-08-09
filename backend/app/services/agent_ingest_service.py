# -*- coding: utf-8 -*-
"""Web-Agent 下载产物 → ERP 自动导入 (2026-06-12 用户拍板"全部一次性打通")。

职责:
- run_ingest: 扫共享 output 目录, 按子目录分类 → 调既有导入器入库;
  防重靠 imported_files.file_hash (同文件只导一次); 加密发货报表标「待口令」;
  余额截图归档标「截图待读数」(待企业号 API 上线/OCR 确认队列)。
- orchestrate: 按更新间隔触发 Web-Agent 任务(串行, 等 job 完成) → 收尾 run_ingest
  → 飞书汇总。登录态缺失/超时的任务标「待人工」, 绝不无限重试 (交接方案 §7.6)。

更新间隔 (用户拍板): 订单默认 1 天；手动编排的余额/流水默认 3 天、settings 可改。
每日 20:30 财务班次使用 force_finance=True，仍会强制刷新当天余额和流水。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.marketing import PromotionFlow
from app.services import import_storage, settings_service, web_agent_service, yulibao_service

_log = logging.getLogger("panse.agent_ingest")

OUTPUT_DIR = Path(os.environ.get("AGENT_OUTPUT_DIR", "/app/agent_output"))

# settings 键
KEY_INTERVAL_ORDERS = "web_agent_interval_orders"      # 天, 默认 1
KEY_INTERVAL_BALANCE = "web_agent_interval_balance"    # 天, 默认 3 (余额+流水)
KEY_STATE = "web_agent_state"                          # 各类别最近成功时间 JSON
KEY_LAST_INGEST = "web_agent_last_ingest"              # 最近一次扫描报告 JSON
KEY_ORCH_STATE = "web_agent_orch_state"                # 编排进行中状态 JSON
KEY_ORDER_QUOTA_RESULT = "taobao_order_quota_last_result"  # 最近订单导出前提额核验（不含敏感内容）
KEY_SCAN_RESULTS = "web_agent_scan_last_results"        # 扫码续跑逐任务结果 JSON（不含凭证）
KEY_SHIPPING_PASSWORD_RESULT = "taobao_shipping_pwd_last_result"  # 最近口令匹配结果（不含口令）
STATE_MAIN_ALIPAY_FLOW = "alipay_main_flow"
MAIN_ALIPAY_FLOW_TASK = "alipay_main"
STATE_FINANCE_TASK_SUCCESS = "finance_task_success"
STATE_ENTERPRISE_ALIPAY_FLOW = "alipay_enterprise_flow"
FINANCE_BROWSER_FLOW_TASKS = (
    "wechat_bill",
    "wanxiangtai",
    "wanshifu",
    MAIN_ALIPAY_FLOW_TASK,
)
FINANCE_EXPORT_TASKS = {"wechat_bill", "wanxiangtai", "wanshifu"}
ORDER_PULL_EXPECTED_ARTIFACT_COUNT = 3
ORDER_PULL_REQUIRED_ROLES = frozenset({"orders", "sales_detail", "shipping"})

_OOXML_ENCRYPTED_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")

# 编排任务分组 (Web-Agent 任务 id, 见 Panse-Web-Agent app/tasks/definitions.py)
ORDERS_TASKS = ["taobao_orders"]
BALANCE_FLOW_TASKS = [
    "wechat_bill", "wanxiangtai", "wanshifu",
    "bal_taobao_aggregate", "bal_ads", "bal_wanshifu",
    # 主力号(8812 个人号)余额: 进自动编排 (用户拍板 2026-06-20「以后都要自动」)。
    #   下方 orchestrate 的 has_session 闸门保证: 登录态有效才跑(直接截图, 不弹码);
    #   登录态失效则跳过 + 记入待扫清单 + 飞书提示(绝不无人时弹码挂起) → 真·自动又不误弹。
    "bal_alipay_main",
    # 企业号(9a) 余额走官方 API, 每次编排已用 refresh_alipay_balances 精确刷, 永不浏览器扫码。
]
# 暂不编排的外部账单；主力号支付宝流水已走每日任务。
SKIPPED_TASKS = {
    "alipay_9a": "支付宝企业号流水: 等官方 API 审核上线后走 API, 不走浏览器",
    "logistics_bill": "物流账单: 待提供承运商入口",
}

_orch_lock = threading.Lock()
_scan_lock = threading.Lock()

KEY_PENDING_SCAN = "web_agent_pending_scan"   # 待扫码任务 id 列表 (JSON)

# Web-Agent 支付宝账号名 → ERP 账户余额表 account_name (企业号走 API 精确取数)
ALIPAY_ACCT_MAP = {"企业号": "支付宝-企业账号"}


def get_pending_scans(db: Session) -> list:
    try:
        lst = json.loads(settings_service.get(db, KEY_PENDING_SCAN) or "[]")
        return lst if isinstance(lst, list) else []
    except json.JSONDecodeError:
        return []


def _add_pending_scan(db: Session, task_id: str) -> None:
    lst = get_pending_scans(db)
    if task_id not in lst:
        lst.append(task_id)
        settings_service.set_value(db, KEY_PENDING_SCAN, json.dumps(lst),
                                   description="自动取数: 待用户扫码的任务")
        db.commit()


def get_scan_results(db: Session) -> dict:
    try:
        value = json.loads(
            settings_service.get(db, KEY_SCAN_RESULTS, env_fallback=False) or "{}"
        )
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_shipping_password_result(db: Session) -> dict:
    """返回最近口令匹配结果；只含状态、文件名和错误原因，不含口令。"""
    try:
        value = json.loads(
            settings_service.get(
                db, KEY_SHIPPING_PASSWORD_RESULT, env_fallback=False,
            ) or "{}"
        )
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_order_quota_result(db: Session) -> dict:
    """最近一次淘宝订单导出前的额度核验结果（仅状态/时间，不含页面或凭证）。"""
    try:
        value = json.loads(
            settings_service.get(
                db, KEY_ORDER_QUOTA_RESULT, env_fallback=False,
            ) or "{}"
        )
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


# 默认可扫码刷新的余额任务 (淘宝聚合一扫覆盖淘宝SSO的推广/万师傅)。
# 企业号余额走 API 不扫码; 主力号(8812 个人号)余额 bal_alipay_main 加入扫码流(用户拍板 2026-06-20):
# 该任务一直在 Web-Agent definitions.py(整页截图+OCR), 但 2026-06-12 改动把它移出触发清单后成了孤儿、
# 永不执行 → 主力号余额停在5月。回"扫码"后依次扫, 主力号QR在淘宝之后单独推(不混); 登录态有效则免扫直接截图。
# 余额仍只在用户回复“扫码”后执行；主力号流水则另按自然日自动编排。
DEFAULT_SCAN_TASKS = ["bal_taobao_aggregate", "bal_ads", "bal_wanshifu", "bal_alipay_main"]


def _task_run_variables(task_id: str, db: Session | None = None, *, on: date | None = None,
                        wait_scan: bool = False) -> dict:
    variables = {"wait_scan": True} if wait_scan else {}
    if task_id == MAIN_ALIPAY_FLOW_TASK:
        from sqlalchemy import func

        from app.models.finance import AlipayFlow

        target = on or date.today()
        start = target - timedelta(days=30)
        if db is not None:
            last = db.execute(
                select(func.max(AlipayFlow.transaction_time)).where(
                    AlipayFlow.account == "主力号"
                )
            ).scalar()
            if last:
                # 含最后一天做一日重叠，承接晚入账/状态变化；最终由流水唯一键增量去重。
                start = max(start, last.date())
        variables.update({
            "date_from": start.isoformat(),
            "date_to": target.isoformat(),
            "wait_scan": bool(wait_scan),
            "account_label": "支付宝主力账号",
        })
    return variables


def _main_alipay_artifacts() -> dict[str, tuple[int, int]]:
    """主力号流水下载产物快照；用于拦截 Web-Agent 的“空成功”。"""
    if not OUTPUT_DIR.exists():
        return {}
    out: dict[str, tuple[int, int]] = {}
    for path in OUTPUT_DIR.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(OUTPUT_DIR).parts
        if (any("alipay" in part.lower() for part in parts)
                and any("主力" in part for part in parts)):
            stat = path.stat()
            out[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return out


def _job_downloads(job_result: dict) -> list[str]:
    """Normalize Web-Agent artifacts across direct and tiered task results."""
    candidates: list = list(job_result.get("downloads") or [])
    nested = job_result.get("result")
    if isinstance(nested, dict):
        candidates.extend(nested.get("downloads") or [])
        candidates.extend(nested.get("_downloads") or [])
    return list(dict.fromkeys(str(item) for item in candidates if item))


def new_order_batch_id(*, on: date | None = None) -> str:
    """Create one immutable business batch id for an order pull/recovery chain."""
    target = on or date.today()
    return f"orders-{target:%Y%m%d}-{uuid4().hex}"


def _normalize_order_report_role(value: object) -> str | None:
    text = str(value or "").strip().lower()
    aliases = {
        "orders": "orders",
        "order": "orders",
        "订单报表": "orders",
        "item_sales": "sales_detail",
        "items": "sales_detail",
        "sales_detail": "sales_detail",
        "宝贝销售明细报表": "sales_detail",
        "销售明细": "sales_detail",
        "shipping": "shipping",
        "发货报表": "shipping",
    }
    return aliases.get(text)


def _role_from_artifact_name(filename: str) -> str | None:
    """Compatibility fallback for old durable evidence and test fixtures."""
    name = Path(str(filename).replace("\\", "/")).name.lower()
    if "shipping" in name or name.startswith("exportorderlist"):
        return "shipping"
    if any(token in name for token in ("item_sales", "items", "sales_detail")):
        return "sales_detail"
    if "orders" in name or "order_master" in name:
        return "orders"
    return None


def _job_artifact_roles(job_result: dict) -> dict[str, str]:
    """Map Web-Agent report results to immutable artifact basenames."""
    payloads = [job_result]
    nested = job_result.get("result")
    if isinstance(nested, dict):
        payloads.append(nested)
    roles: dict[str, str] = {}
    for payload in payloads:
        for report in payload.get("reports") or []:
            if not isinstance(report, dict):
                continue
            role = _normalize_order_report_role(report.get("report"))
            if not role:
                continue
            for raw_path in report.get("downloads") or []:
                name = Path(str(raw_path).replace("\\", "/")).name
                if name:
                    roles[name] = role
    return roles


def start_pending_scans(db: Session) -> dict:
    """用户在飞书回复『扫码』后调用: 后台依次跑待扫任务 (wait_scan=True) —
    发大二维码到飞书、保持浏览器开等扫 ≤10分钟; 扫成功的从待扫清单移除并扫描导入。
    没有记录的待扫任务时, 默认跑全部余额截图任务 (主动刷新登录态)。"""
    tasks = get_pending_scans(db) or list(DEFAULT_SCAN_TASKS)
    # 企业号支付宝永远只走官方 API, 滤掉任何残留的企业号扫码任务, 杜绝误发二维码 (2026-06-12)。
    tasks = [t for t in tasks if "alipay_9a" not in t]
    if not tasks:
        return {"started": False, "reason": "无待扫码任务 (支付宝企业号走 API, 不扫码)"}
    if not _scan_lock.acquire(blocking=False):
        return {"started": False, "reason": "已有扫码流程在进行中"}

    def _run() -> None:
        from app.database import SessionLocal
        d = SessionLocal()
        remain = list(tasks)
        try:
            done = []
            done_artifacts: dict[str, list[str]] = {}
            done_artifact_roles: dict[str, dict[str, str]] = {}
            failures: list[dict] = []
            scan_results = get_scan_results(d)
            order_batch = None
            if "taobao_orders" in tasks:
                order_batch = (
                    latest_order_pull_batch_id(d)
                    if hasattr(d, "execute") else None
                ) or new_order_batch_id(on=date.today())
            for tid in list(tasks):
                artifacts_before = (
                    _main_alipay_artifacts()
                    if tid == MAIN_ALIPAY_FLOW_TASK else None
                )
                r = web_agent_service.run_task(
                    d, tid, _task_run_variables(tid, d, wait_scan=True))
                failure_reason = ""
                if not r.get("job"):
                    failure_reason = str(
                        r.get("error") or r.get("reason") or "Web-Agent 未返回任务编号"
                    )
                else:
                    final = web_agent_service.wait_job(d, r["job"], timeout_s=720, poll_s=8)
                    status = (final.get("status") or "").lower()
                    job_result = final.get("result") or {}
                    result_ok = job_result.get("ok") is not False
                    downloads = _job_downloads(job_result)
                    no_data = bool(job_result.get("no_data"))
                    if tid == MAIN_ALIPAY_FLOW_TASK:
                        has_artifact = _main_alipay_artifacts() != artifacts_before
                    elif tid == "taobao_orders" or tid in FINANCE_EXPORT_TASKS:
                        has_artifact = bool(downloads) or no_data
                    else:
                        has_artifact = True
                    if (status in ("done", "ok", "success")
                            and result_ok and has_artifact):
                        done.append(tid)
                        done_artifacts[tid] = downloads
                        done_artifact_roles[tid] = _job_artifact_roles(job_result)
                        scan_results[tid] = {
                            "status": "success",
                            "at": datetime.now().isoformat(timespec="seconds"),
                            "reason": None,
                        }
                    elif status in ("done", "ok", "success") and not result_ok:
                        failure_reason = str(
                            job_result.get("errors")
                            or job_result.get("reason")
                            or "Web-Agent 返回业务失败"
                        )
                        _log.warning(
                            "扫码续跑任务业务失败 %s: %s",
                            tid,
                            failure_reason,
                        )
                    elif status in ("done", "ok", "success"):
                        failure_reason = "扫码/登录步骤结束，但没有生成新的主力号流水文件"
                        _log.warning("扫码续跑任务空成功 %s：%s", tid, failure_reason)
                    else:
                        failure_reason = str(
                            final.get("error")
                            or final.get("note")
                            or job_result.get("errors")
                            or job_result.get("reason")
                            or f"Web-Agent 任务状态 {status or 'unknown'}"
                        )
                if failure_reason:
                    failure = {"task": tid, "reason": failure_reason[:300]}
                    failures.append(failure)
                    scan_results[tid] = {
                        "status": "failed",
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "reason": failure["reason"],
                    }
            ingest_result = run_ingest(d)   # 扫到的余额截图/淘宝报表一并导入
            order_ingest_result = None
            if "taobao_orders" in done:
                order_ingest_result = run_ingest(
                    d,
                    only_paths=done_artifacts.get("taobao_orders") or [],
                    artifact_roles=done_artifact_roles.get("taobao_orders") or {},
                    order_batch_id=order_batch,
                )
            if (MAIN_ALIPAY_FLOW_TASK in done
                    and (ingest_result.get("errors") or ingest_result.get("pending"))):
                # A browser download is not business success until the new
                # artifact can be parsed and stored.  Keep the exact import
                # failure instead of closing the retry chain on a false green.
                reason = (
                    "主力号流水文件已下载，但未完成入库："
                    f"失败 {int(ingest_result.get('errors') or 0)} 份，"
                    f"待处理 {int(ingest_result.get('pending') or 0)} 份"
                )
                done.remove(MAIN_ALIPAY_FLOW_TASK)
                failures.append({"task": MAIN_ALIPAY_FLOW_TASK, "reason": reason})
                scan_results[MAIN_ALIPAY_FLOW_TASK] = {
                    "status": "failed",
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "reason": reason,
                }
            batch_artifacts = [
                Path(str(path).replace("\\", "/")).name
                for path in (done_artifacts.get("taobao_orders") or [])
            ]
            batch_artifacts = list(dict.fromkeys(
                name for name in batch_artifacts if name
            ))
            batch_roles = done_artifact_roles.get("taobao_orders") or {}
            batch_artifact_states: dict[str, str] = {}
            batch_role_check = {
                "ok": False,
                "roles": {},
                "missing_roles": sorted(ORDER_PULL_REQUIRED_ROLES),
                "duplicate_roles": {},
                "unknown_artifacts": [],
            }
            if "taobao_orders" in done:
                batch_artifact_states = taobao_artifact_states(
                    d, batch_artifacts, order_batch_id=order_batch,
                )
                batch_role_check = validate_order_pull_artifact_roles(
                    d,
                    batch_artifacts,
                    declared_roles=batch_roles,
                    order_batch_id=order_batch,
                )
            if ("taobao_orders" in done
                    and len(batch_artifacts) != ORDER_PULL_EXPECTED_ARTIFACT_COUNT):
                reason = (
                    "订单拉取产物不完整："
                    f"预期 {ORDER_PULL_EXPECTED_ARTIFACT_COUNT} 份，"
                    f"实际 {len(batch_artifacts)} 份"
                )
                done.remove("taobao_orders")
                failures.append({"task": "taobao_orders", "reason": reason})
                scan_results["taobao_orders"] = {
                    "status": "failed",
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "reason": reason,
                }
            elif "taobao_orders" in done and (order_ingest_result or {}).get("errors"):
                reason = (
                    "订单拉取产物角色或内容校验失败："
                    + "; ".join(
                        str((item.get("summary") or {}).get("error") or item.get("path") or "未知")
                        for item in (order_ingest_result.get("files") or [])
                        if item.get("status") == "error"
                    )
                )
                done.remove("taobao_orders")
                failures.append({"task": "taobao_orders", "reason": reason})
                scan_results["taobao_orders"] = {
                    "status": "failed",
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "reason": reason,
                }
            elif "taobao_orders" in done and not batch_role_check["ok"]:
                reason = "订单三报表角色不完整或重复：" + json.dumps(
                    batch_role_check, ensure_ascii=False,
                )
                done.remove("taobao_orders")
                failures.append({"task": "taobao_orders", "reason": reason})
                scan_results["taobao_orders"] = {
                    "status": "failed",
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "reason": reason,
                }
            elif "taobao_orders" in done:
                broken = {
                    name: status for name, status in batch_artifact_states.items()
                    if status not in {"imported", "pending_password"}
                }
                if broken:
                    reason = "订单拉取产物未完成入库：" + ", ".join(
                        f"{name}={status}" for name, status in broken.items()
                    )
                    done.remove("taobao_orders")
                    failures.append({"task": "taobao_orders", "reason": reason})
                    scan_results["taobao_orders"] = {
                        "status": "failed",
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "reason": reason,
                    }
            pending_before = get_pending_scans(d)
            pending_base = pending_before or list(tasks)
            remain = [t for t in pending_base if t not in done]
            settings_service.set_value(d, KEY_PENDING_SCAN, json.dumps(remain))
            settings_service.set_value(
                d,
                KEY_SCAN_RESULTS,
                json.dumps(scan_results, ensure_ascii=False),
                description="自动取数: 最近一次扫码续跑逐任务结果（不含凭证）",
            )
            d.commit()
            if done:
                # 扫码/重新登录续跑也必须补齐逐来源成功凭证；否则下一轮财务
                # 重试看不到本次成功，会再次拉起刚恢复的登录流程。
                state = _load_json(d, KEY_STATE)
                finance_markers = state.get(STATE_FINANCE_TASK_SUCCESS)
                if not isinstance(finance_markers, dict):
                    finance_markers = {}
                error_artifacts = {
                    Path(str(file_info.get("path") or "")).name
                    for file_info in (ingest_result.get("files") or [])
                    if file_info.get("status") == "error"
                }
                marked_at = datetime.now().isoformat(timespec="seconds")
                for task_id in done:
                    if task_id not in FINANCE_BROWSER_FLOW_TASKS:
                        continue
                    artifacts = done_artifacts.get(task_id) or []
                    if task_id in FINANCE_EXPORT_TASKS and not artifacts:
                        continue
                    if any(Path(path).name in error_artifacts for path in artifacts):
                        continue
                    finance_markers[task_id] = marked_at
                state[STATE_FINANCE_TASK_SUCCESS] = finance_markers
                _save_json(d, KEY_STATE, state)
                d.commit()
            if ("taobao_orders" in done
                    and len(batch_artifacts) == ORDER_PULL_EXPECTED_ARTIFACT_COUNT
                    and batch_role_check["ok"]
                    and all(
                        status == "imported"
                        for status in batch_artifact_states.values()
                    )
                    and not pending_shipping_password_files(
                        d,
                        on=date.today(),
                        artifact_names=batch_artifacts,
                    )):
                # A user-triggered scan is the continuation of the failed daily
                # pull.  Mark the complete three-report refresh only after the
                # Web-Agent returned ok=True and ingest has no unresolved file.
                state = _load_json(d, KEY_STATE)
                completed_at = datetime.now().isoformat(timespec="seconds")
                state["taobao_report"] = completed_at
                state["taobao_orders_complete"] = completed_at
                state["taobao_orders_complete_artifacts"] = batch_artifacts
                state["taobao_orders_complete_artifact_roles"] = batch_role_check["roles"]
                state["taobao_orders_complete_batch_id"] = order_batch
                state["taobao_orders_complete_business_date"] = date.today().isoformat()
                state["taobao_orders_complete_legacy_evidence"] = False
                _save_json(d, KEY_STATE, state)
                d.commit()
                # Finish the original business outcome immediately.  Pushed
                # markers make this idempotent when the scheduled retry runs.
                from app.services import order_delivery_completion_service

                order_delivery_completion_service.complete_recovered_order_delivery(
                    d,
                    source="manual_scan",
                    manifest=batch_artifacts,
                    order_batch_id=order_batch,
                    order_business_date=date.today().isoformat(),
                )
            if MAIN_ALIPAY_FLOW_TASK in done:
                state = _load_json(d, KEY_STATE)
                completed_at = datetime.now().isoformat(timespec="seconds")
                state[STATE_MAIN_ALIPAY_FLOW] = completed_at
                finance_markers = state.get(STATE_FINANCE_TASK_SUCCESS)
                if not isinstance(finance_markers, dict):
                    finance_markers = {}
                finance_markers[MAIN_ALIPAY_FLOW_TASK] = completed_at
                state[STATE_FINANCE_TASK_SUCCESS] = finance_markers
                _save_json(d, KEY_STATE, state)
                d.commit()
                # 扫码是原财务失败链的人工续跑。成功证据落地后当场销账，
                # 后续定时器便不会继续拿旧 failures 弹重试通知。
                try:
                    from app.services import automation_pipeline_service

                    automation_pipeline_service.record_success(
                        d,
                        "flow_pull",
                        success_detail="扫码后主力号流水已下载并完成入库",
                    )
                    d.commit()
                except Exception:  # noqa: BLE001
                    d.rollback()
                    _log.exception("扫码成功后关闭主力号流水重试链失败")
            if done:
                # 余额扫码也要用已落库的逐账户日期关闭 waiting_input；否则虽然
                # 截图已成功，定时器仍会保留旧的“等待扫码”状态。这里只采信
                # AccountBalance/流水成功标记，不凭浏览器“done”猜测业务成功。
                try:
                    from app.services import scheduler

                    scheduler._reconcile_finance_success_from_persisted_evidence(d)
                except Exception:  # noqa: BLE001
                    d.rollback()
                    _log.exception("扫码成功后按落库证据关闭财务重试链失败")
            if failures:
                # 用户主动扫码后必须收到本次真实结果，不能只让旧定时器继续报
                # “需扫码”。失败任务仍留在待扫清单，方便处理后准确续跑。
                try:
                    from app.services import feishu_client

                    chat_id = settings_service.get(
                        d, "feishu_push_chat_id", env_fallback=False,
                    )
                    if chat_id:
                        labels = {
                            MAIN_ALIPAY_FLOW_TASK: "支付宝主力号流水",
                            "bal_alipay_main": "支付宝主力号余额",
                            "bal_taobao_aggregate": "淘宝聚合账户余额",
                            "bal_ads": "推广账户余额",
                            "bal_wanshifu": "万师傅余额",
                            "taobao_orders": "淘宝订单报表",
                        }
                        detail = "\n".join(
                            f"- {labels.get(item['task'], item['task'])}：{item['reason']}"
                            for item in failures
                        )
                        feishu_client.send_text(
                            d,
                            chat_id,
                            "⚠️ 扫码续跑未完成\n"
                            + detail
                            + "\n失败任务已保留；请按上述原因处理后再回复『扫码』。",
                        )
                except Exception:  # noqa: BLE001
                    _log.exception("发送扫码续跑失败原因到飞书失败")
        except Exception:  # noqa: BLE001
            _log.exception("扫码流程线程异常")
            d.rollback()
        finally:
            # 扫码续跑线程返回时已经没有仍在等待浏览器的 job。即使本轮失败，
            # 也只保留“待扫码”业务状态，不保留重型 Agent；用户再次回复“扫码”时
            # run_task 会通过轻量唤醒桥重新按需拉起。
            try:
                web_agent_service.request_stop(
                    d, reason="scan_continuation_finished"
                )
            except Exception:  # noqa: BLE001
                _log.warning("扫码续跑结束后请求关闭Web-Agent失败", exc_info=True)
            d.close()
            _scan_lock.release()

    threading.Thread(target=_run, name="web-agent-scan", daemon=True).start()
    return {"started": True, "tasks": tasks}


# ----------------------------- 工具 ----------------------------- #

def _get_int(db: Session, key: str, default: int) -> int:
    try:
        return int(settings_service.get(db, key) or default)
    except (TypeError, ValueError):
        return default


def _load_json(db: Session, key: str) -> dict:
    try:
        return json.loads(settings_service.get(db, key) or "{}")
    except json.JSONDecodeError:
        return {}


def _save_json(db: Session, key: str, data: dict) -> None:
    from app.json_utils import to_jsonable

    settings_service.set_value(
        db, key, json.dumps(to_jsonable(data), ensure_ascii=False)
    )


def _hash_exists(db: Session, file_hash: str) -> Optional[ImportedFile]:
    return db.execute(
        select(ImportedFile).where(ImportedFile.file_hash == file_hash)
        .order_by(ImportedFile.id.desc())
    ).scalars().first()


def _latest_shipping_password(db: Session) -> Optional[str]:
    """返回最近收到的发货报表口令。

    用户于 2026-07-27 确认：口令本身没有时间限制。时间戳只用于审计，不得按 60 分钟、
    24 小时或其他年龄拒绝口令。若口令与某份报表不匹配，解密会自然失败并保留待口令状态。
    """
    return settings_service.get(db, "taobao_shipping_pwd_latest", env_fallback=False) or None


def _report_to_dict(rep) -> dict:
    """导入器报告 (dataclass/dict) → 可 JSON 的摘要 (只留标量与短列表)。"""
    src = rep if isinstance(rep, dict) else dict(vars(rep))
    out = {}
    for k, v in src.items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        elif k == "daily_reconciliation" and isinstance(v, list):
            out[k] = v[:31]
        elif isinstance(v, list):
            out[k] = [str(x)[:120] for x in v[:5]]
    return out


def summarize_order_changes(ingest: dict | None) -> dict:
    """Summarize actual order mutations from the reports imported in this run.

    ``updated`` is deliberately excluded: the Taobao import is an upsert and
    counts unchanged rows as updated.  Only inserted rows and real field
    changes determine whether this pull found new work.
    """
    result = {"inserted": 0, "status_changed": 0, "amount_changed": 0}
    for item in (ingest or {}).get("files") or []:
        if item.get("category") != "taobao_report" or item.get("status") != "imported":
            continue
        summary = item.get("summary") or {}
        for key in result:
            result[key] += int(summary.get(key) or 0)
    return result


def summarize_order_changes_since_last_complete(db: Session, ingest: dict | None = None) -> dict:
    """Include changes imported by an earlier partial attempt of today's pull.

    A three-report pull can import one valid report and then fail on a later
    report.  The retry will skip the already archived file by hash, so looking
    only at the retry's ``run_ingest`` result would incorrectly say there were
    no changes.  Archive summaries since the last completed pull are the
    durable source of truth.
    """
    from app.models.import_file import ImportedFile

    state = _load_json(db, KEY_STATE)
    marker = state.get("taobao_orders_complete")
    try:
        since = datetime.fromisoformat(str(marker)) if marker else datetime.combine(date.today(), datetime.min.time())
    except (TypeError, ValueError):
        since = datetime.combine(date.today(), datetime.min.time())
    rows = db.execute(
        select(ImportedFile.row_summary).where(
            ImportedFile.kind == "taobao",
            ImportedFile.created_at > since,
        )
    ).scalars().all()
    if not rows:
        return summarize_order_changes(ingest)
    result = {"inserted": 0, "status_changed": 0, "amount_changed": 0}
    for summary in rows:
        if not isinstance(summary, dict) or summary.get("agent_status") != "imported":
            continue
        for key in result:
            result[key] += int(summary.get(key) or 0)
    return result


def format_order_change_message(changes: dict | None) -> str:
    changes = changes or {}
    inserted = int(changes.get("inserted") or 0)
    status_changed = int(changes.get("status_changed") or 0)
    amount_changed = int(changes.get("amount_changed") or 0)
    if inserted == 0 and status_changed == 0 and amount_changed == 0:
        return "没有新增订单"
    parts = [
        f"新增订单 {inserted} 单" if inserted else "没有新增订单",
    ]
    if status_changed:
        parts.append(f"已有订单状态变化 {status_changed} 单")
    if amount_changed:
        parts.append(f"已有订单金额变化 {amount_changed} 单")
    return "；".join(parts)


def latest_order_pull_result(db: Session, *, on: date | None = None) -> dict:
    """Return today's last verified order-pull result, never a stale prior day."""
    target = (on or date.today()).isoformat()
    saved = _load_json(db, KEY_STATE).get("taobao_orders_last_result")
    if not isinstance(saved, dict) or saved.get("date") != target:
        return {}
    changes = {
        key: int(saved.get(key) or 0)
        for key in ("inserted", "status_changed", "amount_changed")
    }
    return {
        "date": target,
        "changes": changes,
        "message": saved.get("message") or format_order_change_message(changes),
    }


# ----------------------------- 分类导入 ----------------------------- #

def _classify(rel: Path) -> str:
    """相对路径 → 类别。子目录约定见 Web-Agent definitions.py output_subdir。"""
    parts = [p.lower() for p in rel.parts]
    if "balance" in parts:
        return "balance"
    if "taobao" in parts:
        return "taobao_report"
    if "聚合账单" in rel.parts:
        return "settlement"
    if "wanxiangtai" in parts or "ads" in parts:
        return "promotion"
    if "wanshifu" in parts:
        return "wanshifu"
    if any("alipay" in p for p in parts):
        return "alipay"
    return "other"


def _is_main_alipay_path(path: Path) -> bool:
    """Web-Agent 的主力号产物固定放在 alipay/主力 下。"""
    return any("主力" in part for part in path.parts)


def _import_wanxiangtai_csv(db: Session, raw: bytes) -> dict:
    """万相台无界 CSV → PromotionFlow。
    列: 记账时间,交易日期,收支类型,交易类型,操作金额(元),操作后余额(元),备注
    sync_key = wxt:<记账时间>:<金额> 幂等; 收支类型 收入→充值 / 支出→支出。
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"inserted": 0, "error": "空文件"}
    header = [h.strip() for h in lines[0].split(",")]

    def col(name_part: str) -> Optional[int]:
        for i, h in enumerate(header):
            if name_part in h:
                return i
        return None

    i_time, i_date = col("记账时间"), col("交易日期")
    i_io, i_kind = col("收支类型"), col("交易类型")
    i_amt, i_remark = col("操作金额"), col("备注")
    if i_time is None or i_amt is None:
        return {"inserted": 0, "error": f"表头不识别: {header}"}
    inserted = skipped = 0
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) <= max(i_time, i_amt) or not cells[i_amt]:
            continue
        sync_key = f"wxt:{cells[i_time]}:{cells[i_amt]}"
        if db.execute(select(PromotionFlow).where(
                PromotionFlow.sync_key == sync_key)).scalar_one_or_none():
            skipped += 1
            continue
        io_type = cells[i_io] if i_io is not None and i_io < len(cells) else ""
        kind = cells[i_kind] if i_kind is not None and i_kind < len(cells) else ""
        tdate = None
        if i_date is not None and i_date < len(cells) and cells[i_date]:
            try:
                tdate = date.fromisoformat(cells[i_date][:10])
            except ValueError:
                pass
        remark_parts = [kind]
        if i_remark is not None and i_remark < len(cells):
            remark_parts += [c for c in cells[i_remark:] if c]
        db.add(PromotionFlow(
            sync_key=sync_key,
            transaction_date=tdate,
            # 退余额(推广没扣完关闭后冻结金额退回)≠充值: 自动识别归"退余额", 后续冲减推广投入 (用户 2026-06-24 方案A)
            flow_type=(
                "退余额" if any(("退余额" in p or "退回余额" in p) for p in remark_parts)
                else ("充值" if io_type == "收入" else "支出")
            ),
            amount=Decimal(cells[i_amt].replace(",", "") or "0"),
            remark=" ".join(p for p in remark_parts if p)[:500] or None,
        ))
        inserted += 1
    return {"inserted": inserted, "skipped_duplicate": skipped}


# 余额截图文件夹名 → ERP AccountBalance.account_name (企业号走 API 不在此, 跳过)
_BAL_OCR_MAP = {
    "淘宝聚合": "淘宝聚合账户",
    "推广": "淘宝推广账户",
    "万师傅": "万师傅",
    "支付宝主力号": "主力号",   # 写用户实际用的账户名(原"支付宝-15824198812"对不上UI→OCR写了没人看见; 2026-06-20修)
}
_BAL_MAX = Decimal("5000000")   # 合理上限, 超过视为读错不写


def _ocr_balance_to_db(db: Session, path: Path, raw: bytes) -> tuple[str, str, dict]:
    """B1 (用户拍板 2026-06-12): 余额截图自动 OCR 读「可用余额」→ 置信且数字合理才写 AccountBalance;
    读不准/读不到 → 报异常(alert)、不写库(财务零损伤红线)。企业号走 API 不在此。"""
    from app.models.finance import AccountBalance
    erp_name = next((v for k, v in _BAL_OCR_MAP.items() if k in str(path)), None)
    if not erp_name:
        return ("account_balance", "pending_read",
                {"note": "余额截图已归档 — 未识别账户, 待人工确认"})

    def _flag(reason: str) -> None:
        try:
            from app.services import alert_service
            alert_service.upsert(db, kind="balance_ocr_uncertain", severity="warning",
                                 title=f"余额OCR读不准: {erp_name}",
                                 body=f"{reason} — 已归档未写库, 请人工核对截图后手填。文件: {path.name}",
                                 dedupe_key=f"balance_ocr:{erp_name}:{path.name}")
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    try:
        from app.services import vision_ocr_service
        r = vision_ocr_service.parse_balance_screenshot(db, raw, account_hint=erp_name)
    except Exception as e:  # noqa: BLE001 — OCR 不可用/失败: 归档待人工, 不阻断
        _flag(f"OCR 调用失败: {type(e).__name__}")
        return ("account_balance", "pending_read", {"account": erp_name, "note": "OCR失败, 待人工"})

    avail, conf = r.get("available"), (r.get("confidence") or "").lower()
    try:
        val = Decimal(str(avail)) if avail is not None else None
    except Exception:  # noqa: BLE001
        val = None
    # 读不准/读不到/不合理 → 不写, 报异常
    if val is None or conf != "high" or val < 0 or val > _BAL_MAX:
        _flag(f"读数={avail} 置信={conf}(需 high 且 0~{_BAL_MAX})")
        return ("account_balance", "pending_read",
                {"account": erp_name, "ocr": r, "note": "读不准, 已报异常待人工"})

    today = date.today()
    prev = db.execute(
        select(AccountBalance).where(AccountBalance.account_name == erp_name)
        .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
    ).scalars().first()
    row = db.execute(select(AccountBalance).where(
        AccountBalance.account_name == erp_name,
        AccountBalance.period_year == today.year,
        AccountBalance.period_month == today.month)).scalar_one_or_none()
    if row is None:
        row = AccountBalance(account_name=erp_name, period_year=today.year,
                             period_month=today.month,
                             account_no=(prev.account_no if prev else None),
                             opening_balance=(prev.closing_balance if prev else Decimal("0")))
        db.add(row)
    row.closing_balance = val
    row.as_of_date = today
    row.remark = f"OCR自动读数(可用余额 {val}, {r.get('label_found') or ''}) {today}"
    db.commit()
    return ("account_balance", "imported",
            {"account": erp_name, "balance": str(val), "note": "OCR自动读数已写库"})


def _import_one(db: Session, category: str, path: Path, raw: bytes) -> tuple[str, str, dict]:
    """单文件导入。返回 (归档kind, 状态, 摘要)。
    状态: imported / pending_password / pending_read / unsupported"""
    if category == "taobao_report":
        from app.services import taobao_order_import
        password = None
        if raw[:8] == _OOXML_ENCRYPTED_MAGIC:
            # 加密发货报表: 取飞书最近口令解密。口令不按收到时间失效；没口令才标待口令。
            password = _latest_shipping_password(db)
            if not password:
                return ("taobao", "pending_password",
                        {"note": "加密发货报表 — 待飞书口令(转发『发货密码 xxx』到机器人)后自动解密"})
        rep = taobao_order_import.import_taobao_orders(db, path.name, raw, password=password)
        errs = getattr(rep, "errors", None)
        if errs:
            # 口令不匹配/文件异常 → 仍标待口令 (不算系统错误, 等用户提供对应口令)
            if password and any("解密" in str(e) for e in errs):
                return ("taobao", "pending_password", {"note": str(errs[0])})
            return ("taobao", "error", _report_to_dict(rep))
        return ("taobao", "imported", _report_to_dict(rep))
    if category == "settlement":
        from app.services import settlement_import_service
        rep = settlement_import_service.import_bill(db, raw, source="agent")
        return ("settlement", "imported", _report_to_dict(rep))
    if category == "promotion":
        rep = _import_wanxiangtai_csv(db, raw)
        return ("promotion", "imported" if "error" not in rep else "unsupported", rep)
    if category == "wanshifu":
        import openpyxl
        from app.services import wanshifu_order_service
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
        rep = wanshifu_order_service.import_workbook(db, wb)
        return ("wanshifu_orders", "imported", _report_to_dict(rep))
    if category == "balance":
        return _ocr_balance_to_db(db, path, raw)
    if category == "alipay":
        # 企业号资金账单与个人版交易明细共用自动识别器；目录决定 ERP 账户归属。
        from app.services import alipay_import, tabular
        text = tabular.to_csv_text(raw, path.name)
        account = "主力号" if _is_main_alipay_path(path) else "企业号"
        rep = alipay_import.import_alipay_csv(db, text, account=account)
        if getattr(rep, "errors", None):
            return ("alipay", "error", _report_to_dict(rep))
        summary = _report_to_dict(rep)
        summary["account"] = account
        if account == "主力号":
            settings_service.set_value(
                db,
                "alipay_main_daily_reconciliation",
                json.dumps(
                    {
                        "checked_at": datetime.now().isoformat(timespec="seconds"),
                        "ok": summary.get("reconciliation_ok"),
                        "days": summary.get("daily_reconciliation") or [],
                    },
                    ensure_ascii=False,
                ),
                description="支付宝主力号流水逐日核对（仅 ERP 内部，不外发）",
            )
        return ("alipay", "imported", summary)
    return ("generic", "unsupported", {"note": "未识别类别, 仅归档"})


def refresh_alipay_balances(db: Session) -> list[dict]:
    """刷新支付宝普通余额，并把余利宝净转入作为独立资产余额计入。

    普通余额走官方 ``alipay.data.bill.balance.query`` 精确取数。该接口不含
    余利宝；当前应用未开通独立余利宝查询权限时，余利宝按已导入企业号
    资金账单中的申购、赎回和收益净额估算。两项分行保存，避免内部划转
    被重复算作经营收支。
    """
    from app.models.finance import AccountBalance
    out: list[dict] = []
    accts = web_agent_service.alipay_accounts(db)
    today = date.today()
    for acc in accts:
        erp_name = ALIPAY_ACCT_MAP.get(acc.get("name") or "")
        if not erp_name:
            continue
        r = web_agent_service.alipay_balance(db, acc.get("id"))
        raw = r.get("raw") or {}
        total = raw.get("total_amount") or r.get("balance")
        if not r.get("ok") or total is None:
            out.append({"account": erp_name, "error": r.get("msg") or r.get("error") or "无余额"})
        else:
            prev = db.execute(
                select(AccountBalance).where(AccountBalance.account_name == erp_name)
                .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
            ).scalars().first()
            row = db.execute(select(AccountBalance).where(
                AccountBalance.account_name == erp_name,
                AccountBalance.period_year == today.year,
                AccountBalance.period_month == today.month)).scalar_one_or_none()
            if row is None:
                row = AccountBalance(
                    account_name=erp_name, period_year=today.year, period_month=today.month,
                    account_no=(prev.account_no if prev else None),
                    opening_balance=(prev.closing_balance if prev else Decimal("0")))
                db.add(row)
            row.closing_balance = Decimal(str(total))
            row.as_of_date = today
            avail = raw.get("available_amount")
            frz = raw.get("freeze_amount")
            row.remark = (f"支付宝API精确取数: 可用{avail}+冻结{frz}=总{total} ({today})"
                          if avail is not None else f"支付宝API精确取数: {total} ({today})")
            out.append({"account": erp_name, "balance": str(total), "source": "api_exact"})

        # 余利宝不在普通余额 API 中。按资金账单重建独立资产余额；即使本轮
        # Web-Agent 离线，已入库的余利宝流水仍可安全刷新，不影响普通余额行。
        estimate = yulibao_service.refresh_estimated_balance(
            db, source_account=acc.get("name") or "企业号")
        if estimate.get("ok"):
            out.append({
                "account": estimate.get("account"),
                "balance": str(estimate.get("balance")),
                "as_of_date": str(estimate.get("as_of_date")),
                "source": "flow_estimate",
                "source_count": estimate.get("count"),
            })
        elif estimate.get("reason") == "negative_estimate":
            out.append({
                "account": yulibao_service.YULIBAO_ACCOUNT_NAME,
                "error": "余利宝净转入估算为负，保留原余额并等待人工核对",
                "estimate": estimate.get("balance"),
            })
    db.commit()
    return out


def refresh_alipay_daily(db: Session, *, account_name: str = "企业号",
                         max_days: int = 40) -> dict:
    """按天用官方 API 拉企业号 signcustomer 资金账单 (T+1), 补到昨天。

    官方 alipay.data.dataservice.bill.downloadurl.query 只给已结算的过去日/月, **不给当月整月**
    (传 bill_date=当月 → 40004 TYPE_NOT_SUPPORTED), 故逐日拉。下载落 NAS 共享 alipay_api/,
    随后 run_ingest 经 _import_one('alipay') 自动解析入库 (2026-06-15 接通)。幂等: 重拉同日
    产同名文件 → run_ingest 按 hash 跳过; 流水按 (tx_no,type,amount) 去重, 重复无副作用。"""
    from datetime import date, timedelta

    from sqlalchemy import func as _func
    from app.models.finance import AlipayFlow
    acc = next((a for a in web_agent_service.alipay_accounts(db)
                if (a.get("name") or "") == account_name), None)
    if not acc:
        return {"skip": f"无 {account_name} 账户配置"}
    aid = acc.get("id")
    last = db.query(_func.max(AlipayFlow.transaction_time)).filter(
        AlipayFlow.account == account_name).scalar()
    today = date.today()
    start = last.date() if last else today - timedelta(days=max_days)
    if start < today - timedelta(days=max_days):
        start = today - timedelta(days=max_days)
    end = today - timedelta(days=1)   # T+1: 只到昨天 (当天还没结算)
    out: dict = {"account": account_name, "from": str(start), "to": str(end),
                 "pulled": 0, "fail": 0}
    d = start
    while d <= end:
        try:
            r = web_agent_service.alipay_bill(db, aid, "signcustomer", d.isoformat())
            out["pulled" if r.get("ok") else "fail"] += 1
        except Exception:  # noqa: BLE001 - 单日失败不阻断
            out["fail"] += 1
        d += timedelta(days=1)
    return out


def reingest_pending_shipping(db: Session) -> dict:
    """飞书收到『发货密码』后调用: 用最新口令重试 OUTPUT_DIR 里所有加密发货报表。

    修复 (2026-06-15) —— 以前的死结: 加密报表无口令时标 pending_password 但**照样归档**
    (file_hash 记录), 等口令到了, 下次 run_ingest 又按"已知文件"跳过 → 永远不会解密
    (用户每次都得叫我手动导)。此函数绕过 hash 去重, 直接对所有加密淘宝报表用最新口令重试:
    import 是 upsert 幂等 (重复导无副作用); 一报一密, 口令不匹配的那份自然解密失败、保持
    待解密, 不影响其它份。由飞书口令入站处理器调用, 实现"发口令→自动入库"。"""
    out: dict = {"tried": 0, "imported": 0, "failed": 0, "updated": 0, "files": []}
    pwd = _latest_shipping_password(db)
    if not pwd:
        out["note"] = "尚未收到发货报表口令 (请发送『发货密码 xxx』)"
        return out
    if not OUTPUT_DIR.exists():
        out["note"] = f"共享目录不存在: {OUTPUT_DIR}"
        return out
    from app.services import taobao_order_import
    for path in sorted(OUTPUT_DIR.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        if path.suffix.lower() not in (".xlsx", ".xls"):
            continue
        if _classify(path.relative_to(OUTPUT_DIR)) != "taobao_report":
            continue
        raw = path.read_bytes()
        if raw[:8] != _OOXML_ENCRYPTED_MAGIC:
            continue   # 只重试加密发货报表; 明文报表归 run_ingest 常规流程
        out["tried"] += 1
        try:
            rep = taobao_order_import.import_taobao_orders(
                db, path.name, raw, password=pwd)
            errs = getattr(rep, "errors", None)
            if errs:
                out["failed"] += 1
                out["files"].append({"file": path.name, "status": "pending",
                                     "note": str(errs[0])[:120]})
                continue
            d = _report_to_dict(rep)
            d["agent_status"] = "imported"
            d["agent_report_role"] = "shipping"
            prior = _hash_exists(db, hashlib.sha256(raw).hexdigest())
            prior_summary = (
                prior.row_summary
                if prior is not None and isinstance(prior.row_summary, dict)
                else {}
            )
            for key in (
                "automation_batch_id",
                "agent_report_role_expected",
                "agent_report_role_detected",
                "agent_report_role_source",
            ):
                if prior_summary.get(key) is not None:
                    d[key] = prior_summary[key]
            import_storage.archive(db, content=raw, original_name=path.name,
                                   kind="taobao", source="api", row_summary=d)
            db.commit()
            out["imported"] += 1
            out["updated"] += int(d.get("updated") or 0)
            out["files"].append({"file": path.name, "status": "imported",
                                 "updated": d.get("updated"),
                                 "inserted": d.get("inserted")})
        except Exception as e:  # noqa: BLE001 - 单文件失败不阻断
            db.rollback()
            out["failed"] += 1
            out["files"].append({"file": path.name, "status": "error",
                                 "note": f"{type(e).__name__}: {e}"})
    return out


def _ingest_candidates(only_paths: Optional[list[str]] = None) -> list[Path]:
    """Resolve current-run Agent artifacts inside OUTPUT_DIR.

    Agent responses may contain a Windows/UNC absolute path while ERP mounts
    the same share at another path.  In that case the basename is resolved
    against the mounted output directory.  The fallback full scan remains for
    legacy/manual ingestion calls that have no run manifest.
    """
    if not only_paths:
        return sorted(OUTPUT_DIR.rglob("*"))
    found: dict[str, Path] = {}
    for raw_path in only_paths:
        raw_text = str(raw_path)
        raw = Path(raw_text)
        basename = raw_text.replace("\\", "/").rsplit("/", 1)[-1]
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.append(OUTPUT_DIR / raw)
        if basename:
            candidates.append(OUTPUT_DIR / basename)
            candidates.extend(OUTPUT_DIR.rglob(basename))
        existing = [item for item in candidates if item.is_file()]
        if existing:
            chosen = max(existing, key=lambda item: item.stat().st_mtime)
            found[str(chosen.resolve())] = chosen
    return sorted(found.values(), key=lambda item: str(item))


def run_ingest(
    db: Session,
    *,
    only_paths: Optional[list[str]] = None,
    artifact_roles: Optional[dict[str, str]] = None,
    order_batch_id: str | None = None,
) -> dict:
    """Import current-run artifacts, or all unseen output files as fallback.

    For a scoped order pull, persist the immutable batch id and the report role
    on every artifact evidence row.  The role is checked against the workbook
    content before import; a disagreement is a hard batch error.
    """
    report: dict = {"scanned": 0, "imported": 0, "skipped_known": 0,
                    "pending": 0, "errors": 0, "files": []}
    _pending_pw_files: list[str] = []   # 本轮新下载、待飞书口令解密的加密发货报表
    if not OUTPUT_DIR.exists():
        report["error"] = f"共享目录不存在: {OUTPUT_DIR} (检查 compose 卷挂载)"
        return report
    state = _load_json(db, KEY_STATE)
    scoped_names = {
        Path(str(value).replace("\\", "/")).name
        for value in (only_paths or [])
        if str(value or "").strip()
    }
    for path in _ingest_candidates(only_paths):
        if not path.is_file() or path.name.startswith("_"):
            continue
        if path.suffix.lower() not in (".xlsx", ".xls", ".csv", ".zip", ".png"):
            continue
        rel = path.relative_to(OUTPUT_DIR)
        if rel.parts and rel.parts[0] == "agent_selftest":
            continue
        category = _classify(rel)
        report["scanned"] += 1
        raw = path.read_bytes()
        file_hash = hashlib.sha256(raw).hexdigest()
        expected_role = _normalize_order_report_role(
            (artifact_roles or {}).get(path.name)
        )
        detected_role = None
        if category == "taobao_report":
            from app.services import taobao_order_import

            detected_role = taobao_order_import.detect_report_role(path.name, raw)
        entry = {"path": str(rel), "category": category}
        if expected_role and detected_role and expected_role != detected_role:
            reason = (
                f"报表角色与内容不一致: 清单={expected_role}, 内容={detected_role}"
            )
            entry.update({
                "status": "error",
                "summary": {
                    "error": reason,
                    "agent_report_role_expected": expected_role,
                    "agent_report_role_detected": detected_role,
                    "automation_batch_id": order_batch_id,
                },
            })
            report["errors"] += 1
            report["files"].append(entry)
            continue
        effective_role = detected_role or expected_role
        # The batch id may only label artifacts returned by this Web-Agent
        # run. A broad fallback scan also sees historical Taobao files and must
        # never attach those files to the current business batch.
        batch_for_file = (
            order_batch_id
            if category == "taobao_report"
            and (
                expected_role is not None
                or (bool(scoped_names) and path.name in scoped_names and detected_role is not None)
            )
            else None
        )

        prior = _hash_exists(db, file_hash)
        if prior is not None:
            report["skipped_known"] += 1
            prior_summary = prior.row_summary if isinstance(prior.row_summary, dict) else {}
            status = str(prior_summary.get("agent_status") or "unknown")
            summary = {
                **prior_summary,
                "agent_status": status,
                "agent_report_role": effective_role or prior_summary.get("agent_report_role"),
                "agent_report_role_expected": expected_role,
                "agent_report_role_detected": detected_role,
                "agent_report_role_source": (
                    "content" if detected_role else "agent_manifest" if expected_role else None
                ),
                "automation_batch_id": batch_for_file,
                "duplicate_for_batch": bool(batch_for_file),
            }
            # Same bytes may legitimately recur on a no-change day.  Preserve
            # a new audit row for this batch without importing the orders twice.
            if batch_for_file:
                res = import_storage.archive(
                    db,
                    content=raw,
                    original_name=path.name,
                    kind="taobao",
                    source="api",
                    row_summary=summary,
                )
                db.commit()
                entry["file_id"] = res.file.id
            entry.update({"status": status, "summary": summary})
            report["files"].append(entry)
            continue
        try:
            kind, status, summary = _import_one(db, category, path, raw)
            summary["agent_status"] = status
            if category == "taobao_report":
                summary["agent_report_role"] = effective_role
                summary["agent_report_role_expected"] = expected_role
                summary["agent_report_role_detected"] = detected_role
                summary["agent_report_role_source"] = (
                    "content" if detected_role else "agent_manifest" if expected_role else None
                )
                summary["automation_batch_id"] = batch_for_file
            res = import_storage.archive(
                db, content=raw, original_name=path.name, kind=kind,
                source="api", row_summary=summary)
            db.commit()
            entry.update({"status": status, "summary": summary, "file_id": res.file.id})
            if status == "imported":
                report["imported"] += 1
                state[category] = datetime.now().isoformat(timespec="seconds")
                if category == "alipay" and _is_main_alipay_path(rel):
                    state[STATE_MAIN_ALIPAY_FLOW] = datetime.now().isoformat(timespec="seconds")
            else:
                report["pending"] += 1
                if status == "pending_password":
                    _pending_pw_files.append(path.name)
        except Exception as e:  # noqa: BLE001 - 单文件失败不阻断批量
            db.rollback()
            _log.warning("agent ingest 失败 %s", rel, exc_info=True)
            entry.update({"status": "error",
                          "summary": {"error": f"{type(e).__name__}: {e}"}})
            report["errors"] += 1
        report["files"].append(entry)
    # 主动提醒 (用户要求 2026-06-15): 取数下载到加密发货报表却无口令 → 主动推飞书,
    # 让用户转发『发货密码 xxx』; 收到后 _capture_shipping_password→reingest_pending_shipping
    # 自动解密入库。每份加密文件归档后即 hash-known, 下轮不再 pending → 一份只提醒一次, 不刷屏。
    if _pending_pw_files:
        current_password = _latest_shipping_password(db)
        if current_password:
            # 现有口令曾成功不代表它能解密后来新导出的文件。把本次新文件的
            # 不匹配结果写成最新证据，并暂停无新输入就不可能成功的定时重试。
            # 口令本身不写日志、不写结果，也绝不以“超时/过期”描述。
            pending_files = pending_shipping_password_files(
                db, artifact_names=_pending_pw_files,
            )
            mismatch_reason = (
                "现有发货口令无法解密新报表（口令未过期，但与这些文件不匹配）："
                + ",".join(pending_files[:5] or _pending_pw_files[:5])
            )
            settings_service.set_value(
                db,
                KEY_SHIPPING_PASSWORD_RESULT,
                json.dumps({
                    "status": "password_mismatch",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "pending_files": pending_files,
                    "reason": mismatch_reason,
                }, ensure_ascii=False),
                description="最近发货报表口令匹配结果（不含口令）",
            )
            try:
                from app.services import automation_pipeline_service

                automation_pipeline_service.pause_for_input(
                    db, "order_delivery", mismatch_reason,
                )
            except Exception:
                _log.warning("发货报表口令不匹配后暂停订单重试失败", exc_info=True)
            db.commit()
        import os as _os
        if not _os.environ.get("PANSE_DISABLE_NOTIFY"):
            n = len(_pending_pw_files)
            if current_password:
                _msg = (
                    f"📦 新下载的 {n} 份加密发货报表与系统现有口令不匹配。"
                    "口令没有超时，但新报表需要其对应口令；请把淘宝本次发来的口令以"
                    "『发货密码 xxxx』转发到这里，收到后自动解密、入库并续推下单图。"
                )
            else:
                _msg = (
                    f"📦 取数下载到 {n} 份加密发货报表待解密。请把淘宝发来的口令以"
                    "『发货密码 xxxx』转发到这里 —— 口令不按时间失效，收到后自动解密、入库并续推下单图。"
                )
            # 优先推飞书: 用户本就在飞书转发口令, 且 notify provider 未必配了 webhook
            # (现网=wechat_work 但 webhook 空 → 走 notify 会静默丢失)。飞书推送失败再兜底 notify。
            _pushed = False
            try:
                from app.services import feishu_client
                _chat = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
                if _chat:
                    feishu_client.send_text(db, _chat, _msg)
                    _pushed = True
            except Exception:
                _log.warning("发货报表待口令飞书提醒失败", exc_info=True)
            if not _pushed:
                try:
                    from app.services import notify_service
                    notify_service.notify(db, _msg, level="warn", title="发货报表待口令")
                except Exception:
                    _log.warning("发货报表待口令兜底通知失败", exc_info=True)
    # 联动消异常 (用户要求 2026-06-15): 本轮真有导入 → 立即跑流水匹配(回填 alipay_flow_no /
    # 翻工厂已付 / 建售后等, create_purchases=False 不兜底建采购避免噪音) + 全量复核销账,
    # 让"导入了对应记录就把异常消掉"即时生效, 不必等夜间调度。
    if report["imported"]:
        try:
            from app.services import alipay_flow_router_service, exception_recheck_service
            alipay_flow_router_service.run_all(db, create_purchases=False)
            db.commit()
            report["auto_resolved"] = exception_recheck_service.bulk_close_resolved(db)
            db.commit()
            # 工厂下单表自动并入新订单 (用户拍板 2026-06-15: 已付款/已发货/已签收, 去补单/退款, 幂等去重)
            from app.services import factory_order_service
            report["factory_sync"] = factory_order_service.sync_from_orders(db)
            db.commit()
        except Exception:  # noqa: BLE001 - 联动失败不影响导入结果
            db.rollback()
            _log.warning("ingest 后联动消异常失败", exc_info=True)
    state["last_ingest_at"] = datetime.now().isoformat(timespec="seconds")
    _save_json(db, KEY_STATE, state)
    _save_json(db, KEY_LAST_INGEST, report)
    db.commit()
    return report


# ----------------------------- 编排 ----------------------------- #

def _due(state: dict, category: str, interval_days: int, force: bool) -> bool:
    if force:
        return True
    last = state.get(category)
    if not last:
        return True
    try:
        return datetime.fromisoformat(last) <= datetime.now() - timedelta(days=interval_days)
    except ValueError:
        return True


def _due_today(state: dict, category: str, force: bool) -> bool:
    """按自然日调度，每天最多成功运行一次，不受上次运行时刻漂移影响。"""
    if force:
        return True
    last = state.get(category)
    if not last:
        return True
    try:
        return datetime.fromisoformat(last).date() < date.today()
    except (TypeError, ValueError):
        return True


def _order_pull_tasks(payload: dict | None) -> list[dict]:
    """Return structured task rows while tolerating legacy summary counters.

    Older catch-up runs stored ``tasks`` as an integer count.  Recovery code
    scans those durable rows together with the current manifest, so one legacy
    counter must not abort a new password callback after the report was already
    decrypted.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("tasks")
    if not isinstance(raw, (list, tuple)):
        return []
    return [item for item in raw if isinstance(item, dict)]


def order_pull_artifact_names(payload: dict | None) -> list[str]:
    """Extract the exact three-report manifest from one order-pull result."""
    payload = payload or {}
    task = next(
        (
            item for item in _order_pull_tasks(payload)
            if item.get("task") == "taobao_orders"
            and str(item.get("status") or "").lower() in ("done", "ok", "success")
        ),
        None,
    )
    values = (
        (task or {}).get("artifacts")
        or payload.get("artifacts")
        or payload.get("manifest")
        or []
    )
    if not values:
        values = _job_downloads(payload)
    elif isinstance(values, (str, Path)):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = []
    names: list[str] = []
    for value in values:
        name = Path(str(value).replace("\\", "/")).name
        if name and name not in names:
            names.append(name)
    return names


def order_pull_artifact_roles(payload: dict | None) -> dict[str, str]:
    """Return the durable filename -> role mapping for one pull result."""
    payload = payload or {}
    task = next(
        (
            item for item in _order_pull_tasks(payload)
            if item.get("task") == "taobao_orders"
            and str(item.get("status") or "").lower() in ("done", "ok", "success")
        ),
        None,
    )
    raw_roles = (
        (task or {}).get("artifact_roles")
        or payload.get("artifact_roles")
        or {}
    )
    roles: dict[str, str] = {}
    if isinstance(raw_roles, dict):
        for raw_name, raw_role in raw_roles.items():
            name = Path(str(raw_name).replace("\\", "/")).name
            role = _normalize_order_report_role(raw_role)
            if name and role:
                roles[name] = role
    # Compatibility is intentionally narrow: only explicit semantic fixture /
    # legacy names are inferred. New production pulls persist trusted roles.
    for name in order_pull_artifact_names(payload):
        inferred = _role_from_artifact_name(name)
        if inferred:
            roles.setdefault(name, inferred)
    return roles


def taobao_artifact_roles(
    db: Session,
    artifact_names: list[str],
    *,
    declared_roles: Optional[dict[str, str]] = None,
    order_batch_id: str | None = None,
) -> dict[str, str | None]:
    """Return each manifest artifact's latest trustworthy role evidence."""
    names = [
        Path(str(value).replace("\\", "/")).name
        for value in artifact_names
        if str(value or "").strip()
    ]
    declared = {
        Path(str(name).replace("\\", "/")).name: _normalize_order_report_role(role)
        for name, role in (declared_roles or {}).items()
    }
    rows = db.execute(
        select(ImportedFile)
        .where(
            ImportedFile.kind == "taobao",
            ImportedFile.original_filename.in_(set(names)),
        )
        .order_by(ImportedFile.id.asc())
    ).scalars().all()
    latest: dict[str, ImportedFile] = {}
    for row in rows:
        summary = row.row_summary if isinstance(row.row_summary, dict) else {}
        row_batch = str(summary.get("automation_batch_id") or "")
        name = str(row.original_filename)
        if order_batch_id:
            if row_batch == order_batch_id:
                latest[name] = row
            continue
        latest[name] = row

    out: dict[str, str | None] = {}
    for name in names:
        summary = (
            latest[name].row_summary
            if name in latest and isinstance(latest[name].row_summary, dict)
            else {}
        )
        persisted = _normalize_order_report_role(summary.get("agent_report_role"))
        out[name] = persisted or declared.get(name) or _role_from_artifact_name(name)
    return out


def validate_order_pull_artifact_roles(
    db: Session,
    artifact_names: list[str],
    *,
    declared_roles: Optional[dict[str, str]] = None,
    order_batch_id: str | None = None,
) -> dict:
    """Require exactly one orders, sales-detail and shipping artifact."""
    roles = taobao_artifact_roles(
        db,
        artifact_names,
        declared_roles=declared_roles,
        order_batch_id=order_batch_id,
    )
    by_role: dict[str, list[str]] = {role: [] for role in ORDER_PULL_REQUIRED_ROLES}
    unknown: list[str] = []
    for name, role in roles.items():
        if role in by_role:
            by_role[role].append(name)
        else:
            unknown.append(name)
    missing = sorted(role for role, values in by_role.items() if not values)
    duplicates = {
        role: values for role, values in by_role.items() if len(values) > 1
    }
    return {
        "ok": (
            len(artifact_names) == ORDER_PULL_EXPECTED_ARTIFACT_COUNT
            and not missing
            and not duplicates
            and not unknown
        ),
        "roles": roles,
        "missing_roles": missing,
        "duplicate_roles": duplicates,
        "unknown_artifacts": unknown,
    }


def order_pull_batch_id(payload: dict | None) -> str | None:
    """Read the immutable batch id from a pull result or its order task."""
    payload = payload or {}
    direct = str(payload.get("order_batch_id") or "").strip()
    if direct:
        return direct
    task = next(
        (
            item for item in _order_pull_tasks(payload)
            if item.get("task") == "taobao_orders"
        ),
        None,
    )
    value = str((task or {}).get("order_batch_id") or "").strip()
    return value or None


def latest_order_pull_evidence(db: Session, *, on=None) -> dict:
    """Return the newest durable order-pull evidence for one business day."""
    target = on or date.today()
    candidates: list[tuple[datetime, dict]] = []
    current = _load_json(db, KEY_ORCH_STATE)

    def _append(payload: dict, started_at) -> None:
        if not started_at or not (
            order_pull_artifact_names(payload) or order_pull_batch_id(payload)
        ):
            return
        try:
            value = (
                started_at
                if isinstance(started_at, datetime)
                else datetime.fromisoformat(str(started_at))
            )
        except (TypeError, ValueError):
            return
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        raw_business_date = str(payload.get("order_business_date") or "").strip()
        try:
            evidence_date = date.fromisoformat(raw_business_date) if raw_business_date else value.date()
        except ValueError:
            return
        if evidence_date == target:
            candidates.append((value, payload))

    _append(current, current.get("started_at"))
    from app.models.scheduled_job import ScheduledJobRun

    rows = db.execute(
        select(ScheduledJobRun)
        .where(ScheduledJobRun.job_id.in_((
            "daily_0630_web_agent",
            "pull_catchup_30min",
            "order_delivery_recovery",
        )))
        .order_by(ScheduledJobRun.id.desc())
        .limit(50)
    ).scalars().all()
    for row in rows:
        _append(row.result_summary or {}, row.started_at)
    if not candidates:
        return {}
    return max(candidates, key=lambda item: item[0])[1]


def latest_order_pull_artifact_names(db: Session, *, on=None) -> list[str]:
    """Return the latest order pull's durable artifact manifest."""
    return order_pull_artifact_names(latest_order_pull_evidence(db, on=on))


def latest_order_pull_batch_id(db: Session, *, on=None) -> str | None:
    """Return today's active order recovery-chain batch id, if present."""
    return order_pull_batch_id(latest_order_pull_evidence(db, on=on))


def taobao_artifact_states(
    db: Session,
    artifact_names: list[str],
    *,
    order_batch_id: str | None = None,
) -> dict[str, str]:
    """Return each manifest file's latest durable ingest state."""
    names = {
        Path(str(value).replace("\\", "/")).name
        for value in artifact_names
        if str(value or "").strip()
    }
    if not names:
        return {}
    rows = db.execute(
        select(ImportedFile)
        .where(
            ImportedFile.kind == "taobao",
            ImportedFile.original_filename.in_(names),
        )
        .order_by(ImportedFile.id.asc())
    ).scalars().all()
    latest: dict[str, str] = {}
    for row in rows:
        summary = row.row_summary if isinstance(row.row_summary, dict) else {}
        row_batch = str(summary.get("automation_batch_id") or "")
        status = str(summary.get("agent_status") or "unknown")
        if order_batch_id:
            if row_batch == order_batch_id:
                latest[row.original_filename] = status
            continue
        latest[row.original_filename] = status
    return {name: latest.get(name, "missing") for name in sorted(names)}


def pending_shipping_password_files(
    db: Session, *, on=None, all_dates: bool = False, latest_only: bool = False,
    artifact_names: list[str] | set[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """返回仍会阻塞指定订单拉取批次的加密发货报表。

    每次订单拉取都会保存本次三份下载物的精确清单。``artifact_names`` 非空时只
    检查该清单；其他批次留下的待口令文件继续保留审计，但不得污染本批次的新鲜度门。
    同一物理文件仍按哈希取最后状态，因此跨午夜补口令也能覆盖此前的待处理记录。

    默认只检查指定日期；``all_dates`` 用于口令回调跨午夜继续处理旧报表。
    """
    target = on or date.today()
    allowed_names = None
    if artifact_names is not None:
        allowed_names = {
            Path(str(name).replace("\\", "/")).name
            for name in artifact_names
            if str(name or "").strip()
        }
    rows = db.execute(
        select(ImportedFile)
        .where(ImportedFile.kind == "taobao")
        .order_by(ImportedFile.id.asc())
    ).scalars().all()

    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    # Group every retry/archive record of the same physical report.  The first
    # record owns the operational batch date; the last record is its current
    # resolution state.
    artifacts: dict[str, dict] = {}
    for row in rows:
        created_at = getattr(row, "created_at", None)
        if not created_at:
            continue
        key = row.file_hash or f"id:{row.id}"
        summary = row.row_summary or {}
        artifact = artifacts.get(key)
        if artifact is None:
            artifacts[key] = {
                "first": row,
                "latest": row,
                "shipping": summary.get("agent_status") == "pending_password",
                "names": {row.original_filename or ""},
            }
        else:
            artifact["latest"] = row
            artifact["shipping"] = bool(
                artifact["shipping"]
                or summary.get("agent_status") == "pending_password"
            )
            artifact["names"].add(row.original_filename or "")

    shipping = [item for item in artifacts.values() if item["shipping"]]

    def _batch_date(item: dict):
        # Database server defaults are UTC.  Compare by ERP local calendar
        # date so files received between 00:00 and 08:00 remain in today's
        # operational batch.
        return _aware(item["first"].created_at).astimezone().date()

    unresolved = []
    for item in shipping:
        latest_summary = item["latest"].row_summary or {}
        if latest_summary.get("agent_status") != "pending_password":
            continue
        day = _batch_date(item)
        if not all_dates and day != target:
            continue
        if allowed_names is not None and not (item["names"] & allowed_names):
            continue
        unresolved.append(item)

    if all_dates and latest_only and unresolved:
        newest = max(_batch_date(item) for item in unresolved)
        unresolved = [item for item in unresolved if _batch_date(item) == newest]
    unresolved.sort(key=lambda item: (item["first"].created_at, item["first"].id))
    return [
        item["latest"].original_filename or f"imported_file:{item['latest'].id}"
        for item in unresolved
    ]


def order_data_fresh(db: Session, *, on=None, not_before_hour: int | None = None) -> bool:
    """今日淘宝订单数据是否已刷新 = web_agent_state.taobao_report 日期 >= 今天。
    作自动推送的「新鲜度门」: 订单近3月全量取数成功(或当天有订单报表导入)才算新鲜 —
    防用隔夜旧数据把已关闭/已退款的单误推工厂群 (2026-07-07 关闭单误推根治)。"""
    from datetime import date as _date, datetime as _dt
    state = _load_json(db, KEY_STATE)
    # The 18:00 push gate requires proof that the complete 3-report pull
    # finished, not merely that one Taobao workbook happened to be imported.
    key = "taobao_orders_complete" if not_before_hour is not None else "taobao_report"
    tr = state.get(key)
    if not tr:
        return False
    try:
        refreshed_at = _dt.fromisoformat(tr)
        target_date = on or _date.today()
        business_date = str(state.get("taobao_orders_complete_business_date") or "")
        if not_before_hour is not None and business_date != target_date.isoformat():
            return False
        if refreshed_at.date() < target_date:
            return False
        if (not_before_hour is not None and refreshed_at.date() == target_date
                and refreshed_at.hour < not_before_hour):
            return False
        complete_artifacts = list(dict.fromkeys(
            Path(str(value).replace("\\", "/")).name
            for value in (state.get("taobao_orders_complete_artifacts") or [])
            if str(value or "").strip()
        ))
        batch_id = str(state.get("taobao_orders_complete_batch_id") or "").strip()
        legacy_evidence = bool(state.get("taobao_orders_complete_legacy_evidence"))
        declared_roles = state.get("taobao_orders_complete_artifact_roles") or {}
        if not_before_hour is not None and not batch_id and not legacy_evidence:
            return False
        if not_before_hour is not None:
            if len(complete_artifacts) != ORDER_PULL_EXPECTED_ARTIFACT_COUNT:
                return False
            artifact_states = taobao_artifact_states(
                db,
                complete_artifacts,
                order_batch_id=None if legacy_evidence else batch_id,
            )
            if any(status != "imported" for status in artifact_states.values()):
                return False
            role_check = validate_order_pull_artifact_roles(
                db,
                complete_artifacts,
                declared_roles=declared_roles,
                order_batch_id=None if legacy_evidence else batch_id,
            )
            if not role_check["ok"]:
                return False
            if pending_shipping_password_files(
                db, on=target_date, artifact_names=complete_artifacts,
            ):
                return False
        return True
    except (ValueError, TypeError):
        return False


def finalize_order_pull_after_shipping_password(
    db: Session, *, on=None, not_before_hour: int = 18,
    now: datetime | None = None,
) -> dict:
    """口令补齐最后一份发货报表后，把本轮已完成的淘宝取数正式收口。

    ``taobao_orders_complete`` 原本只在 orchestrate 退出前、三份报表当场全部可导入时写入。
    加密发货报表通常要等用户稍后从飞书补口令，此时 orchestrate 已经结束；即使解密成功，
    新鲜度门仍会一直判旧，后续下单图补跑全部被拦住。

    只有同时满足以下证据才补写完成标记，避免把隔夜或不完整数据误判为可推：
    - 今天指定时点后的 orchestrate 确实跑完 ``taobao_orders``；
    - 今日已有淘宝报表成功导入；
    - 今日已无待口令文件。
    """
    current = now or datetime.now()
    target = on or current.date()

    # KEY_ORCH_STATE 只保留“最近一次编排”，20:30 财务取数会覆盖 18:00 淘宝取数。
    # 因此先看内存态，找不到时再从不可覆盖的 ScheduledJobRun 历史取当天证据。
    evidence = _load_json(db, KEY_ORCH_STATE)
    evidence_started_at = evidence.get("started_at")

    def _is_manual_recovery(payload: dict) -> bool:
        return bool(payload.get("manual_recovery") or payload.get("manual_pull"))

    def _business_date(payload: dict, started_at) -> date | None:
        raw = str(payload.get("order_business_date") or "").strip()
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                return None
        try:
            value = (
                datetime.fromisoformat(str(started_at))
                if not isinstance(started_at, datetime)
                else started_at
            )
            return value.astimezone().date() if value.tzinfo else value.date()
        except (TypeError, ValueError):
            return None

    def _evidence_matches(payload: dict, started_at) -> bool:
        if not started_at:
            return False
        try:
            dt = (
                datetime.fromisoformat(str(started_at))
                if not isinstance(started_at, datetime)
                else started_at
            )
        except (TypeError, ValueError):
            return False
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        task = next(
            (t for t in _order_pull_tasks(payload) if t.get("task") == "taobao_orders"),
            None,
        )
        business_day = _business_date(payload, started_at)
        allowed_days = {target} if on is not None else {
            current.date(), current.date() - timedelta(days=1),
        }
        return (
            business_day in allowed_days
            # Scheduled pulls remain restricted to the approved evening
            # window. A durable user-triggered recovery may run earlier.
            and (_is_manual_recovery(payload) or dt.hour >= not_before_hour)
            and (task or {}).get("status", "").lower() in ("done", "ok", "success")
        )

    if not _evidence_matches(evidence, evidence_started_at):
        from app.models.scheduled_job import ScheduledJobRun

        rows = db.execute(
            select(ScheduledJobRun)
            .where(ScheduledJobRun.job_id.in_((
                "daily_0630_web_agent",
                "pull_catchup_30min",
            )))
            .order_by(ScheduledJobRun.id.desc())
            .limit(30)
        ).scalars().all()
        matched = next(
            (
                row
                for row in rows
                if _evidence_matches(row.result_summary or {}, row.started_at)
            ),
            None,
        )
        if matched is None:
            return {"completed": False, "reason": "missing_current_order_pull_evidence"}
        evidence = matched.result_summary or {}
        evidence_started_at = matched.started_at

    evidence_target = _business_date(evidence, evidence_started_at)
    if evidence_target is None:
        return {"completed": False, "reason": "order_business_date_missing"}
    target = evidence_target

    order_task = next(
        (t for t in _order_pull_tasks(evidence) if t.get("task") == "taobao_orders"),
        None,
    )
    if (order_task or {}).get("status", "").lower() not in ("done", "ok", "success"):
        return {"completed": False, "reason": "taobao_orders_task_not_complete"}
    batch_artifacts = order_pull_artifact_names(evidence)
    declared_roles = order_pull_artifact_roles(evidence)
    if len(batch_artifacts) != ORDER_PULL_EXPECTED_ARTIFACT_COUNT:
        return {
            "completed": False,
            "reason": "order_pull_manifest_incomplete",
            "expected": ORDER_PULL_EXPECTED_ARTIFACT_COUNT,
            "actual": len(batch_artifacts),
            "artifacts": batch_artifacts,
        }
    batch_id = order_pull_batch_id(evidence)
    legacy_evidence = not bool(batch_id)
    if not batch_id:
        material = target.isoformat() + "\n" + "\n".join(sorted(batch_artifacts))
        batch_id = "legacy-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    if pending_shipping_password_files(
        db,
        on=target,
        artifact_names=batch_artifacts,
    ):
        return {"completed": False, "reason": "shipping_password_still_pending"}
    batch_states = taobao_artifact_states(
        db,
        batch_artifacts,
        order_batch_id=None if legacy_evidence else batch_id,
    )
    if any(
        status != "imported" for status in batch_states.values()
    ):
        return {
            "completed": False,
            "reason": "order_pull_artifacts_not_imported",
            "artifact_states": batch_states,
        }
    role_check = validate_order_pull_artifact_roles(
        db,
        batch_artifacts,
        declared_roles=declared_roles,
        order_batch_id=None if legacy_evidence else batch_id,
    )
    if not role_check["ok"]:
        return {
            "completed": False,
            "reason": "order_pull_artifact_roles_invalid",
            "role_check": role_check,
        }

    state = _load_json(db, KEY_STATE)
    try:
        report_at = datetime.fromisoformat(str(state.get("taobao_report") or ""))
    except (TypeError, ValueError):
        return {"completed": False, "reason": "missing_imported_order_report"}
    evidence_hour = 0 if _is_manual_recovery(evidence) else not_before_hour
    if report_at.date() != target or report_at.hour < evidence_hour:
        return {"completed": False, "reason": "imported_order_report_outside_current_window"}

    completed_at = current.isoformat(timespec="seconds")
    state["taobao_orders_complete"] = completed_at
    if batch_artifacts:
        state["taobao_orders_complete_artifacts"] = batch_artifacts
    state["taobao_orders_complete_artifact_roles"] = role_check["roles"]
    state["taobao_orders_complete_batch_id"] = batch_id
    state["taobao_orders_complete_business_date"] = target.isoformat()
    state["taobao_orders_complete_legacy_evidence"] = legacy_evidence
    _save_json(db, KEY_STATE, state)
    db.commit()
    return {
        "completed": True,
        "completed_at": completed_at,
        "artifacts": batch_artifacts,
        "artifact_roles": role_check["roles"],
        "order_batch_id": batch_id,
        "order_business_date": target.isoformat(),
    }


def orchestrate(db: Session, *, force: bool = False, quiet: bool = False,
                force_orders: bool = False, force_finance: bool = False,
                orders_only: bool = False,
                skip_tasks: set[str] | None = None,
                order_batch_id: str | None = None,
                order_business_date: date | None = None) -> dict:
    """串行执行一次取数编排；调度、手动取数和补跑共用同一把锁。"""
    if not _orch_lock.acquire(blocking=False):
        return {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "tasks": [],
            "pending_manual": [],
            "skipped": ["orchestrate_running"],
            "already_running": True,
        }
    try:
        return _orchestrate_locked(
            db, force=force, quiet=quiet,
            force_orders=force_orders, force_finance=force_finance,
            orders_only=orders_only, skip_tasks=skip_tasks,
            order_batch_id=order_batch_id,
            order_business_date=order_business_date)
    except Exception:
        try:
            web_agent_service.request_stop(
                db, reason="orchestration_failed"
            )
        except Exception:  # noqa: BLE001
            _log.warning("编排异常后请求关闭Web-Agent失败", exc_info=True)
        raise
    finally:
        _orch_lock.release()


def _orchestrate_locked(db: Session, *, force: bool = False, quiet: bool = False,
                        force_orders: bool = False, force_finance: bool = False,
                        orders_only: bool = False,
                        skip_tasks: set[str] | None = None,
                        order_batch_id: str | None = None,
                        order_business_date: date | None = None) -> dict:
    """每日编排: 探活 → 按更新间隔触发到期任务(串行) → 扫描导入 → 汇总。"""
    business_date = order_business_date or date.today()
    if (orders_only or force_orders) and not order_batch_id:
        order_batch_id = new_order_batch_id(on=business_date)
    out: dict = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "tasks": [], "pending_manual": [], "task_errors": [], "skipped": [],
    }
    if order_batch_id:
        out["order_batch_id"] = order_batch_id
        out["order_business_date"] = business_date.isoformat()
    from app.services import automation_pipeline_service

    pipeline = "order_delivery" if orders_only else (
        "balance_pull" if force_finance else "order_delivery"
    )
    automation_pipeline_service.record_stage(
        db, pipeline, "wake_requested", detail="NAS请求Windows按需启动Web-Agent"
    )
    db.commit()
    hb = web_agent_service.ensure_online(
        db,
        reason="scheduled_order_pull" if orders_only else "scheduled_finance_pull",
    )
    if not hb.get("online"):
        automation_pipeline_service.record_stage(
            db, pipeline, "agent_online", status="fail", detail=hb.get("error")
        )
        out["agent_offline"] = hb.get("error", "无法连接")
        out["ingest"] = run_ingest(db)   # Agent 掉线也把已有文件扫了
        out["yulibao_after_ingest"] = yulibao_service.refresh_estimated_balance(db)
        _save_json(db, KEY_ORCH_STATE, {**out, "running": False})
        db.commit()
        return out

    automation_pipeline_service.record_stage(
        db, pipeline, "agent_online", detail="Web-Agent已按需启动并通过认证探活"
    )
    db.commit()

    tasks_info = {t["id"]: t for t in (web_agent_service.list_tasks(db).get("tasks") or [])}
    state = _load_json(db, KEY_STATE)
    iv_orders = _get_int(db, KEY_INTERVAL_ORDERS, 1)
    iv_balance = _get_int(db, KEY_INTERVAL_BALANCE, 3)

    plan: list[str] = []
    if force_orders or (not force_finance and _due(state, "taobao_report", iv_orders, force)):
        plan += ORDERS_TASKS
    if "taobao_orders" in plan and not order_batch_id:
        order_batch_id = new_order_batch_id(on=business_date)
        out["order_batch_id"] = order_batch_id
        out["order_business_date"] = business_date.isoformat()
    finance_force = force or force_finance
    if (not orders_only and (_due(state, "settlement", iv_balance, finance_force)
            or _due(state, "balance", iv_balance, finance_force)
            or _due(state, "promotion", iv_balance, finance_force))):
        plan += BALANCE_FLOW_TASKS
    if not orders_only and _due_today(state, STATE_MAIN_ALIPAY_FLOW, finance_force):
        plan.append(MAIN_ALIPAY_FLOW_TASK)

    # 有限补跑只重试仍缺失的数据源。扫码后文件可能已经异步入库，若仍强制
    # 重跑整套财务任务，会把刚成功的支付宝会话再次拉到登录页并重复提醒扫码。
    if skip_tasks:
        plan = [task_id for task_id in plan if task_id not in skip_tasks]
        out["skipped"].extend(
            {"task": task_id, "reason": "today_data_already_fresh"}
            for task_id in sorted(skip_tasks)
        )
        # A failed attempt may have queued a Feishu "scan" action before the
        # asynchronously downloaded artifact was ingested.  Once today's data
        # is present, keeping that stale queue would make a later user reply
        # launch the login flow again even though no scan is needed.
        pending_scans = get_pending_scans(db)
        remaining_scans = [task_id for task_id in pending_scans if task_id not in skip_tasks]
        if remaining_scans != pending_scans:
            settings_service.set_value(
                db,
                KEY_PENDING_SCAN,
                json.dumps(remaining_scans),
                description="自动取数: 待用户扫码的任务",
            )
            db.commit()

    for task_id, reason in SKIPPED_TASKS.items():
        out["skipped"].append({"task": task_id, "reason": reason})

    # 支付宝企业号余额: 走官方 API 精确刷 — 每次编排都刷 (API 便宜, 无浏览器/无扫码,
    # 用户拍板 2026-06-12 企业号余额"每天刷"; 不受余额/流水的 3 天截图周期限制)。
    if not orders_only:
        try:
            out["alipay_balance"] = refresh_alipay_balances(db)
            state["alipay_balance"] = datetime.now().isoformat(timespec="seconds")
            _save_json(db, KEY_STATE, state)
            db.commit()
        except Exception as e:  # noqa: BLE001
            out["alipay_balance"] = [{"error": f"{type(e).__name__}: {e}"}]
    # 企业号流水: 按天官方API补到昨天(T+1), 落NAS共享; 下方 run_ingest 经 _import_one 自动入库。
    # 每轮都补(官方API便宜、无浏览器/无扫码); 解决"当月整月API取不了→流水停更"(2026-06-15)。
    if not orders_only:
        try:
            out["alipay_daily"] = refresh_alipay_daily(db)
        except Exception as e:  # noqa: BLE001
            out["alipay_daily"] = {"error": f"{type(e).__name__}: {e}"}

    today = date.today()
    orders_pull_complete = False
    main_alipay_task_succeeded = False
    run_artifacts: list[str] = []
    run_artifact_roles: dict[str, str] = {}
    for task_id in plan:
        info = tasks_info.get(task_id, {})
        if (task_id != MAIN_ALIPAY_FLOW_TASK
                and info and not info.get("has_session")):
            out["pending_manual"].append(
                {"task": task_id, "reason": "登录态缺失 — 请到取数控制台重新扫码"})
            continue
        # 不传日期 (用户拍板 2026-06-12): 淘宝导出走"近3个月"全量, 每次刷新所有订单状态,
        # 避免按几天导漏掉中间某天的状态变化。Web-Agent 录制工作流本就无选日期步骤。
        variables = _task_run_variables(task_id, db, on=today)
        artifacts_before = (
            _main_alipay_artifacts()
            if task_id == MAIN_ALIPAY_FLOW_TASK else None
        )
        _save_json(db, KEY_ORCH_STATE, {
            **out,
            "running": True,
            "current": task_id,
        })
        db.commit()
        r = web_agent_service.run_task(db, task_id, variables)
        if not r.get("ok", True) or not r.get("job"):
            out["tasks"].append({"task": task_id, "status": "error",
                                 "error": r.get("error", "无 job id")})
            continue
        # 淘宝3报表异步导出受淘宝"两次导出≥5分钟"限流, 整轮可达~18分钟 → 等到 30 分钟,
        # 与 Web-Agent agent_total_timeout_s(1500s) 对齐, 避免单轮假超时 (2026-06-15)。
        final = web_agent_service.wait_job(db, r["job"], timeout_s=1800)
        status = (final.get("status") or "").lower()
        job_result = final.get("result") or {}
        task_artifacts = _job_downloads(job_result)
        task_artifact_roles = _job_artifact_roles(job_result)
        run_artifacts.extend(task_artifacts)
        run_artifact_roles.update(task_artifact_roles)
        quota_evidence: dict = {}
        if task_id == "taobao_orders":
            quota = job_result.get("quota_bump") or {}
            quota_verified = bool(quota.get("ok")) and bool(
                quota.get("already_unlimited") or quota.get("verified_unlimited")
            )
            quota_state = (
                "already_unlimited" if quota.get("already_unlimited")
                else "verified_unlimited" if quota.get("verified_unlimited")
                else "not_verified"
            )
            quota_evidence = {
                "verified": quota_verified,
                "state": quota_state,
                "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "order_batch_id": order_batch_id,
                "order_business_date": business_date.isoformat(),
            }
            settings_service.set_value(
                db, KEY_ORDER_QUOTA_RESULT,
                json.dumps(quota_evidence, ensure_ascii=False),
                description="淘宝订单导出前最近一次解密额度核验（不含敏感内容）",
            )
        if status in ("done", "ok", "success") and job_result.get("ok") is False:
            status = "error"
            final = {**final, "error": job_result.get("errors") or "任务结果不完整"}
        if (task_id == MAIN_ALIPAY_FLOW_TASK
                and status in ("done", "ok", "success")
                and _main_alipay_artifacts() == artifacts_before):
            status = "error"
            final = {**final, "error": "任务完成但未生成支付宝主力账号流水文件"}
        item = {"task": task_id, "status": status}
        if job_result.get("no_data"):
            item["no_data"] = True
            item["message"] = str(job_result.get("message") or "本期无新增数据")[:200]
        if task_artifacts:
            item["artifacts"] = task_artifacts
        if task_artifact_roles:
            item["artifact_roles"] = task_artifact_roles
        if task_id == "taobao_orders" and order_batch_id:
            item["order_batch_id"] = order_batch_id
            item["order_business_date"] = business_date.isoformat()
            item["quota_verified"] = quota_evidence.get("verified", False)
            item["quota_state"] = quota_evidence.get("state", "not_verified")
        if status in ("error", "failed", "timeout"):
            err = str(final.get("error") or final.get("note") or "")
            item["error"] = err[:300]
            if any(marker in err for marker in (
                "需扫码", "扫码", "登录状态已失效", "需重新登录", "session_expired",
            )):
                # 登录/扫码需要用户输入：记入待处理清单，不在无人值守编排里
                # 继续盲跑。万师傅不是支付宝二维码，提示应准确要求重新登录。
                _add_pending_scan(db, task_id)
                reason = (
                    "登录状态已失效 — 请到取数控制台重新登录，成功后自动续跑"
                    if any(marker in err for marker in (
                        "登录状态已失效", "需重新登录", "session_expired",
                    ))
                    else "需扫码 — 方便时在飞书回复『扫码』启动"
                )
                out["pending_manual"].append(
                    {"task": task_id, "reason": reason})
            else:
                out["task_errors"].append(
                    {"task": task_id, "reason": f"任务{status}: {err[:120]}"})
        out["tasks"].append(item)
        if (task_id == "taobao_orders"
                and status in ("done", "ok", "success")
                and item.get("artifacts")):
            orders_pull_complete = True
        if task_id == MAIN_ALIPAY_FLOW_TASK and status in ("done", "ok", "success"):
            main_alipay_task_succeeded = True

    out["ingest"] = run_ingest(
        db,
        only_paths=run_artifacts if orders_only and run_artifacts else None,
        artifact_roles=run_artifact_roles or None,
        order_batch_id=order_batch_id,
    )
    out["artifacts"] = run_artifacts
    out["artifact_roles"] = run_artifact_roles
    automation_pipeline_service.record_stage(
        db,
        pipeline,
        "artifact_ingest",
        status="fail" if out["ingest"].get("errors") else "ok",
        detail=(
            f"导入{int(out['ingest'].get('imported') or 0)}份，"
            f"失败{int(out['ingest'].get('errors') or 0)}份，"
            f"待处理{int(out['ingest'].get('pending') or 0)}份"
        ),
        artifacts=run_artifacts,
    )
    # 余额 API 先于资金账单下载执行。导入完成后再重算一次余利宝，避免
    # 新增申购/赎回要多等一个自动化周期才进入可用资金。
    out["yulibao_after_ingest"] = yulibao_service.refresh_estimated_balance(db)
    db.commit()
    if "taobao_orders" in plan:
        out["order_changes"] = summarize_order_changes_since_last_complete(db, out["ingest"])
        out["order_message"] = format_order_change_message(out["order_changes"])
    main_alipay_ingest_failed = any(
        item.get("status") == "error"
        and "主力" in str(item.get("path") or "")
        and item.get("category") == "alipay"
        for item in out["ingest"].get("files", [])
    )
    if main_alipay_task_succeeded and not main_alipay_ingest_failed:
        # 即使导出内容与昨日完全相同（文件 hash 已见过），成功取数也算今日完成。
        latest_state = _load_json(db, KEY_STATE)
        latest_state[STATE_MAIN_ALIPAY_FLOW] = datetime.now().isoformat(timespec="seconds")
        _save_json(db, KEY_STATE, latest_state)
        db.commit()
    # 财务重试必须按来源复用今日已经完成的数据，而不是把一次总任务的
    # “执行过”当作全部成功。每个浏览器流水来源都有独立、可持久化的完成标记；
    # 导出类任务还必须真的返回文件，且该文件没有入库错误。
    latest_state = _load_json(db, KEY_STATE)
    finance_markers = latest_state.get(STATE_FINANCE_TASK_SUCCESS)
    if not isinstance(finance_markers, dict):
        finance_markers = {}
    error_artifacts = {
        Path(str(file_info.get("path") or "")).name
        for file_info in (out["ingest"].get("files") or [])
        if file_info.get("status") == "error"
    }
    marked_at = datetime.now().isoformat(timespec="seconds")
    for task in out.get("tasks") or []:
        task_id = str(task.get("task") or "")
        if task_id not in FINANCE_BROWSER_FLOW_TASKS:
            continue
        if str(task.get("status") or "").lower() not in ("done", "ok", "success"):
            continue
        artifacts = [str(path) for path in (task.get("artifacts") or []) if path]
        if (task_id in FINANCE_EXPORT_TASKS
                and not artifacts
                and not task.get("no_data")):
            continue
        if task_id == MAIN_ALIPAY_FLOW_TASK and main_alipay_ingest_failed:
            continue
        if any(Path(path).name in error_artifacts for path in artifacts):
            continue
        finance_markers[task_id] = marked_at
    latest_state[STATE_FINANCE_TASK_SUCCESS] = finance_markers

    daily_flow = out.get("alipay_daily")
    enterprise_ingest_failed = any(
        item.get("status") == "error"
        and item.get("category") == "alipay"
        and "主力" not in str(item.get("path") or "")
        for item in (out["ingest"].get("files") or [])
    )
    if (isinstance(daily_flow, dict)
            and not daily_flow.get("error")
            and not daily_flow.get("skip")
            and int(daily_flow.get("fail") or 0) == 0
            and not enterprise_ingest_failed):
        latest_state[STATE_ENTERPRISE_ALIPAY_FLOW] = marked_at
    _save_json(db, KEY_STATE, latest_state)
    db.commit()
    order_batch_artifacts = order_pull_artifact_names(out)
    declared_order_roles = order_pull_artifact_roles(out)
    persistent_pending_password = pending_shipping_password_files(
        db,
        on=today,
        artifact_names=order_batch_artifacts or None,
    )
    out["ingest"]["pending_password_files"] = persistent_pending_password
    order_artifact_states = taobao_artifact_states(
        db, order_batch_artifacts, order_batch_id=order_batch_id,
    )
    out["ingest"]["order_artifact_states"] = order_artifact_states
    role_check = validate_order_pull_artifact_roles(
        db,
        order_batch_artifacts,
        declared_roles=declared_order_roles,
        order_batch_id=order_batch_id,
    )
    out["ingest"]["order_artifact_role_check"] = role_check
    order_batch_ready = (
        len(order_batch_artifacts) == ORDER_PULL_EXPECTED_ARTIFACT_COUNT
        and not out["ingest"].get("errors")
        and role_check["ok"]
        and all(
        status == "imported" for status in order_artifact_states.values()
        )
    )
    if (orders_pull_complete and order_batch_ready
            and not persistent_pending_password):
        latest_state = _load_json(db, KEY_STATE)
        completed_at = datetime.now().isoformat(timespec="seconds")
        latest_state["taobao_report"] = completed_at
        latest_state["taobao_orders_complete"] = completed_at
        latest_state["taobao_orders_complete_artifacts"] = order_batch_artifacts
        latest_state["taobao_orders_complete_artifact_roles"] = role_check["roles"]
        latest_state["taobao_orders_complete_batch_id"] = order_batch_id
        latest_state["taobao_orders_complete_business_date"] = business_date.isoformat()
        latest_state["taobao_orders_complete_legacy_evidence"] = False
        latest_state["taobao_orders_last_result"] = {
            "date": today.isoformat(),
            "message": out.get("order_message") or "没有新增订单",
            **(out.get("order_changes") or {}),
        }
        _save_json(db, KEY_STATE, latest_state)
        db.commit()

    # 取数「全部成功」(无待扫码/无失败任务) → 立刻补生成工厂下单图 (静默, 不推飞书; 推送仍按 18:00)。
    # 有报错/需扫码 → 跳过, 等今天重新扫码全部成功后再生成 (用户拍板 2026-06-17)。
    if orders_pull_complete:
        # Finance/login work may run in the same orchestration.  Its failure
        # must be recorded in its own pipeline, but cannot turn a verified
        # three-report order batch into a false order-delivery failure.
        all_ok = order_batch_ready and not persistent_pending_password
    else:
        # Finance-only and generic scans are not order-delivery evidence.
        # They may import data, but must never generate factory images.
        all_ok = False
    if all_ok:
        try:
            from app.services import order_sheet_archive_service
            out["order_sheets"] = order_sheet_archive_service.generate_pending(db)
        except Exception as e:  # noqa: BLE001
            out["order_sheets"] = {"error": f"{type(e).__name__}: {e}"}
    else:
        out["order_sheets"] = {"skipped": "取数未全部成功(有报错/需扫码), 等重新扫码全部成功后再生成下单图"}

    out["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_json(db, KEY_ORCH_STATE, {**out, "running": False})
    db.commit()

    # 编排线程返回时已没有仍在运行的浏览器 job。待登录/待扫码只保留为 ERP
    # 状态；不让完整 Agent 空等。用户回复“扫码”时由轻量桥重新按需启动。
    try:
        web_agent_service.request_stop(db, reason="orchestration_finished")
    except Exception:  # noqa: BLE001
        _log.warning("编排完成后请求关闭Web-Agent失败", exc_info=True)

    # 飞书汇总 (复用机器人通道; 测试环境 PANSE_DISABLE_NOTIFY 静默; quiet=续跑补取数不刷屏)
    if not quiet:
        try:
            from app.services import notify_service
            ing = out["ingest"]
            text = (f"自动取数完成: 任务 {len(out['tasks'])} 个, "
                    f"新导入 {ing.get('imported', 0)} 份, 待人工 {len(out['pending_manual'])} 项。")
            if out["pending_manual"]:
                text += "\n待人工: " + "; ".join(
                    f"{p['task']}({p['reason'][:40]})" for p in out["pending_manual"][:5])
            notify_service.notify(db, text, level="info", title="畔色 ERP [自动取数日报]")
        except Exception:  # pragma: no cover
            pass
    return out


def start_orchestrate_async(*, force: bool = False) -> bool:
    """手动「立即取数」: 后台线程跑编排。已在跑返回 False。"""
    if not _orch_lock.acquire(blocking=False):
        return False

    def _run() -> None:
        from app.database import SessionLocal
        db = SessionLocal()
        failed = False
        try:
            _orchestrate_locked(db, force=force)
        except Exception:  # noqa: BLE001
            failed = True
            _log.exception("web-agent 编排线程异常")
            db.rollback()
        finally:
            if failed:
                try:
                    web_agent_service.request_stop(
                        db, reason="orchestration_thread_failed"
                    )
                except Exception:  # noqa: BLE001
                    _log.warning("编排线程异常后请求关闭Web-Agent失败", exc_info=True)
            db.close()
            _orch_lock.release()

    threading.Thread(target=_run, name="web-agent-orchestrate", daemon=True).start()
    return True


def is_running() -> bool:
    if _orch_lock.acquire(blocking=False):
        _orch_lock.release()
        return False
    return True


def pull_orders_async(db: Session) -> dict:
    """手动「更新拉取订单」(订单页按钮): 后台触发淘宝订单近3月全量下载 + 导入。
    与全量取数共用锁, 避免并发。在线检查由调用方/端点做。"""
    if not _orch_lock.acquire(blocking=False):
        return {"started": False, "reason": "已有取数/拉单在进行中, 请稍候"}

    def _run() -> None:
        from app.database import SessionLocal
        d = SessionLocal()
        started_at = datetime.now()
        batch_id = new_order_batch_id(on=started_at.date())
        try:
            result = _orchestrate_locked(
                d,
                force=True,
                quiet=True,
                force_orders=True,
                orders_only=True,
                order_batch_id=batch_id,
                order_business_date=started_at.date(),
            )
            result["manual_recovery"] = True
            result["manual_pull"] = result.get("ingest") or {}
            _save_json(d, KEY_ORCH_STATE, {**result, "running": False})
            d.commit()
        except Exception as exc:  # noqa: BLE001
            _log.exception("手动拉单线程异常")
            d.rollback()
            _save_json(d, KEY_ORCH_STATE, {
                "running": False,
                "manual_recovery": True,
                "order_batch_id": batch_id,
                "order_business_date": started_at.date().isoformat(),
                "started_at": started_at.isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "tasks": [{
                    "task": "taobao_orders",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }],
            })
            d.commit()
        finally:
            try:
                web_agent_service.request_stop(
                    d, reason="manual_order_pull_finished"
                )
            except Exception:  # noqa: BLE001
                _log.warning("手动拉单结束后请求关闭Web-Agent失败", exc_info=True)
            d.close()
            _orch_lock.release()

    threading.Thread(target=_run, name="web-agent-pull-orders", daemon=True).start()
    return {"started": True}
