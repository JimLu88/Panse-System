"""全量导出 Excel + 可配置定时备份。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import backup_service, settings_service


def test_export_all_creates_xlsx(db_session, tmp_path):
    path = backup_service.export_all(db_session, output_dir=str(tmp_path))
    assert path.exists()
    assert path.name.startswith("panse_backup_") and path.name.endswith(".xlsx")
    # 列表能读到
    files = backup_service.list_backups(str(tmp_path))
    assert any(f["filename"] == path.name for f in files)


def test_run_writes_and_rotates(db_session, tmp_path):
    result = backup_service.run(db_session, output_dir=str(tmp_path))
    assert result["file"].startswith("panse_backup_")
    assert result["size_mb"] >= 0


def test_config_defaults_and_set(db_session):
    cfg = backup_service.get_config(db_session)
    assert cfg["auto_enabled"] is True
    assert cfg["interval_days"] == 7  # 默认 7 天

    cfg2 = backup_service.set_config(db_session, interval_days=14, auto_enabled=False)
    assert cfg2["interval_days"] == 14
    assert cfg2["auto_enabled"] is False


def test_run_if_due_respects_interval(db_session, tmp_path):
    backup_service.set_config(db_session, dir=str(tmp_path), interval_days=7, auto_enabled=True)

    # 首次: 无 last_run → 应执行
    r1 = backup_service.run_if_due(db_session)
    assert r1.get("ran") is True

    # 紧接着再调: 距上次不足 7 天 → 跳过
    r2 = backup_service.run_if_due(db_session)
    assert "skipped" in r2

    # 关闭后跳过
    backup_service.set_config(db_session, auto_enabled=False)
    r3 = backup_service.run_if_due(db_session)
    assert r3.get("skipped") == "自动备份已关闭"


def test_run_if_due_runs_after_interval(db_session, tmp_path):
    backup_service.set_config(db_session, dir=str(tmp_path), interval_days=7, auto_enabled=True)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    settings_service.set_value(db_session, "backup_last_run_at", old)
    db_session.commit()
    r = backup_service.run_if_due(db_session)
    assert r.get("ran") is True


def test_feishu_reset_requires_config(db_session):
    # 未配置飞书 → reset_feishu_data 抛 FeishuError
    from app.services import data_reset_service
    from app.services.feishu_client import FeishuError
    with pytest.raises(FeishuError):
        data_reset_service.reset_feishu_data(db_session)
