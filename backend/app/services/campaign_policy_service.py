"""Load and enforce the repository-root campaign signup policy.

The JSON file is deliberately outside application code so operators, tests and
future AI sessions all read the same contract.  Docker mounts the same file at
``/app/TAOBAO_CAMPAIGN_SIGNUP_POLICY.json``; a missing or malformed policy is a
hard stop, never a permissive fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


POLICY_FILENAME = "TAOBAO_CAMPAIGN_SIGNUP_POLICY.json"
REQUIRED_POLICY_ID = "taobao_campaign_signup_policy"


def _candidate_paths() -> list[Path]:
    explicit = (os.environ.get("CAMPAIGN_SIGNUP_POLICY_PATH") or "").strip()
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    # Source checkout: backend/app/services -> repository root.
    paths.append(Path(__file__).resolve().parents[3] / POLICY_FILENAME)
    # Docker compose mounts the repository-root file here.
    paths.append(Path("/app") / POLICY_FILENAME)
    return paths


def policy_path() -> Path:
    for path in _candidate_paths():
        if path.is_file():
            return path
    tried = ", ".join(str(path) for path in _candidate_paths())
    raise RuntimeError(f"活动报名规则文件缺失，已停止：{POLICY_FILENAME}；检查位置：{tried}")


def require_policy() -> dict[str, Any]:
    path = policy_path()
    try:
        raw = path.read_text(encoding="utf-8")
        policy = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - malformed policy must hard-stop
        raise RuntimeError(f"活动报名规则文件无法读取，已停止：{path}：{exc}") from exc
    if policy.get("policy_id") != REQUIRED_POLICY_ID:
        raise RuntimeError(
            f"活动报名规则 policy_id 不匹配，已停止：{policy.get('policy_id')!r}")
    execution = policy.get("execution") or {}
    pricing = policy.get("pricing") or {}
    gates = policy.get("qualification_gates") or {}
    scope = policy.get("scope_and_idempotency") or {}
    if execution.get("signup_executor") != "campaign_automation_program_only":
        raise RuntimeError("活动报名规则未锁定为程序自动执行，已停止")
    if (execution.get("ai_may_submit") is not False
            or execution.get("ai_may_adjust_price") is not False
            or execution.get("ai_may_retry_after_failure") is not False
            or execution.get("manual_signup_api_enabled") is not False):
        raise RuntimeError("活动报名规则未明确禁止 AI/页面提交、改价或重试，已停止")
    if execution.get("on_failure") != "stop_mark_alarmed_notify_user_and_wait_for_explicit_decision":
        raise RuntimeError("活动报名失败处理未锁定为停止、告警并等待用户决定，已停止")
    if pricing.get("real_sku_signup_price") != "erp_daily_price":
        raise RuntimeError("活动报名规则未锁定真实 SKU 报名价=ERP 日常价，已停止")
    if pricing.get("real_sku_signup_price_may_be_lowered_to_pass_platform") is not False:
        raise RuntimeError("活动报名规则未明确禁止降低真实 SKU 报名价，已停止")
    if gates.get("single_item_discount_participates_in_qualification") is not True:
        raise RuntimeError("活动报名资格规则错误：同期单品立减必须计入资格校验")
    if gates.get("any_sku_conflict_action") != "exclude_whole_item_and_report":
        raise RuntimeError("活动报名规则未锁定任一 SKU 冲突即整品排除并报告，已停止")
    if gates.get("missing_or_stale_floor_evidence_action") != "block_before_upload_and_report":
        raise RuntimeError("活动报名规则未锁定价格线证据缺失/过期即上传前阻塞，已停止")
    if (scope.get("exclude_no_sales_items_from_campaign_signup") is not False
            or scope.get("registered_no_sales_is_advisory_only") is not True
            or scope.get("every_listed_item_is_requalified_by_platform_for_each_campaign") is not True
            or scope.get("qualification_before_discount_and_final_signup") is not True):
        raise RuntimeError("活动报名规则未锁定每场对全部 ERP 在售商品重新执行平台资格检查，已停止")
    if scope.get("no_sales_only_failure_action") != "keep_out_of_campaign_and_use_single_item_discount":
        raise RuntimeError("活动报名规则未锁定无动销仅失败的单品立减兜底，已停止")
    if scope.get("accepted_item_action") != "single_item_discount_first_then_final_campaign_signup":
        raise RuntimeError("活动报名规则未锁定先单品立减、后正式活动报名，已停止")
    if scope.get("existing_single_discount_edit_mode") != "one_item_per_job_with_sku_readback":
        raise RuntimeError("活动报名规则未锁定既有单品立减逐商品修改并逐 SKU 回读，已停止")
    if scope.get("existing_single_discount_activity_binding") != "per_item_id_to_activity_id":
        raise RuntimeError("活动报名规则未锁定单品立减活动ID按商品绑定，已停止")
    if scope.get("qualification_hard_failure_action") != "isolate_whole_item_report_and_continue_safe_items":
        raise RuntimeError("活动报名规则未锁定资格硬失败整品隔离并继续安全商品，已停止")
    lines = policy.get("explanation_lines")
    if not isinstance(lines, list) or len(lines) < 5 or not all(isinstance(x, str) and x for x in lines):
        raise RuntimeError("活动报名规则 explanation_lines 不完整，已停止")
    policy["_path"] = str(path)
    policy["_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return policy


def public_policy() -> dict[str, Any]:
    policy = require_policy()
    return {
        "policy_id": policy["policy_id"],
        "version": policy.get("version"),
        "title": policy.get("title"),
        "explanation_lines": policy["explanation_lines"],
        "execution": policy.get("execution"),
        "pricing": policy.get("pricing"),
        "qualification_gates": policy.get("qualification_gates"),
        "sha256": policy["_sha256"],
    }


def floor_evidence_max_age_hours() -> int:
    policy = require_policy()
    value = (policy.get("qualification_gates") or {}).get(
        "floor_evidence_max_age_hours", 24)
    try:
        hours = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("活动报名规则 floor_evidence_max_age_hours 非法，已停止") from exc
    if hours <= 0:
        raise RuntimeError("活动报名规则 floor_evidence_max_age_hours 必须大于 0，已停止")
    return hours
