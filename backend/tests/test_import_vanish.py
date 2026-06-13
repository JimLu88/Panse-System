"""导入消失检测 (import_vanish_service) 测试。"""
from app.models.exception import DataException
from app.services import import_vanish_service as ivs


def test_report_missing_idempotent(db_session):
    n1 = ivs.report_missing(
        db_session, source_table="orders", label="订单",
        missing=["T-GONE-1", "T-GONE-2"], scope_desc="本次文件覆盖 2026-05-01~2026-05-31")
    assert n1 == 2
    # 再报同样的键 → 不重复
    n2 = ivs.report_missing(
        db_session, source_table="orders", label="订单",
        missing=["T-GONE-1", "T-GONE-2"], scope_desc="再次导入")
    assert n2 == 0
    excs = db_session.query(DataException).filter_by(
        exception_type="import_missing", source_table="orders").all()
    assert len(excs) == 2
    assert all("未做任何改动" in e.description for e in excs)
    assert all(e.status == "open" for e in excs)


def test_resolve_reappeared(db_session):
    ivs.report_missing(
        db_session, source_table="orders", label="订单",
        missing=["T-BACK"], scope_desc="窗口")
    # 该单在下次导入中重新出现 → 自动销账
    n = ivs.resolve_reappeared(db_session, source_table="orders",
                               present_keys={"T-BACK", "T-OTHER"})
    assert n == 1
    e = db_session.query(DataException).filter_by(
        exception_type="import_missing", source_pk="T-BACK").one()
    assert e.status == "resolved"
    assert e.resolved_by == "导入重现自动销账"
