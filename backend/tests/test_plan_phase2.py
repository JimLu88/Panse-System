# -*- coding: utf-8 -*-
"""Plan 阶段二回归测试 (F2/F5/L8/L5/L7)。

对应 Plan 的 test_release_disposition / test_customization_precheck /
test_match_unlink / 工厂对账拆分 / 定价BOM漂移, 合并单文件按类组织。
"""
from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.factory_recon_item import FactoryReconItem
from app.models.finance import AlipayFlow
from app.models.inventory import PartInventory
from app.models.material import Material
from app.models.order import Order, PartPurchase
from app.models.pricing import PricingSku


class TestL8MatchUnlink:
    def _flow(self, db, no="FLOW001"):
        f = AlipayFlow(account="主力号", transaction_no=no, amount=Decimal("-100"),
                       reconciliation_status="matched", reconciliation_type="other")
        db.add(f)
        db.flush()
        return f

    def test_unlock_when_no_refs(self, db_session):
        from app.services import match_unlink_service as mu
        f = self._flow(db_session)
        assert mu.unlink_purchase(db_session, "FLOW001") is True
        assert f.reconciliation_status == "open"
        assert f.reconciliation_type is None

    def test_shared_flow_not_unlocked(self, db_session):
        """两单共用一流水: 还有引用 → 不解锁 (拆分付款场景)。"""
        from app.services import match_unlink_service as mu
        f = self._flow(db_session, "FLOW002")
        db_session.add(Order(platform="淘宝", order_no="O-1", status="paid",
                             alipay_flow_no="FLOW002"))
        db_session.flush()
        assert mu.unlink_purchase(db_session, "FLOW002") is False
        assert f.reconciliation_status == "matched"


class TestL5ReconDisposition:
    def _item(self, db, price="300.00"):
        it = FactoryReconItem(order_no="R-1", settle_price=Decimal(price))
        db.add(it)
        db.flush()
        return it

    def test_split_must_balance(self, db_session):
        from app.services import factory_recon_service as fr
        it = self._item(db_session)
        with pytest.raises(ValueError):
            fr.split_item(db_session, it.id, parts=[
                {"amount": "100", "resolution_kind": "价差"},
                {"amount": "100", "resolution_kind": "运费"},
            ])   # 100+100 ≠ 300 → 报错

    def test_split_creates_children(self, db_session):
        from app.services import factory_recon_service as fr
        it = self._item(db_session)
        out = fr.split_item(db_session, it.id, parts=[
            {"amount": "200", "resolution_kind": "价差"},
            {"amount": "100", "resolution_kind": "运费"},
        ], actor="tester")
        assert len(out["children"]) == 2
        assert it.resolved is True
        kids = db_session.query(FactoryReconItem).filter_by(parent_item_id=it.id).all()
        assert sum(k.settle_price for k in kids) == Decimal("300")

    def test_confirm_item(self, db_session):
        from app.services import factory_recon_service as fr
        it = self._item(db_session)
        out = fr.confirm_item(db_session, it.id, resolution_kind="补偿", actor="tester")
        assert out["resolution_kind"] == "补偿"
        assert it.confirmed_by == "tester" and it.resolved is True
        with pytest.raises(ValueError):
            fr.confirm_item(db_session, it.id, resolution_kind="瞎填")


class TestL7PricingBomDrift:
    def _setup(self, db, *, mat_price="50", pricing_parts="100"):
        db.add(Material(code="AC-7001", name="测试配件7001", price=Decimal(mat_price)))
        db.add(BomLine(product_code="P7", sku_code="SKU-L7", material_code="AC-7001",
                       qty_per_product=Decimal("2")))
        db.add(PricingSku(product_code="P7", sku_code="SKU-L7",
                          external_parts_cost=Decimal(pricing_parts)))
        db.flush()

    def test_drift_marks_stale(self, db_session):
        from app.services import pricing_bom_sync_service as pbs
        from app.models.pricing_ext import PricingSkuCosts
        self._setup(db_session, mat_price="50", pricing_parts="500")   # BOM=100 vs 定价=500
        r = pbs.check_sku(db_session, "SKU-L7")
        assert r["stale"] is True
        row = db_session.query(PricingSkuCosts).filter_by(sku_code="SKU-L7").one()
        assert row.stale_reason and "差" in row.stale_reason

    def test_within_tolerance_clears(self, db_session):
        from app.services import pricing_bom_sync_service as pbs
        from app.models.pricing_ext import PricingSkuCosts
        self._setup(db_session, mat_price="50", pricing_parts="100")   # BOM=100 = 定价=100
        r = pbs.check_sku(db_session, "SKU-L7")
        assert r["stale"] is False
        row = db_session.query(PricingSkuCosts).filter_by(sku_code="SKU-L7").one()
        assert row.stale_reason is None and row.bom_synced_at is not None


class TestF5Precheck:
    def test_groups(self, db_session):
        from app.services.customization_service import BomDiffLine, precheck_stock
        db_session.add(PartInventory(material_code="AC-5001", warehouse="江西仓库",
                                     physical_qty=Decimal("10"), locked_qty=Decimal("0")))
        db_session.flush()
        lines = [
            BomDiffLine(material_code="AC-5001", material_name="现货件",
                        original_qty=Decimal("1"), new_qty=Decimal("2")),
            BomDiffLine(material_code="AC-5002", material_name="缺货件",
                        original_qty=Decimal("1"), new_qty=Decimal("3")),
            BomDiffLine(material_code="MW-5003", material_name="新开料",
                        original_qty=Decimal("1"), new_qty=Decimal("1"),
                        requires_new_material=True),
        ]
        r = precheck_stock(db_session, lines)
        assert r["has_shortage"] is True
        assert len(r["in_stock"]) == 1
        assert len(r["need_purchase"]) == 1 and r["need_purchase"][0]["shortage"] == 3.0
        assert len(r["need_new_material"]) == 1


class TestF2DispositionGuard:
    def test_internal_purge_unlinks_flow(self, db_session):
        """purge 删内部互转采购后, 对应流水核销状态回 open (L8 钩子联动)。"""
        from app.services import alipay_flow_router_service as ar
        db_session.add(AlipayFlow(account="佳宝号", transaction_no="FLOW-P1",
                                  amount=Decimal("-5000"),
                                  reconciliation_status="matched"))
        db_session.add(PartPurchase(purchase_no="T202600001", supplier="**英",
                                    material_name="理财申购", qty=Decimal("1"),
                                    amount=Decimal("5000"), total_amount=Decimal("5000"),
                                    alipay_flow_no="FLOW-P1"))
        db_session.flush()
        n = ar.purge_non_purchase_records(db_session)
        assert n == 1
        f = db_session.query(AlipayFlow).filter_by(transaction_no="FLOW-P1").one()
        assert f.reconciliation_status == "open"
