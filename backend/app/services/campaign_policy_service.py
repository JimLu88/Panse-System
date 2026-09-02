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
    submission_fields = policy.get("submission_fields") or {}
    post_submit = policy.get("post_submit") or {}
    if execution.get("signup_executor") != "campaign_automation_program_only":
        raise RuntimeError("活动报名规则未锁定为程序自动执行，已停止")
    if (execution.get("ai_may_submit") is not False
            or execution.get("ai_may_adjust_price") is not False
            or execution.get("ai_may_retry_after_failure") is not False
            or execution.get("manual_signup_api_enabled") is not False):
        raise RuntimeError("活动报名规则未明确禁止 AI/页面提交、改价或重试，已停止")
    if (execution.get("platform_write_probe_enabled") is not False
            or execution.get("maximum_platform_signup_submissions_per_run") != 1):
        raise RuntimeError("活动报名规则未锁定为只允许一次平台报名写入，已停止")
    bounded = execution.get("bounded_plan7_remaining_manifest_batches") or {}
    if bounded != {
        "enabled": True,
        "workflow_key": "campaign:super-reduce:2026-09-01",
        "reviewed_full_scope_sha256": (
            "d2ee3fd43a5c80d31799fc17b1b9f57c90db9f7ec7c78a0c88a501a7b51db2b8"
        ),
        "authorized_item_scope_sha256": (
            "1f66d114e711b0fb3448a8a1503120bb5edd35a2d6416105f66545392f15bc86"
        ),
        "preexisting_scope_partition": (
            "fresh_official_export_per_sku_exact_match_only; "
            "qualified_readonly_closeout; price_conflict_whole_item_hard_stop; "
            "wholly_missing_only_may_upload"
        ),
        "max_items_per_batch": 50,
        "max_rows_per_batch": 500,
        "split_only_when_limit_requires_it": True,
    }:
        raise RuntimeError("活动报名规则未锁定计划7剩余清单的精确批次边界，已停止")
    if (execution.get("each_signup_batch_requires_terminal_and_exact_per_sku_readback")
            is not True
            or execution.get("claimed_failed_or_unknown_batch_may_auto_retry")
            is not False):
        raise RuntimeError("活动报名规则未锁定逐批终态回查或失败禁止重试，已停止")
    if execution.get("on_failure") != (
            "platform_terminal_no_sales_is_recorded_and_quietly_excluded_"
            "from_later_campaigns_all_other_failures_stop_and_alarm"):
        raise RuntimeError("活动报名失败处理未锁定为无动销静默隔离、其余停止告警，已停止")
    if (execution.get("automatic_campaign_withdrawal_enabled") is not False
            or execution.get("withdrawal_requires_current_explicit_item_list_authorization") is not True):
        raise RuntimeError("活动报名规则未禁止自动撤销或未要求精确清单授权，已停止")
    if (pricing.get("real_sku_signup_price")
            != "erp_daily_price_unless_audited_combined_conflict_adjustment_lte_2_yuan"):
        raise RuntimeError("活动报名规则未锁定真实SKU默认日常价及合计2元内审计修正，已停止")
    if (pricing.get("real_sku_signup_price_may_be_lowered_to_pass_platform")
            != "up_to_2_yuan_per_sku_with_audit"):
        raise RuntimeError("活动报名规则未锁定普通SKU最多2元自动调整，已停止")
    if gates.get("single_item_discount_participates_in_qualification") is not True:
        raise RuntimeError("活动报名资格规则错误：同期单品立减必须计入资格校验")
    if (gates.get("any_sku_conflict_action")
            != "auto_adjust_combined_lte_2_yuan_else_use_clean_sku_slot_or_hold_whole_item"):
        raise RuntimeError("活动报名规则未锁定2元内自动修正及超额转备用槽，已停止")
    if gates.get("missing_or_stale_floor_evidence_action") != "block_before_upload_and_report":
        raise RuntimeError("活动报名规则未锁定价格线证据缺失/过期即上传前阻塞，已停止")
    if (gates.get("pre_submit_mode") != "local_read_only_evidence_preflight"
            or scope.get("exclude_no_sales_items_from_campaign_signup") is not True
            or scope.get("registered_no_sales_is_advisory_only") is not False
            or scope.get("every_listed_item_is_requalified_by_platform_for_each_campaign") is not False
            or scope.get("qualification_before_discount_and_final_signup") is not False
            or scope.get("platform_qualification_source")
            != "the_single_final_signup_terminal_record"):
        raise RuntimeError("活动报名规则未锁定只读预检和单次正式平台资格结果，已停止")
    if (scope.get("whole_item_link_exclusion")
            != "explicit_item_marker_or_all_mapped_skus_authoritatively_marked_custom_placeholder_only; never_keyword_inference"
            or scope.get("sku_rotation_enabled") != "controlled_new_slot_pool_only"
            or scope.get("legacy_reassign_existing_physical_sku_ids") is not False
            or scope.get("sku_slot_switch_max_per_logical_sku_per_campaign") != 1
            or scope.get(
                "cooling_to_clean_requires_fresh_exact_platform_history_clear_evidence"
            ) is not True):
        raise RuntimeError("活动报名规则未锁定受控新备用槽或仍允许旧SKU身份平移，已停止")
    if scope.get("no_sales_only_failure_action") != (
            "record_terminal_fact_and_quietly_exclude_from_later_campaigns_"
            "without_signup_discount_retry_or_unresolved"):
        raise RuntimeError("活动报名规则未锁定平台终态无动销静默排除，已停止")
    if pricing.get("custom_placeholder_sku_handling") != (
            "erp_is_custom_placeholder_is_authoritative_and_included; "
            "suffix_only_custom_codes_still_require_exact_taobao_sku_id_allowlist; "
            "never_name_keyword_inference"):
        raise RuntimeError("活动报名规则未锁定权威定制SKU随整品报名，已停止")
    if pricing.get("authoritative_placeholder_safe_lowering_enabled") is not True:
        raise RuntimeError("活动报名规则未允许权威定制SKU使用更低安全报名价，已停止")
    if pricing.get("placeholder_missing_floor_with_known_live_price_enabled") is not True:
        raise RuntimeError("活动报名规则未允许已有平台现价的权威定制SKU使用保守价，已停止")
    if pricing.get("placeholder_safe_cap_without_live_price_enabled") is not True:
        raise RuntimeError("活动报名规则未允许权威定制SKU缺平台现价时使用安全上限，已停止")
    if pricing.get("placeholder_safe_cap_authorization") != (
            "authoritative_is_custom_placeholder_may_use_coupon_floor_safe_cap_or_"
            "conservative_daily_price_fallback_without_current_live_price; "
            "known_live_price_uses_the_lower_value; "
            "never_changes_daily_price"):
        raise RuntimeError("活动报名规则未锁定定制SKU只改活动报名价，已停止")
    if (scope.get("custom_placeholder_safe_cap_without_live_price")
            != "authoritative_placeholder_uses_coupon_floor_safe_cap_or_conservative_daily_price_fallback; non_authoritative_rows_still_block"):
        raise RuntimeError("活动报名规则未锁定权威定制SKU缺平台现价时使用安全上限，已停止")
    if scope.get("accepted_item_action") != "single_item_discount_first_then_final_campaign_signup":
        raise RuntimeError("活动报名规则未锁定先单品立减、后正式活动报名，已停止")
    if scope.get("existing_single_discount_edit_mode") != "one_item_per_job_with_sku_readback":
        raise RuntimeError("活动报名规则未锁定既有单品立减逐商品修改并逐 SKU 回读，已停止")
    if scope.get("existing_single_discount_activity_binding") != "per_item_id_to_activity_id":
        raise RuntimeError("活动报名规则未锁定单品立减活动ID按商品绑定，已停止")
    if "withdrawal_requires_current_explicit_item_list_authorization" not in str(
            (policy.get("post_submit") or {}).get(
                "active_activity_records_outside_current_scope", "")):
        raise RuntimeError("活动报名规则未锁定保留现场且撤销须当前精确授权，已停止")
    if scope.get("qualification_hard_failure_action") != "isolate_whole_item_report_and_continue_safe_items":
        raise RuntimeError("活动报名规则未锁定资格硬失败整品隔离并继续安全商品，已停止")
    if (submission_fields.get("shipping_time_mode") != "relative_days_text"
            or submission_fields.get("shipping_time_required_on_every_signup_row") is not True):
        raise RuntimeError("活动报名规则未锁定逐行相对发货时效，已停止")
    try:
        shipping_days = int(submission_fields.get("default_shipping_days"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("活动报名规则默认发货天数非法，已停止") from exc
    if not 1 <= shipping_days <= 365:
        raise RuntimeError("活动报名规则默认发货天数必须在1到365天，已停止")
    receipt_fields = post_submit.get("receipt_fields") or []
    if (post_submit.get("receipt_required") is not True
            or not isinstance(receipt_fields, list)
            or not set({"job_id", "terminal_counts", "fresh_export_sha256",
                        "per_sku_verification"}).issubset(set(receipt_fields))):
        raise RuntimeError("活动报名规则未锁定终态与新导出结构化回执，已停止")
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
        "scope_and_idempotency": policy.get("scope_and_idempotency"),
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


def default_shipping_days() -> int:
    """Return the policy-locked relative shipping time for every signup row."""
    policy = require_policy()
    return int((policy.get("submission_fields") or {})["default_shipping_days"])
