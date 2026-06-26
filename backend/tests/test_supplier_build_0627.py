# -*- coding: utf-8 -*-
"""按采购记录自动建供应商 + 按料推断类型 (用户 2026-06-27)。"""
from decimal import Decimal

from sqlalchemy import select

from app.models.order import PartPurchase
from app.models.supplier import Supplier
from app.services import supplier_build_service as sbs


def test_infer_type_by_material():
    assert sbs.infer_supplier_type(["岩板货款"]) == "rock_slab"
    assert sbs.infer_supplier_type(["三次贴皮采购"]) == "veneer"
    assert sbs.infer_supplier_type(["0512榉木采购"]) == "beech_wood"
    assert sbs.infer_supplier_type(["五金螺丝结构胶采购"]) == "hardware"
    assert sbs.infer_supplier_type(["D709大板3块"]) == "woodwork"
    assert sbs.infer_supplier_type(["洞石背板到付"]) == "finish_panel"
    assert sbs.infer_supplier_type(["不知道是啥"]) == "other"


def test_auto_build_skips_noise_and_infers(db_session):
    db = db_session
    db.add_all([
        PartPurchase(purchase_no="A1", supplier="宋磊", material_name="岩板货款",
                     amount=Decimal("750"), qty=Decimal("1")),
        PartPurchase(purchase_no="A2", supplier="老孙木皮廠", material_name="18mm贴皮",
                     amount=Decimal("640"), qty=Decimal("1")),
        PartPurchase(purchase_no="A3", supplier="拼多多平台商户", material_name="商户单号XP123",
                     amount=Decimal("380"), qty=Decimal("1")),   # 噪音 → 跳过
    ])
    db.flush()
    prev = sbs.auto_build_from_purchases(db, apply=False)
    assert prev["applied"] is False and prev["created"] == 0
    names_prev = {i["name"] for i in prev["items"]}
    assert "宋磊" in names_prev and "老孙木皮廠" in names_prev
    assert "拼多多平台商户" not in names_prev          # 噪音不进候选

    res = sbs.auto_build_from_purchases(db, apply=True)
    assert res["created"] == 2
    built = {s.name: s for s in db.execute(select(Supplier)).scalars().all()}
    assert built["宋磊"].supplier_type == "rock_slab"
    assert built["老孙木皮廠"].supplier_type == "veneer"
    assert built["宋磊"].alipay_counterparty_keywords == ["宋磊"]   # 名字进关键字, 便于支付宝归账
    assert "拼多多平台商户" not in built

    res2 = sbs.auto_build_from_purchases(db, apply=True)   # 幂等
    assert res2["created"] == 0
