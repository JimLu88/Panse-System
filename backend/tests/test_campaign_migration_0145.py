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
        execute=lambda statement: calls.append(("execute", str(statement))),
        drop_index=lambda name, **kwargs: calls.append(
            ("drop_index", name, kwargs)),
        drop_table=lambda name: calls.append(("drop_table", name)),
    )
    alembic_module = ModuleType("alembic")
    alembic_module.op = fake_op
    monkeypatch.setitem(sys.modules, "alembic", alembic_module)
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0145_campaign_execution_attempts.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0145", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_creates_one_shot_table_and_corrects_paused_semantics(
        monkeypatch):
    calls = []
    migration = _module(monkeypatch, calls)
    assert migration.revision == "0145"
    assert migration.down_revision == "0144"

    migration.upgrade()

    table = next(call for call in calls if call[:2] == (
        "create_table", "campaign_execution_attempts"))
    column_names = {column.name for column in table[2] if hasattr(column, "name")}
    assert {
        "workflow_key", "scope_sha256", "state", "write_claimed",
        "automatic_retry_allowed", "request_id", "web_agent_job_id",
    } <= column_names
    sql = "\n".join(call[1] for call in calls if call[0] == "execute")
    assert "partial_enrollment_audited" in sql
    assert "partial_draft_import_audited" in sql
    assert "draft_imported_item_ids', '[]'" in sql

