"""定时任务可配置化 — schedule override 合并 + 6h 默认 + 校验。纯逻辑, 不连 DB/网络。"""
from __future__ import annotations

import pytest

from app.services import scheduler as sch


def _cfg(cron=None, interval=None):
    return {"label": "x", "fn": lambda db: {}, "cron": cron,
            "interval_minutes": interval, "enabled": True}


def test_effective_interval_default():
    kind, schedule, enabled = sch._effective_schedule(_cfg(interval=120), {})
    assert kind == "interval"
    assert schedule["interval_minutes"] == 120
    assert enabled is True


def test_effective_interval_override():
    _, schedule, _ = sch._effective_schedule(_cfg(interval=120), {"interval_minutes": 360})
    assert schedule["interval_minutes"] == 360


def test_effective_interval_floor_at_1():
    # 负数等异常值兜底到 1 分钟 (0 视为"未设置"回落默认; set_schedule 已拦截 <1 写入)
    _, schedule, _ = sch._effective_schedule(_cfg(interval=120), {"interval_minutes": -5})
    assert schedule["interval_minutes"] == 1


def test_effective_interval_zero_falls_back_to_default():
    _, schedule, _ = sch._effective_schedule(_cfg(interval=120), {"interval_minutes": 0})
    assert schedule["interval_minutes"] == 120


def test_effective_cron_merge_keeps_other_fields():
    kind, schedule, _ = sch._effective_schedule(
        _cfg(cron={"hour": 9, "minute": 0}), {"cron": {"hour": 15}}
    )
    assert kind == "cron"
    assert schedule == {"hour": 15, "minute": 0}


def test_effective_cron_ignores_unknown_keys():
    _, schedule, _ = sch._effective_schedule(
        _cfg(cron={"hour": 9, "minute": 0}), {"cron": {"hour": 8, "evil": 1}}
    )
    assert "evil" not in schedule
    assert schedule["hour"] == 8


def test_effective_disabled():
    _, _, enabled = sch._effective_schedule(_cfg(interval=60), {"enabled": False})
    assert enabled is False


def test_effective_default_disabled():
    cfg = _cfg(interval=60)
    cfg["enabled"] = False
    _, _, enabled = sch._effective_schedule(cfg, {})
    assert enabled is False


def test_user_override_can_enable_default_disabled_job():
    cfg = _cfg(interval=60)
    cfg["enabled"] = False
    _, _, enabled = sch._effective_schedule(cfg, {"enabled": True})
    assert enabled is True


def test_shipments_default_is_6h():
    sch._REGISTRY.clear()
    sch._register_default_jobs()
    assert "shipments_tracking_6h" in sch._REGISTRY
    assert sch._REGISTRY["shipments_tracking_6h"]["interval_minutes"] == 360
    assert sch._REGISTRY["shipments_tracking_6h"]["enabled"] is False


def test_load_reduction_schedules_are_scoped():
    sch._REGISTRY.clear()
    sch._register_default_jobs()
    assert sch._REGISTRY["feishu_sync_30min"]["interval_minutes"] == 360
    assert sch._REGISTRY["hourly_gallery_thumb_warm"]["cron"] == {"hour": 4, "minute": 10}
    assert sch._REGISTRY["hourly_ingest_scan"]["cron"] == {"hour": "18-22", "minute": 15}
    assert sch._REGISTRY["pull_catchup_30min"]["cron"] == {
        "hour": "19-22", "minute": 17}


def test_set_schedule_unknown_job_raises():
    with pytest.raises(ValueError):
        sch.set_schedule(None, "does_not_exist_job", enabled=False)
