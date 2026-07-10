# -*- coding: utf-8 -*-
"""推广充值识别·扫码充值 (2026-07-10): 万相台扫码充值是第三种形态, 计入累计充值, 不再假报"消耗超充值"。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from datetime import date
from decimal import Decimal as D

from app.models.marketing import PromotionFlow
from app.services import reconciliation_service as rec


def _pf(db, d, ftype, amt, remark=None, flow_no=None):
    db.add(PromotionFlow(transaction_date=d, flow_type=ftype, amount=D(str(amt)),
                         remark=remark, alipay_flow_no=flow_no))


def test_scan_recharge_counts_scanpay(db_session):
    """扫码充值计入充值(有流水号佐证), 月度平; 消耗5000 vs 充值5000+期初0 → 不报'消耗超充值'。"""
    _pf(db_session, date(2026, 4, 28), "收入", 5000, remark="万相台扫码充值 补佐证",
        flow_no="2026042822001427681442943165")
    _pf(db_session, date(2026, 4, 29), "支出", 5000, remark="扣款 现金消耗扣款")
    db_session.commit()
    res = rec.run_promotion(db_session, record_exceptions=False)
    apr = next((x for x in res.diffs if "2026-04" in str(x.key)), None)
    assert apr is not None and apr.severity == "ok"           # 充值5000=佐证5000
    burn = next((x for x in res.diffs if "消耗" in str(x.key) or "消耗" in str(x.message)), None)
    if burn is not None:                                       # 充值≥消耗 → 不该报超支
        assert "超过充值" not in str(burn.message) or burn.severity in ("ok", "not_available")
