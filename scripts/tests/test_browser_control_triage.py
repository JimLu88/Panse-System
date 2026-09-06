import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_control_triage import budget_plan, classify


def obs(**values):
    return {"stage": "action", **values}


@pytest.mark.parametrize("error,code", [
    ("Tab 1 is already part of browser session abc", "OWNER_CONFLICT"),
    ("js execution timed out; kernel reset, rerun your request", "CALLER_TIMEOUT"),
    ("kernel reset", "KERNEL_RESET"),
    ("Debugger unattached", "DEBUGGER_UNATTACHED"),
    ("Tab not found: 123. Existing tabs: 456", "TAB_STALE"),
    ("Target page, context or browser has been closed", "TARGET_CLOSED"),
    ("Browser disconnected", "BROWSER_DISCONNECTED"),
    ("Native host has exited", "NATIVE_HOST_EXITED"),
    ("Specified native messaging host not found", "NATIVE_HOST_MISSING"),
    ("admin-enforced policy could not be verified", "POLICY_CHECK_FAILED"),
    ("Trusted RPC dependency must resolve within a configured trusted code path", "TRUSTED_PACKAGE_FAILED"),
    ("net::ERR_BLOCKED_BY_CLIENT", "CLIENT_BLOCKED"),
])
def test_classifies_without_granting_write(error, code):
    result = classify(obs(error=error))
    assert result["code"] == code
    assert result["result"] == "unknown"
    assert result["write_retry_authorized"] is False
    assert result["campaign_success"] is False
    assert result["global_reconfiguration_authorized"] is False


def test_outer_timeout_not_invented_attach_failure():
    r = classify(obs(error="js execution timed out; kernel reset"))
    assert r["layer"] == "caller_budget"
    assert r["readonly_recovery_allowed"]


def test_timeout_but_actual_effect_observed_do_not_replay():
    r = classify(obs(error="js execution timed out", effect_observed=True,
                     evidence_kind="page_readback"))
    assert r["result"] == "stage_effect_observed"
    assert r["next_action"] == "continue_from_observed_state_not_replay"
    assert not r["readonly_recovery_allowed"]


@pytest.mark.parametrize("stage", ["discovery", "claim", "action", "upload", "download"])
def test_metadata_never_proves_business_or_action(stage):
    r = classify(obs(stage=stage, effect_observed=True, evidence_kind="metadata"))
    assert r["result"] == "unknown"
    assert not r["campaign_success"]


def test_uploaded_filename_is_not_campaign_success():
    r = classify(obs(stage="upload", effect_observed=True, evidence_kind="page_readback"))
    assert r["result"] == "stage_effect_observed"
    assert not r["campaign_success"]


def test_download_requires_file_evidence():
    r = classify(obs(stage="download", effect_observed=True, evidence_kind="page_readback"))
    assert r["result"] == "unknown"
    r = classify(obs(stage="download", effect_observed=True, evidence_kind="file_on_disk"))
    assert r["result"] == "stage_effect_observed"


def test_recovery_bounded_and_no_owner_takeover():
    r = classify(obs(error="js execution timed out", recovery_count=1))
    assert not r["readonly_recovery_allowed"]
    assert r["next_action"] == "report_last_evidence"
    assert not classify(obs(error="already part of browser session"))["readonly_recovery_allowed"]


def test_visible_gate_not_overridden_by_prior_success():
    r = classify(obs(error="Debugger unattached", user_gate_visible=True,
                     effect_observed=True, evidence_kind="page_readback"))
    assert r["code"] == "USER_GATE"
    assert r["requires_user_now"]
    assert r["next_action"] == "preserve_page_and_handoff"


def test_no_private_error_echo():
    r = classify(obs(error="unknown https://private.example/?token=SECRET customer Alice",
                     private_payload="SECRET"))
    assert "SECRET" not in json.dumps(r)
    assert "private.example" not in json.dumps(r)


@pytest.mark.parametrize("value", [[], {"stage":"unknown"}, obs(error=None),
    obs(effect_observed="true"), obs(recovery_count=True), obs(recovery_count=-1),
    obs(evidence_kind="exit_zero"), obs(user_gate_visible="yes"), obs(error="x"*16385)])
def test_bad_input(value):
    with pytest.raises(ValueError):
        classify(value)


def test_unknown_budget_does_not_invent_inner_default():
    assert budget_plan() == {"outer_timeout_ms":60000,"split_required":False,
                             "inner_default_verified":False,"estimate_only":True}
    assert budget_plan(operation_count=2)["split_required"]


def test_multiple_slow_steps_must_split():
    assert budget_plan(30000, operation_count=2)["split_required"]
    assert budget_plan(30000)["outer_timeout_ms"] == 55000
    assert budget_plan(50000)["split_required"]


@pytest.mark.parametrize("kwargs", [
    {"inner_timeout_ms":0}, {"inner_timeout_ms":True},
    {"operation_count":0}, {"overhead_ms":-1}
])
def test_bad_budget(kwargs):
    with pytest.raises(ValueError):
        budget_plan(**kwargs)
