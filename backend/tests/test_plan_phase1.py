# -*- coding: utf-8 -*-
"""Plan 阶段一回归测试 (C2/C14/L3/L2/L4/C4/C3/C1)。

对应 Plan 里的 test_formula_seed / test_import_cleaning / test_refill_autoflag /
test_aftersales_pnl_refresh / test_lowstock_alert_dedupe 等, 合并为单文件按类组织。
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.finance import RefillRecord
from app.models.marketing import AfterSales
from app.models.material import Material
from app.models.order import Order


class TestC2FormulaSeed:
    def test_seed_twice_inserts_once(self, db_session):
        from app.services import formula_engine_service as fes
        first = fes.seed_builtin_rules(db_session)
        assert first > 0
        second = fes.seed_builtin_rules(db_session)
        assert second == 0   # 幂等: 第二次不再插

    def test_align_is_idempotent(self, db_session):
        from app.services import formula_engine_service as fes
        fes.seed_builtin_rules(db_session)
        r1 = fes.align_rules_to_builtin(db_session)
        r2 = fes.align_rules_to_builtin(db_session)
        assert isinstance(r1, dict) and isinstance(r2, dict)


class TestC14ImportCleaning:
    def test_excel_serial_to_date(self):
        from app.services.import_clean import excel_serial_to_date
        assert excel_serial_to_date(46175) == date(2026, 6, 2)
        assert excel_serial_to_date("46175") == date(2026, 6, 2)
        assert excel_serial_to_date(123.45) is None      # 不在合理范围 → 不是日期
        assert excel_serial_to_date("abc") is None

    def test_clean_phone_strips_virtual_ext(self):
        from app.services.import_clean import clean_phone
        assert clean_phone("13900001111-1234") == "13900001111"
        assert clean_phone("13900001111") == "13900001111"
        assert clean_phone("0579-85123456") == "0579-85123456"   # 座机区号不动 (前缀只有4位)
        assert clean_phone(None) is None

    def test_clean_no_strips_whitespace(self):
        from app.services.import_clean import clean_no
        assert clean_no("AB12\t34 ") == "AB1234"
        assert clean_no("　SF123　") == "SF123"   # 全角空格
        assert clean_no("   ") is None

    def test_order_import_to_date_serial(self):
        from app.services.order_import import _to_date
        assert _to_date("46175") == date(2026, 6, 2)
        assert _to_date("2026-06-08") == date(2026, 6, 8)

    def test_bill_import_date_serial(self):
        from app.services.bill_import_service import _date
        assert _date(46175) == date(2026, 6, 2)


class TestL3RefillAutoflag:
    def test_refill_csv_import_flags_order(self, db_session):
        from app.services import bill_import_service
        db_session.add(Order(platform="淘宝", order_no="RF001", status="paid"))
        db_session.flush()
        csv_text = "订单号,补单成本\nRF001,12.5\n"
        rep = bill_import_service.import_refill_records_csv(db_session, csv_text)
        assert rep.inserted == 1
        o = db_session.query(Order).filter_by(order_no="RF001").one()
        assert o.is_refill is True

    def test_rederive_unflags_when_record_removed(self, db_session):
        from app.services import order_sync_service
        db_session.add(Order(platform="淘宝", order_no="RF002", status="paid", is_refill=True))
        db_session.flush()
        res = order_sync_service.rederive_refill_flags(db_session, recompute_cost=False)
        o = db_session.query(Order).filter_by(order_no="RF002").one()
        assert o.is_refill is False
        assert res.unflagged == 1


class TestL2SchedulerJobs:
    def test_jobs_registered(self):
        from app.services import scheduler as sch
        sch._register_default_jobs()
        assert "daily_0645_refill_rederive" in sch._REGISTRY
        assert "daily_0710_factory_payment_backfill" in sch._REGISTRY

    def test_backfill_job_dry_run_by_default(self, db_session):
        from app.services.scheduler import _job_factory_payment_backfill
        res = _job_factory_payment_backfill(db_session)
        assert res["inference_enabled"] is False
        assert "inference_dry_run_preview" in res


class TestL4AftersalesPnlRefresh:
    def test_refresh_order_compensation(self, db_session):
        from app.services import order_sync_service
        db_session.add(Order(platform="淘宝", order_no="AS001", status="signed",
                             paid_amount=Decimal("1000")))
        db_session.add(AfterSales(platform_order_no="AS001",
                                  compensation_fee=Decimal("80")))
        db_session.flush()
        changed = order_sync_service.refresh_order_compensation(db_session, "AS001")
        assert changed is True
        o = db_session.query(Order).filter_by(order_no="AS001").one()
        assert o.compensation_fee == Decimal("80")
        # 再跑一次无变化 → False (幂等)
        assert order_sync_service.refresh_order_compensation(db_session, "AS001") is False


class TestC4AlertDedupe:
    def test_recently_notified_cooldown(self, db_session):
        from app.services import alert_service
        a = alert_service.upsert(
            db_session, kind="low_stock_part", severity="critical",
            title="t", dedupe_key="cool:x", push_notify=False,
        )
        a.notified_at = datetime.now(timezone.utc)
        db_session.flush()
        assert alert_service._recently_notified(db_session, "cool:x") is True
        # 2026-06-11 拍板: 冷却 6h→12h (一天最多推两次)
        a.notified_at = datetime.now(timezone.utc) - timedelta(hours=13)
        db_session.flush()
        assert alert_service._recently_notified(db_session, "cool:x") is False

    def test_get_active_context(self, db_session):
        from app.services import alert_service
        alert_service.upsert(
            db_session, kind="low_stock_part", severity="warn",
            title="t", dedupe_key="ctx:x", push_notify=False,
            context={"factory_order_ids": [1, 2]},
        )
        ctx = alert_service.get_active_context(db_session, "ctx:x")
        assert ctx == {"factory_order_ids": [1, 2]}


class TestC3AntiCrossMaterial:
    def test_same_prefix_different_base_not_reused(self, db_session):
        from app.services.customization_service import _find_reusable_material
        db_session.add(Material(code="AC-9001", name="定制床铺板A", is_custom=True,
                                base_material_code="MW-001", remark="高=2000|宽=1500"))
        db_session.flush()
        # 同名前缀但基础料不同 → 不复用 (防串料)
        m = _find_reusable_material(
            db_session, base_material_code="MW-999",
            base_material_name="定制床铺板A", size_sig="高=2000|宽=1500",
        )
        assert m is None

    def test_same_base_same_dims_reused(self, db_session):
        from app.services.customization_service import _find_reusable_material
        db_session.add(Material(code="AC-9002", name="定制床铺板B", is_custom=True,
                                base_material_code="MW-002",
                                width_mm=Decimal("1500"), height_mm=Decimal("2000")))
        db_session.flush()
        m = _find_reusable_material(
            db_session, base_material_code="MW-002",
            base_material_name="定制床铺板B", size_sig="",
            width_mm=Decimal("1500"), height_mm=Decimal("2000"),
        )
        assert m is not None and m.code == "AC-9002"


class TestC1CustomerScope:
    def test_exclude_historical(self, db_session):
        from app.services import customer_service
        from app.models.customer import Customer
        db_session.add(Order(platform="淘宝", order_no="C1A", status="signed",
                             customer_name="张三", customer_phone="13900001111",
                             paid_amount=Decimal("100"), is_historical=True))
        db_session.add(Order(platform="淘宝", order_no="C1B", status="signed",
                             customer_name="李四", customer_phone="13900002222",
                             paid_amount=Decimal("200"), is_historical=False))
        db_session.flush()
        r_live = customer_service.aggregate_all(db_session, include_historical=False)
        names = {c.name for c in db_session.query(Customer).all()}
        assert "李四" in names and "张三" not in names
        # 默认口径含历史 → 张三回来
        customer_service.aggregate_all(db_session, include_historical=True)
        names = {c.name for c in db_session.query(Customer).all()}
        assert "张三" in names
        assert isinstance(r_live, dict)
