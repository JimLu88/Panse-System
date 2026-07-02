"""夜间模式「系统异常」告警的自愈: 体检恢复后自动销警, 不必等12h或人工点已知晓。

场景: 部署时新迁移文件先进容器、几秒后才 alembic upgrade, 夜间看门狗在窗口内误报
migrations current<latest; upgrade 跑完条件恢复 → 下一轮体检 _resolve_quiet_alert 自动关掉。
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.alert import Alert
from app.services import alert_service, system_monitor


def test_quiet_alert_self_resolves(db_session):
    db = db_session
    alert_service.upsert(
        db, kind="watchdog", severity="critical",
        title="夜间模式: 系统异常(已暂停自动重启)",
        body="migrations: current=0116 latest=0117",
        dedupe_key="watchdog_quiet_fail", push_notify=False,
    )
    db.commit()
    a = db.execute(select(Alert).where(
        Alert.dedupe_key == "watchdog_quiet_fail", Alert.resolved_at.is_(None)
    )).scalar_one()
    assert a.resolved_at is None

    # 体检恢复 → 自愈销警
    system_monitor._resolve_quiet_alert(db)
    assert db.get(Alert, a.id).resolved_at is not None


def test_resolve_quiet_alert_noop_when_none(db_session):
    """没有夜间告警时调用是安全的 no-op(不抛)。"""
    system_monitor._resolve_quiet_alert(db_session)
