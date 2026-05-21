"""看门狗扩展: 自救重启 / PID 文件 / 事件 / 重启 diff."""
from __future__ import annotations

import os
import signal
import time
from unittest.mock import patch

import pytest

from app.models.system_event import SystemEvent
from app.models.system_health import SystemHealthLog
from app.services import system_monitor


# ----------------------------- 事件 / Diff ----------------------- #


def test_log_event_writes_row(db_session):
    system_monitor.log_event(db_session, "process_started", actor="system",
                             detail="pid=1234")
    from sqlalchemy import select
    rows = db_session.execute(select(SystemEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "process_started"
    assert rows[0].actor == "system"


def test_snapshot_for_event_returns_dict(db_session):
    snap = system_monitor._snapshot_for_event(db_session)
    assert isinstance(snap, dict)
    assert "mem_used_pct" in snap
    assert "db_ok" in snap


def test_log_process_started_includes_snapshot(db_session):
    system_monitor.log_process_started(db_session)
    db_session.flush()
    from sqlalchemy import select
    e = db_session.execute(select(SystemEvent)).scalar_one()
    assert e.kind == "process_started"
    assert e.snapshot_json is not None
    assert "mem_used_pct" in e.snapshot_json


def test_recent_events_limit_and_order(db_session):
    for i in range(5):
        system_monitor.log_event(db_session, "process_started",
                                 detail=f"#{i}", actor="system")
    db_session.flush()
    rows = system_monitor.recent_events(db_session, limit=3)
    assert len(rows) == 3
    # 最新在前
    assert rows[0].detail == "#4"


# ----------------------------- PID 文件 ------------------------- #


@pytest.fixture
def pid_tmp(tmp_path, monkeypatch):
    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(system_monitor, "PID_FILE", str(pid_file))
    yield str(pid_file)


def test_claim_pid_file_writes_own_pid_when_empty(pid_tmp, db_session):
    killed = system_monitor.claim_pid_file(db_session)
    assert killed is None
    assert open(pid_tmp).read().strip() == str(os.getpid())


def test_claim_pid_file_overwrites_dead_pid(pid_tmp, db_session):
    """文件里写了一个死 PID → 覆盖, 不应触发 kill."""
    # 用 99999999 — 几乎肯定不存在
    open(pid_tmp, "w").write("99999999")
    with patch("app.services.system_monitor.os.kill") as mk:
        # _is_alive 内部也用 os.kill(pid, 0), 我们模拟 ProcessLookupError
        mk.side_effect = ProcessLookupError("no such process")
        killed = system_monitor.claim_pid_file(db_session)
    # 没杀任何人 (死的)
    assert killed is None
    assert open(pid_tmp).read().strip() == str(os.getpid())


def test_claim_pid_file_kills_live_orphan(pid_tmp, db_session):
    """文件里有一个真活着的 PID (非自己) → 应 SIGTERM, 等不退就 SIGKILL."""
    # 模拟一个永活进程
    fake_pid = 12345
    open(pid_tmp, "w").write(str(fake_pid))

    kill_calls = []
    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        # sig=0 (探活): 永远报"活着"
        # sig=SIGTERM: 假装收到但不死
        # sig=SIGKILL: 假装死了
        if sig == 0:
            # 在 SIGKILL 之后报死
            if any(s == signal.SIGKILL for _, s in kill_calls):
                raise ProcessLookupError("dead now")
            return None
        if sig == signal.SIGTERM:
            return None
        if sig == signal.SIGKILL:
            return None

    with patch("app.services.system_monitor.os.kill", side_effect=fake_kill), \
         patch("app.services.system_monitor.time.sleep"):  # 不真等 3 秒
        killed = system_monitor.claim_pid_file(db_session)

    assert killed == fake_pid
    # 应包含 SIGTERM 和 SIGKILL
    sigs_used = {sig for _, sig in kill_calls if sig in (signal.SIGTERM, signal.SIGKILL)}
    assert signal.SIGTERM in sigs_used
    assert signal.SIGKILL in sigs_used
    # 写了一条 orphan_killed 事件
    db_session.commit()
    from sqlalchemy import select
    e = db_session.execute(select(SystemEvent).where(
        SystemEvent.kind == "orphan_killed",
    )).scalar_one()
    assert str(fake_pid) in e.detail


def test_release_pid_file_only_removes_own(pid_tmp):
    open(pid_tmp, "w").write(str(os.getpid()))
    system_monitor.release_pid_file()
    assert not os.path.exists(pid_tmp)


def test_release_pid_file_keeps_others(pid_tmp):
    """如果 PID 文件被新进程覆盖了, 老进程退出时不应删除."""
    open(pid_tmp, "w").write("99999")
    system_monitor.release_pid_file()
    assert os.path.exists(pid_tmp)
    assert open(pid_tmp).read().strip() == "99999"


# ----------------------------- 自救重启 ------------------------- #


def test_should_auto_restart_no_data(db_session):
    should, reason = system_monitor._should_auto_restart(db_session)
    assert should is False


def test_should_auto_restart_triggers_on_3_consecutive_fail(db_session):
    for _ in range(3):
        db_session.add(SystemHealthLog(
            check_name="db_ping", status="fail", detail="x", duration_ms=10,
        ))
    db_session.flush()
    should, reason = system_monitor._should_auto_restart(db_session)
    assert should is True
    assert "db_ping" in reason


def test_should_auto_restart_does_not_trigger_with_2_fails(db_session):
    for _ in range(2):
        db_session.add(SystemHealthLog(
            check_name="db_ping", status="fail", detail="x", duration_ms=10,
        ))
    db_session.flush()
    should, _ = system_monitor._should_auto_restart(db_session)
    assert should is False


def test_should_auto_restart_does_not_trigger_when_ok_follows_fails(db_session):
    """两次 fail + 一次 ok → 不触发 (最近的不是 fail)."""
    db_session.add_all([
        SystemHealthLog(check_name="db_ping", status="fail", detail="x", duration_ms=10),
        SystemHealthLog(check_name="db_ping", status="fail", detail="x", duration_ms=10),
        SystemHealthLog(check_name="db_ping", status="ok", detail="x", duration_ms=10),
    ])
    db_session.flush()
    should, _ = system_monitor._should_auto_restart(db_session)
    assert should is False


def test_should_auto_restart_checks_memory_too(db_session):
    for _ in range(3):
        db_session.add(SystemHealthLog(
            check_name="memory", status="fail", detail="98%", duration_ms=1,
        ))
    db_session.flush()
    should, reason = system_monitor._should_auto_restart(db_session)
    assert should is True
    assert "memory" in reason


def test_should_auto_restart_ignores_non_critical(db_session):
    """ai_config 不在 critical 列表, 哪怕全 fail 也不触发."""
    for _ in range(5):
        db_session.add(SystemHealthLog(
            check_name="ai_config", status="fail", detail="x", duration_ms=1,
        ))
    db_session.flush()
    should, _ = system_monitor._should_auto_restart(db_session)
    assert should is False


def test_maybe_auto_restart_writes_events_and_triggers(db_session):
    # 准备触发条件
    for _ in range(3):
        db_session.add(SystemHealthLog(
            check_name="db_ping", status="fail", detail="connection lost",
            duration_ms=5000,
        ))
    db_session.flush()
    # 强制重置 cooldown 状态
    system_monitor._LAST_AUTO_RESTART_TS = 0.0

    with patch("app.services.system_monitor.request_restart") as mock_rr:
        reason = system_monitor.maybe_auto_restart(db_session)
    assert reason is not None
    assert "db_ping" in reason
    mock_rr.assert_called_once()

    from sqlalchemy import select
    events = db_session.execute(
        select(SystemEvent).order_by(SystemEvent.id)
    ).scalars().all()
    kinds = [e.kind for e in events]
    assert "watchdog_triggered" in kinds
    assert "restart_requested" in kinds
    assert any(e.actor == "watchdog" for e in events)


def test_maybe_auto_restart_respects_cooldown(db_session):
    """触发过一次后, 10 分钟内不再触发."""
    for _ in range(3):
        db_session.add(SystemHealthLog(
            check_name="db_ping", status="fail", detail="x", duration_ms=10,
        ))
    db_session.flush()

    system_monitor._LAST_AUTO_RESTART_TS = time.time()  # 假装刚触发过
    with patch("app.services.system_monitor.request_restart") as mock_rr:
        reason = system_monitor.maybe_auto_restart(db_session)
    assert reason is None
    mock_rr.assert_not_called()


def test_maybe_auto_restart_no_trigger_when_healthy(db_session):
    db_session.add_all([
        SystemHealthLog(check_name="db_ping", status="ok", detail="5ms", duration_ms=5),
    ])
    db_session.flush()
    system_monitor._LAST_AUTO_RESTART_TS = 0.0
    with patch("app.services.system_monitor.request_restart") as mock_rr:
        reason = system_monitor.maybe_auto_restart(db_session)
    assert reason is None
    mock_rr.assert_not_called()


def test_request_restart_with_db_writes_event(db_session):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        with patch("app.services.system_monitor.os.kill"):
            system_monitor.request_restart(
                db_session, actor="admin",
                detail="手动重启测试",
            )
        from sqlalchemy import select
        e = db_session.execute(
            select(SystemEvent).where(SystemEvent.kind == "restart_requested")
        ).scalar_one()
        assert e.actor == "admin"
        assert "手动重启测试" in (e.detail or "")
        assert e.snapshot_json is not None
    finally:
        loop.close()
        asyncio.set_event_loop(None)
