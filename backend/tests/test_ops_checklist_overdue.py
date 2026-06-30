"""运营待办超时报警 (2026-06-23): 物流(周)/打包/玻璃/岩板/电力轨道(月) 超时未完成 → Alert。"""
from datetime import date
from decimal import Decimal

from app.models.finance import LogisticsBill, PackingBill
from app.models.alert import Alert
from app.services import ops_checklist_service as ops


def test_is_overdue_monthly_after_threshold():
    glass = next(t for t in ops.OPS_TASKS if t["key"] == "monthly_glass_recon")
    assert glass["overdue_after_days"] == 15
    assert ops._is_overdue(glass, done=False, today=date(2026, 6, 20)) is True    # 第20天 ≥ 15
    assert ops._is_overdue(glass, done=False, today=date(2026, 6, 10)) is False   # 第10天 < 15
    assert ops._is_overdue(glass, done=True, today=date(2026, 6, 20)) is False    # 已完成不算超时


def test_overdue_items_includes_unfinished_monthly(db_session):
    keys = {i["key"] for i in ops.overdue_items(db_session, today=date(2026, 6, 20))}
    assert "monthly_glass_recon" in keys
    assert "monthly_rockslab_recon" in keys
    assert "monthly_electric_rail_recon" in keys
    assert "monthly_packing_recon" in keys     # 本月无打包账单 → 超时


def test_packing_auto_done_clears_overdue(db_session):
    db_session.add(PackingBill(bill_month="2026-06", packing_fee=Decimal("10")))
    db_session.flush()
    keys = {i["key"] for i in ops.overdue_items(db_session, today=date(2026, 6, 20))}
    assert "monthly_packing_recon" not in keys   # 本月已导打包 → 自动完成 → 不超时


def test_weekly_logistics_auto_done_clears_overdue(db_session):
    # 本周内有物流账单(用 today 当天, 必在本 ISO 周) → 自动完成 → 不超时
    db_session.add(LogisticsBill(bill_date=date(2026, 6, 20), carrier="德邦",
                                 freight_amount=Decimal("10")))
    db_session.flush()
    keys = {i["key"] for i in ops.overdue_items(db_session, today=date(2026, 6, 20))}
    assert "weekly_logistics_recon" not in keys


def test_check_and_alert_overdue_writes_alerts(db_session, monkeypatch):
    # 固定"今天"到月中(≥15号): 月度对账 overdue_after_days=15, 月初运行时未到超时点会得0
    # → 原测试随运行日期 flake。锁死日期使其确定 (这条就是历史遗留的"隔离 flake")。
    import datetime as _dt

    class _Mid(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 20)

    monkeypatch.setattr(ops, "date", _Mid)
    res = ops.check_and_alert_overdue(db_session)
    assert res["overdue"] >= 3   # 至少 玻璃/岩板/电力轨道 (运行日 ≥ 当月15号时还含打包)
    cnt = db_session.query(Alert).filter(Alert.kind == "ops_overdue",
                                         Alert.resolved_at.is_(None)).count()
    assert cnt >= 3
    # 同周期重复跑不新增 (dedupe_key 幂等)
    ops.check_and_alert_overdue(db_session)
    cnt2 = db_session.query(Alert).filter(Alert.kind == "ops_overdue",
                                          Alert.resolved_at.is_(None)).count()
    assert cnt2 == cnt
