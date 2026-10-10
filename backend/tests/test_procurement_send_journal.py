import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.procurement_agent.send_journal import SendBlocked, SendJournal


def action(**kwargs):
    return {"task_id": 1, "inquiry_id": 2, "action_key": "initial:0", **kwargs}


def journal(tmp_path):
    return SendJournal(tmp_path / "outbox.sqlite3", server_scope="https://erp.invalid")


def test_committed_before_any_external_effect_and_survives_reopen(tmp_path):
    first = journal(tmp_path)
    first.begin(action())
    second = journal(tmp_path)
    assert second.state(action()) == "executing"
    with pytest.raises(SendBlocked):
        second.begin(action(lease_token="different", suggested_message="edited"))


def test_atomic_attempt_is_reserved_only_once(tmp_path):
    first = journal(tmp_path)
    def reserve(_):
        try:
            first.begin(action())
            return True
        except SendBlocked:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(8))) == 1


def test_confirmed_attempt_not_replayed_but_next_round_allowed(tmp_path):
    store = journal(tmp_path)
    store.begin(action())
    store.mark(action(), "sent_observed")
    store.mark(action(), "confirmed")
    with pytest.raises(SendBlocked):
        store.begin(action())
    store.begin(action(action_key="followup:1"))
    assert store.state(action(action_key="followup:1")) == "executing"


@pytest.mark.parametrize("state", ["executing", "unknown", "manual", "sent_observed"])
def test_unresolved_attempt_blocks_other_merchants_and_server_aliases(tmp_path, state):
    store = journal(tmp_path)
    store.begin(action())
    if state != "executing":
        store.mark(action(), state)
    other = SendJournal(store.path, server_scope="https://different-erp.invalid")
    with pytest.raises(SendBlocked):
        other.begin(action(inquiry_id=8))


def test_local_execution_lock_is_exclusive_and_released(tmp_path):
    first, second = journal(tmp_path), journal(tmp_path)
    with first.execution_lock():
        with pytest.raises(SendBlocked):
            with second.execution_lock():
                pytest.fail("second worker acquired desktop lock")
    with second.execution_lock():
        pass


@pytest.mark.parametrize("bad", [
    {"task_id": None}, {"inquiry_id": 0}, {"task_id": True},
    {"action_key": None}, {"action_key": ""}, {"action_key": "followup:0"},
])
def test_stable_identity_required(tmp_path, bad):
    with pytest.raises(SendBlocked):
        journal(tmp_path).begin(action(**bad))


def test_no_plaintext_or_credentials_in_journal(tmp_path):
    store = journal(tmp_path)
    store.begin(action(suggested_message="sensitive-message-xyz", lease_token="secret-lease-xyz"))
    raw = store.path.read_bytes()
    assert b"sensitive-message-xyz" not in raw
    assert b"secret-lease-xyz" not in raw
    assert b"https://erp.invalid" not in raw


def test_no_memory_or_relative_storage(tmp_path):
    with pytest.raises(ValueError):
        SendJournal(Path(":memory:"), server_scope="test")
    with pytest.raises(ValueError):
        SendJournal(tmp_path / "x.sqlite3", server_scope="")


def test_cannot_skip_delivery_observation(tmp_path):
    store = journal(tmp_path)
    store.begin(action())
    with pytest.raises(SendBlocked):
        store.mark(action(), "confirmed")
    assert store.state(action()) == "executing"
