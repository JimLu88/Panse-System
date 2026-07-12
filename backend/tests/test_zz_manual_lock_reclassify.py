# -*- coding: utf-8 -*-
"""人工锁: 核销类型被人改过(修改档案有记录)的流水, 机器归类一律绕行 (用户 2026-07-12)。

复发案: 流水19365(山**退款)人工归 refund_out 后, 双机战期间无护栏旧镜像的 route 又翻回
factory_payment → 逐月对账假差每日重建。退款护栏只认得"退款"特征; 人工锁兜住所有形态。
"""
from datetime import datetime
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.factory_settlement import FactorySupplierAlias
from app.services import factory_settlement_service as fss
from app.services import field_change_service
from app.services import smart_matching_service as smart


def _flow(**kw):
    kw.setdefault("balance", Decimal("0"))
    return AlipayFlow(**kw)


def test_route_skips_human_locked_flow(db_session):
    """人拍过板的分类(非退款形态, 护栏管不到) route 也不许翻; 无档案的对照流水正常翻。"""
    db_session.add(FactorySupplierAlias(supplier="玉山县博冠家具", alias="玉山", note="t"))
    locked = _flow(account="主力号", transaction_no="LOCK1", transaction_type="转账",
                   amount=Decimal("-800"), counterparty="玉山县博冠家具", remark="代付杂项",
                   reconciliation_type="promotion",
                   transaction_time=datetime(2026, 6, 1, 10, 0))
    free = _flow(account="主力号", transaction_no="FREE1", transaction_type="转账",
                 amount=Decimal("-900"), counterparty="玉山县博冠家具", remark="6月货款",
                 reconciliation_type=None,
                 transaction_time=datetime(2026, 6, 2, 10, 0))
    db_session.add_all([locked, free])
    db_session.flush()
    # 人的决定进修改档案 → 锁生效
    field_change_service.record(db_session, table="alipay_flows", pk=locked.id,
                                field="reconciliation_type", old="factory_payment",
                                new="promotion", actor="测试用户", source="web")
    db_session.commit()
    fss.route_alipay_settlements(db_session)
    assert locked.reconciliation_type == "promotion"        # 人改过 → 机器绕行
    assert free.reconciliation_type == "factory_payment"    # 没人碰过 → 照常归类


def test_reclassify_refund_mislabels_skips_human_locked(db_session):
    """reclassify_refund_mislabels: 描述含退款但人已拍板 factory_payment → 不动; 无档案对照归 refund。"""
    locked = _flow(account="主力号", transaction_no="LOCK2", transaction_type="转账",
                   amount=Decimal("-50"), counterparty="某人", remark="退款字样但实为货款",
                   reconciliation_type="factory_payment",
                   transaction_time=datetime(2026, 6, 3, 10, 0))
    free = _flow(account="主力号", transaction_no="FREE2", transaction_type="退款",
                 amount=Decimal("-60"), counterparty="某客", remark="退款-桌子",
                 reconciliation_type="other",
                 transaction_time=datetime(2026, 6, 4, 10, 0))
    db_session.add_all([locked, free])
    db_session.flush()
    field_change_service.record(db_session, table="alipay_flows", pk=locked.id,
                                field="reconciliation_type", old="refund",
                                new="factory_payment", actor="测试用户", source="web")
    db_session.commit()
    smart.reclassify_refund_mislabels(db_session)
    assert locked.reconciliation_type == "factory_payment"  # 人拍板 → 不翻
    assert free.reconciliation_type == "refund"             # 对照正常归位
