"""系统监控 + 看门狗: 状态 / 健康检查 / 重启信号."""
from __future__ import annotations

import signal
from unittest.mock import patch

import pytest

from app.models.system_health import SystemHealthLog
from app.services import system_monitor


def test_get_status_returns_snapshot(db_session):
    s = system_monitor.get_status(db_session)
    assert s.uptime_sec >= 0
    assert s.python_version  # 比如 "3.11.4"
    assert s.db_ok is True   # SQLite in-memory 自然 ok
    assert s.disk_total_gb > 0
    assert len(s.recent_checks) >= 4  # db + disk + memory + migrations + ai
    # 检查名应包含核心几个
    names = {c.name for c in s.recent_checks}
    assert {"db_ping", "disk", "memory", "migrations", "ai_config"} <= names


def test_run_checks_writes_logs(db_session):
    results = system_monitor.run_checks(db_session, persist=True)
    db_session.flush()
    from sqlalchemy import select
    logs = db_session.execute(select(SystemHealthLog)).scalars().all()
    assert len(logs) == len(results)
    log_names = {l.check_name for l in logs}
    assert "db_ping" in log_names


def test_run_checks_no_persist(db_session):
    system_monitor.run_checks(db_session, persist=False)
    from sqlalchemy import select
    logs = db_session.execute(select(SystemHealthLog)).scalars().all()
    assert logs == []


def test_db_check_ok_in_memory_sqlite(db_session):
    results = system_monitor.run_checks(db_session, persist=False)
    db = next(c for c in results if c.name == "db_ping")
    assert db.status == "ok"
    assert db.duration_ms >= 0


def test_disk_check_returns_a_status():
    from app.services.system_monitor import _check_disk
    r = _check_disk()
    assert r.name == "disk"
    assert r.status in ("ok", "warn", "fail")
    assert "used=" in r.detail


def test_memory_check_runs():
    from app.services.system_monitor import _check_memory
    r = _check_memory()
    assert r.name == "memory"
    assert r.status in ("ok", "warn", "fail")


def test_ai_config_warn_when_nothing_set(db_session, monkeypatch):
    """没配 diagnose/ocr key 时, ai_config 应 warn 但不 fail."""
    from app.services.system_monitor import _check_ai_config
    monkeypatch.delenv("AI_DIAGNOSE_API_KEY", raising=False)
    monkeypatch.delenv("AI_OCR_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("S", (), {"anthropic_api_key": "", "ai_model": "",
                                "jwt_secret": "x"})(),
    )
    r = _check_ai_config(db_session)
    assert r.status == "warn"


def test_ai_config_ok_when_key_set(db_session):
    from app.services import settings_service
    settings_service.set_value(db_session, "ai_diagnose_api_key", "sk-test-key-12345678")
    from app.services.system_monitor import _check_ai_config
    r = _check_ai_config(db_session)
    assert r.status == "ok"
    assert "诊断 ✓" in r.detail


def test_recent_logs_filter_by_check_name(db_session):
    db_session.add_all([
        SystemHealthLog(check_name="db_ping", status="ok", detail="1ms", duration_ms=1),
        SystemHealthLog(check_name="db_ping", status="ok", detail="2ms", duration_ms=2),
        SystemHealthLog(check_name="disk", status="ok", detail="50%", duration_ms=5),
    ])
    db_session.flush()
    all_logs = system_monitor.recent_logs(db_session, limit=10)
    assert len(all_logs) == 3
    db_only = system_monitor.recent_logs(db_session, limit=10, check_name="db_ping")
    assert len(db_only) == 2
    assert all(l.check_name == "db_ping" for l in db_only)


def test_request_restart_sends_sigterm():
    """request_restart 不实际杀进程, 但应 schedule 一个 SIGTERM."""
    calls = []
    with patch("app.services.system_monitor.os.kill",
               side_effect=lambda pid, sig: calls.append((pid, sig))):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            system_monitor.request_restart()
            # call_later 调度到 loop, run 一下让它执行
            loop.run_until_complete(asyncio.sleep(0.8))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    assert len(calls) == 1
    assert calls[0][1] == signal.SIGTERM


def test_request_restart_no_loop_no_crash():
    """没有 event loop 时不应崩."""
    import asyncio
    asyncio.set_event_loop(None)
    # 实际 SIGTERM 不应发出 (没 loop, call_later 失败); 但函数本身不能崩
    with patch("app.services.system_monitor.os.kill") as mk:
        try:
            system_monitor.request_restart()
        except RuntimeError:
            pass  # 允许 RuntimeError ("no current event loop")
        mk.assert_not_called()


def test_start_and_stop_background():
    """background_loop 可启动 + 可停止, 不抛."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        system_monitor.start_background(interval_sec=999)  # 不会真跑
        # 重复调安全
        system_monitor.start_background(interval_sec=999)
        system_monitor.stop_background()
        # 停止后再 start 又能起
        system_monitor.start_background(interval_sec=999)
        system_monitor.stop_background()
    finally:
        loop.close()
        asyncio.set_event_loop(None)
