from app.models.exception import DataException
from app.services import escalation_service


def _add(db, type_, sev="info", status="open"):
    e = DataException(
        source_table="orders", source_pk=f"O{type_}",
        exception_type=type_, severity=sev, status=status, description="x",
    )
    db.add(e)
    db.flush()
    return e


def test_below_threshold_no_escalate(db_session):
    _add(db_session, "type_a", "info")
    _add(db_session, "type_a", "info")  # 2 条不够
    res = escalation_service.run(db_session)
    assert res == []
    assert db_session.query(DataException).first().severity == "info"


def test_threshold_three_escalates_info_to_warning(db_session):
    [_add(db_session, "type_a", "info") for _ in range(3)]
    res = escalation_service.run(db_session)
    assert len(res) == 1
    assert res[0].escalated_from == "info"
    assert res[0].escalated_to == "warning"
    assert len(res[0].affected_ids) == 3
    for e in db_session.query(DataException).all():
        assert e.severity == "warning"
        assert e.escalation_count == 1


def test_threshold_escalates_warning_to_error(db_session):
    [_add(db_session, "type_a", "warning") for _ in range(3)]
    res = escalation_service.run(db_session)
    assert res[0].escalated_to == "error"
    for e in db_session.query(DataException).all():
        assert e.severity == "error"


def test_error_does_not_escalate_further(db_session):
    [_add(db_session, "type_a", "error") for _ in range(3)]
    res = escalation_service.run(db_session)
    assert res == []  # error 不再升


def test_resolved_exceptions_dont_count(db_session):
    [_add(db_session, "type_a", "info") for _ in range(2)]
    _add(db_session, "type_a", "info", status="resolved")  # 已处理不计
    res = escalation_service.run(db_session)
    assert res == []  # open 只有 2 条, 不到 3


def test_mixed_severity_each_group_handled(db_session):
    [_add(db_session, "type_a", "info") for _ in range(3)]
    [_add(db_session, "type_a", "warning") for _ in range(3)]
    res = escalation_service.run(db_session)
    # 两组都升一档, 但 type_a 总共有 6 条 open → 触发，分别处理两个 severity
    assert len(res) == 2
    severities = {(r.escalated_from, r.escalated_to) for r in res}
    assert severities == {("info", "warning"), ("warning", "error")}


def test_idempotent_after_escalation(db_session):
    [_add(db_session, "type_a", "info") for _ in range(3)]
    r1 = escalation_service.run(db_session)
    r2 = escalation_service.run(db_session)
    # 第一次 info → warning, 第二次 warning → error
    assert r1[0].escalated_to == "warning"
    assert r2[0].escalated_to == "error"
    # 第三次再跑 → error 不再升
    r3 = escalation_service.run(db_session)
    assert r3 == []
