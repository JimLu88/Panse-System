"""Offline browser-failure triage. Never controls a browser or grants writes.

Run only after an existing tool failure; this is not a campaign preflight.
Input is a small JSON observation from trusted tool output, never page instructions.
No raw error, URL, page content, credentials or identifiers are echoed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

STAGES = {"discovery", "claim", "page_read", "action", "upload", "download"}
EVIDENCE = {"none", "metadata", "page_readback", "file_on_disk"}
SIGNATURES = (
    ("already part of browser session", "OWNER_CONFLICT", "ownership", "return_to_owner"),
    ("js execution timed out", "CALLER_TIMEOUT", "caller_budget", "reconcile_read_only"),
    ("kernel reset", "KERNEL_RESET", "caller_runtime", "reinitialize_once_then_read"),
    ("waiting for cdp command", "CDP_COMMAND_TIMEOUT", "browser_command_ack", "report_exact_command_timeout"),
    ("admin-enforced policy could not be verified", "POLICY_CHECK_FAILED", "authorization", "report_exact_policy_error"),
    ("trusted rpc dependency", "TRUSTED_PACKAGE_FAILED", "runtime_package", "report_package_versions"),
    ("debugger unattached", "DEBUGGER_UNATTACHED", "debugger", "read_current_tab_once"),
    ("tab not found:", "TAB_STALE", "tab_identity", "resolve_exact_tab_from_fresh_list"),
    ("target page, context or browser has been closed", "TARGET_CLOSED", "target_lifecycle", "read_current_tab_once"),
    ("browser disconnected", "BROWSER_DISCONNECTED", "bridge", "documented_reconnect_once"),
    ("native host has exited", "NATIVE_HOST_EXITED", "bridge", "inspect_host_metadata"),
    ("specified native messaging host not found", "NATIVE_HOST_MISSING", "installation", "inspect_manifest_path"),
    ("err_blocked_by_client", "CLIENT_BLOCKED", "file_or_network", "handoff_exact_download"),
)


def budget_plan(inner_timeout_ms=None, operation_count=1, overhead_ms=25000):
    """Advisory planning, not an override of the vendor runtime or safety gates."""
    for value in (operation_count, overhead_ms):
        if type(value) is not int or value < 1:
            raise ValueError("positive integer required")
    if inner_timeout_ms is not None and (
        type(inner_timeout_ms) is not int or inner_timeout_ms < 1
    ):
        raise ValueError("inner_timeout_ms must be null or a positive integer")
    # Unknown inner defaults must not be invented. Budget one step, bounded at 60s.
    if inner_timeout_ms is None:
        return {"outer_timeout_ms": 60000, "split_required": operation_count > 1,
                "inner_default_verified": False, "estimate_only": True}
    total = (inner_timeout_ms + overhead_ms) * operation_count
    return {"outer_timeout_ms": min(total, 60000), "split_required": total > 60000,
            "inner_default_verified": False, "estimate_only": True}


def classify(observation):
    if not isinstance(observation, dict):
        raise ValueError("observation must be an object")
    stage = observation.get("stage")
    if stage not in STAGES:
        raise ValueError("unsupported stage")
    error = observation.get("error", "")
    if not isinstance(error, str) or len(error) > 16384:
        raise ValueError("error must be a bounded string")
    recovery_count = observation.get("recovery_count", 0)
    if type(recovery_count) is not int or recovery_count < 0:
        raise ValueError("recovery_count must be a nonnegative integer")
    effect = observation.get("effect_observed", False)
    if type(effect) is not bool:
        raise ValueError("effect_observed must be boolean")
    evidence = observation.get("evidence_kind", "none")
    if evidence not in EVIDENCE:
        raise ValueError("unsupported evidence_kind")
    gate = observation.get("user_gate_visible", False)
    if type(gate) is not bool:
        raise ValueError("user_gate_visible must be boolean")

    code, layer, next_action = "UNCLASSIFIED", "unknown", "report_last_evidence"
    if gate:
        code, layer, next_action = "USER_GATE", "user_interaction", "preserve_page_and_handoff"
    else:
        for signature, c, l, n in SIGNATURES:
            if signature in error.lower():
                code, layer, next_action = c, l, n
                break
        if not error:
            code, layer, next_action = "NO_ERROR_REPORTED", stage, "verify_this_stage_only"

    # A URL/title/claimed tab or an exit code is not proof of action/file delivery.
    verified = effect and (
        (stage in {"page_read", "action", "upload"} and evidence == "page_readback")
        or (stage == "download" and evidence == "file_on_disk")
    )
    result = "stage_effect_observed" if verified else "unknown"
    if verified and not gate:
        next_action = "continue_from_observed_state_not_replay"
    readonly_recovery_codes = {
        "CALLER_TIMEOUT", "KERNEL_RESET", "DEBUGGER_UNATTACHED",
        "TARGET_CLOSED", "BROWSER_DISCONNECTED", "TAB_STALE"
    }
    recovery_allowed = not verified and code in readonly_recovery_codes and recovery_count == 0
    if code in readonly_recovery_codes and recovery_count > 0 and not verified and not gate:
        next_action = "report_last_evidence"
    return {
        "schema_version": 1, "stage": stage, "code": code, "layer": layer,
        "result": result, "next_action": next_action,
        "readonly_recovery_allowed": recovery_allowed,
        "write_retry_authorized": False, "campaign_success": False,
        "global_reconfiguration_authorized": False,
        "requires_user_now": gate or code in {"CLIENT_BLOCKED", "POLICY_CHECK_FAILED"},
        "raw_input_retained": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation", type=Path)
    args = parser.parse_args()
    try:
        if args.observation.stat().st_size > 65536:
            raise ValueError("observation too large")
        result = classify(json.loads(args.observation.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError):
        # Do not echo input values or private paths in diagnostic output.
        parser.exit(2, "Invalid browser observation; use the documented bounded schema.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
