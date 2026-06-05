"""审计日志留存清理 (优化 #9) 测试: 超期删除、近期保留。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api.audit import prune_audit_logs
from app.models.auth import AuditLog


def test_prune_deletes_old_keeps_recent(db_session):
    old = AuditLog(method="POST", path="/old")
    new = AuditLog(method="POST", path="/new")
    db_session.add_all([old, new])
    db_session.commit()
    # 显式把 old 的时间改成 400 天前 (绕过默认 now)
    old.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    db_session.commit()

    deleted = prune_audit_logs(db_session, days=180)
    assert deleted == 1
    paths = [a.path for a in db_session.execute(select(AuditLog)).scalars()]
    assert paths == ["/new"]
