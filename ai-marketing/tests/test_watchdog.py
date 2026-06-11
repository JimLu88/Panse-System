"""看门狗：体检 / 连续失败自救 / 冷却防风暴。"""
import pathlib

import pytest

from app.services import watchdog


@pytest.fixture(autouse=True)
def _reset_state():
    """每个用例重置看门狗内存态与冷却文件。"""
    watchdog.STATE.update({"consecutive_failures": 0, "last_check_at": None,
                           "last_ok_at": None, "restarts_blocked_by_cooldown": 0})
    cooldown = pathlib.Path("./watchdog.cooldown")
    if cooldown.exists():
        cooldown.unlink()
    yield
    if cooldown.exists():
        cooldown.unlink()


def test_healthy_check_resets_counter():
    result = watchdog.check_once()
    assert result["ok"] is True
    assert result["consecutive_failures"] == 0
    assert watchdog.STATE["last_ok_at"] is not None


def test_consecutive_failures_accumulate(monkeypatch):
    monkeypatch.setitem(watchdog._CHECKS, "db", lambda: (False, "模拟DB挂了"))
    for expect in (1, 2, 3):
        r = watchdog.check_once()
        assert r["ok"] is False
        assert r["consecutive_failures"] == expect
    # 恢复后归零
    monkeypatch.setitem(watchdog._CHECKS, "db", lambda: (True, "ok"))
    assert watchdog.check_once()["consecutive_failures"] == 0


def test_self_restart_triggers_kill_and_cooldown():
    killed = []
    # 第一次：真触发（注入 kill 替身，不真发 SIGTERM）
    assert watchdog.self_restart(kill=lambda: killed.append(1)) is True
    assert killed == [1]
    # 第二次：冷却期内被拦，不再 kill
    assert watchdog.self_restart(kill=lambda: killed.append(2)) is False
    assert killed == [1]
    assert watchdog.STATE["restarts_blocked_by_cooldown"] == 1


def test_health_log_persisted(db):
    from app.models import HealthLog
    before = db.query(HealthLog).count()
    watchdog.check_once()
    assert db.query(HealthLog).count() == before + 1


def test_watchdog_api(client):
    r = client.get("/api/watchdog")
    assert r.status_code == 200
    body = r.json()
    assert "consecutive_failures" in body
    assert body["config"]["failures_to_restart"] == 3
    assert isinstance(body["recent"], list)


def test_scheduler_heartbeat_check():
    from app.services import scheduler
    # 未首跑：宽限通过
    old = scheduler.DIGEST.get("generated_at")
    scheduler.DIGEST["generated_at"] = None
    ok, _ = watchdog._check_scheduler()
    assert ok is True
    # 心跳过期：判异常
    scheduler.DIGEST["generated_at"] = "2020-01-01T00:00:00+00:00"
    ok, info = watchdog._check_scheduler()
    assert ok is False
    scheduler.DIGEST["generated_at"] = old
