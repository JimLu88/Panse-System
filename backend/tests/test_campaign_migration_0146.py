from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def _module(monkeypatch, calls):
    fake_op = SimpleNamespace(
        create_table=lambda name, *args, **kwargs: calls.append(
            ("create_table", name, args, kwargs)),
        create_index=lambda name, table, columns, **kwargs: calls.append(
            ("create_index", name, table, tuple(columns), kwargs)),
        drop_table=lambda name: calls.append(("drop_table", name)),
    )
    alembic_module = ModuleType("alembic")
    alembic_module.op = fake_op
    monkeypatch.setitem(sys.modules, "alembic", alembic_module)
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0146_campaign_sku_slot_pool.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0146", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_creates_slot_pool_and_one_shot_attempt(monkeypatch):
    calls = []
    migration = _module(monkeypatch, calls)
    assert migration.revision == "0146"
    assert migration.down_revision == "0145"

    migration.upgrade()

    tables = {call[1]: call for call in calls if call[0] == "create_table"}
    assert {"campaign_sku_slots", "campaign_sku_slot_attempts"} <= set(tables)
    slot_columns = {
        column.name for column in tables["campaign_sku_slots"][2]
        if hasattr(column, "name")
    }
    assert {
        "sku_code", "taobao_item_id", "taobao_sku_id", "physical_slot_code",
        "state", "attribute_sha256", "baseline_daily_price",
        "custom_min_final_price", "cooling_until", "last_workflow_key",
    } <= slot_columns
    attempt_columns = {
        column.name for column in tables["campaign_sku_slot_attempts"][2]
        if hasattr(column, "name")
    }
    assert {
        "workflow_key", "taobao_item_id", "sku_code", "source_slot_id",
        "target_slot_id", "manifest_sha256", "state", "write_claimed",
        "request_id", "result_summary",
    } <= attempt_columns
