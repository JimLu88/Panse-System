# -*- coding: utf-8 -*-
"""导入翻烧饼灰度监控: 只对"值回跳震荡"报异常, 单向进展不报; 幂等; 稳定后复核销账。"""
from app.models.exception import DataException
from app.models.field_change import FieldChange
from app.services import import_flip_monitor_service as mon


def _fc(db, ono, field, new, old=None):
    db.add(FieldChange(table_name="orders", row_pk=ono, field=field,
                       old_value=old, new_value=new, actor="订单重导", source="import"))


def test_flip_monitor_flags_oscillation_and_idempotent(db_session):
    ono = "FLIP001"
    # 实付回跳: 4500→1795→4500 (值 4500.36 重复出现 = 震荡)
    for old, new in [("1795.17", "4500.36"), ("4500.36", "1795.17"), ("1795.17", "4500.36")]:
        _fc(db_session, ono, "paid_amount", new, old)
    db_session.commit()
    r = mon.scan(db_session, days=30)
    assert r["recorded"] == 1
    exc = db_session.query(DataException).filter_by(exception_type="order_import_flip", source_pk=ono).one()
    assert exc.status == "open"
    # 仍在窗口内震荡 → 复核保留(不销账)
    assert mon.check_resolved(db_session, exc, days=30) is not None
    # 幂等: 再扫不重复记
    r2 = mon.scan(db_session, days=30)
    assert r2["recorded"] == 0 and r2["skipped_existing"] == 1


def test_flip_monitor_ignores_progression(db_session):
    ono = "PROG001"
    # 单向进展 paid→shipped→signed (取值互不相同) → 不算翻, 不报
    for new in ["paid", "shipped", "signed"]:
        _fc(db_session, ono, "status", new)
    db_session.commit()
    assert mon.scan(db_session, days=30)["recorded"] == 0


def test_flip_monitor_resolves_when_stable(db_session):
    ono = "STABLE1"
    exc = DataException(source_table="orders", source_pk=ono, exception_type="order_import_flip",
                        description="t", severity="warning", status="open")
    db_session.add(exc); db_session.commit()
    # 无任何回跳记录 → 复核返回 None(销账)
    assert mon.check_resolved(db_session, exc, days=30) is None
