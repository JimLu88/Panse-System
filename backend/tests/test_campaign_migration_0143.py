from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import sqlalchemy as sa


def _migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0143_restore_plan7_official_scope.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0143", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_restores_only_plan7_scope_and_is_idempotent(monkeypatch):
    alembic_module = ModuleType("alembic")
    alembic_module.op = SimpleNamespace(get_bind=lambda: None)
    monkeypatch.setitem(sys.modules, "alembic", alembic_module)
    migration = _migration_module()
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    metadata = sa.MetaData()
    plans = sa.Table(
        "campaign_plans",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_key", sa.String(128)),
        sa.Column("remark", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(plans.insert(), [
            {
                "id": 7,
                "workflow_key": "campaign:super-reduce:2026-09-01",
                "remark": (
                    "platform_qualified_items=797294092429; "
                    "official_active_items=797294092429"
                ),
            },
            {
                "id": 8,
                "workflow_key": "campaign:super88:49462:49469",
                "remark": "official_active_items=793202812082",
            },
        ])
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        migration.upgrade()
        migration.upgrade()

        rows = {
            row.workflow_key: row.remark
            for row in connection.execute(
                sa.select(plans.c.workflow_key, plans.c.remark)
            )
        }

    plan7 = rows["campaign:super-reduce:2026-09-01"]
    assert "official_all_store=true" in plan7
    assert "official_exempt_items=805268708396" in plan7
    assert "official_active_items=" not in plan7
    assert plan7.count("official_all_store=") == 1
    assert plan7.count("official_exempt_items=") == 1
    assert rows["campaign:super88:49462:49469"] == (
        "official_active_items=793202812082"
    )
