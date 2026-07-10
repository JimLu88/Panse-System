# -*- coding: utf-8 -*-
"""已知供应商自动建单免存疑 (2026-07-10, 用户定性: 陈金贵=榉木/泰盛隆=榉木皮/和国=岩板/美丽=五金)。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑, 同 test_zz_purchase_payment_shared_flow。)"""
from datetime import datetime
from decimal import Decimal as D

from app.models.finance import AlipayFlow
from app.models.order import PartPurchase
from app.models.supplier import Supplier
from app.services import alipay_flow_router_service as router
from app.services import data_quality_service as dq


def _flow(db, tno, cp, amt=-500, remark="岩板采购"):
    # transaction_type 不能用"转账"(命中 _NON_PURCHASE_KW 会被建单排除, 测不到分类分支)
    db.add(AlipayFlow(account="主力号", transaction_no=tno, transaction_type="即时到账交易",
                      amount=D(str(amt)), balance=D("0"), counterparty=cp, remark=remark,
                      transaction_time=datetime(2026, 7, 1, 10, 0)))


def test_known_supplier_purchase_not_suspect(db_session):
    """对手方命中供应商关键词(和国=岩板) → 建单归'配件采购(已知供应商)', 不进存疑、不刷异常。"""
    db_session.add(Supplier(name="和国", supplier_type="parts",
                            alipay_counterparty_keywords=["和国", "**国"], remark="岩板供应商"))
    _flow(db_session, "KS1", "和国(**国)")
    db_session.commit()
    router.create_purchases_from_unclassified(db_session)
    db_session.commit()
    p = db_session.query(PartPurchase).filter(PartPurchase.alipay_flow_no == "KS1").one()
    assert p.purchase_type == router.KNOWN_SUPPLIER_PURCHASE_TYPE
    n = dq.scan_unclassified_purchase(db_session)
    db_session.commit()
    from app.models.exception import DataException
    assert not [e for e in db_session.query(DataException).filter(
        DataException.exception_type == "unclassified_purchase",
        DataException.status == "open").all() if str(p.id) == str(e.source_pk)]


def test_unknown_counterparty_still_suspect(db_session):
    """对照: 陌生对手方仍走存疑, 安全网不放水。"""
    _flow(db_session, "KS2", "神秘人(**秘)")
    db_session.commit()
    router.create_purchases_from_unclassified(db_session)
    db_session.commit()
    p = db_session.query(PartPurchase).filter(PartPurchase.alipay_flow_no == "KS2").one()
    assert p.purchase_type == router.UNCLASSIFIED_PURCHASE_TYPE
