# -*- coding: utf-8 -*-
"""补单对账'订单找不到'检查的渠道豁免 (2026-07-12 用户: 补单单独建表, 小红书/孚格平台订单
本就不进订单总表, 不该报假警; 畔色淘宝补单仍查=录错号哨兵)。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from datetime import date
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.finance import RefillRecord
from app.services.data_quality_service import scan_refill_unmatched


def _r(db, no, remark=None):
    db.add(RefillRecord(order_no=no, refill_date=date(2026, 5, 10),
                        order_amount=D("20"), commission=D("8"), remark=remark))


def test_channel_refills_exempt_taobao_still_checked(db_session):
    _r(db_session, "P794795653839454081", "畔色木作的店（小红书）")   # P单号+小红书 → 豁免
    _r(db_session, "5116245840126038945", "淘宝孚格家居")            # 孚格 → 豁免
    _r(db_session, "3300000000000000123")                            # 畔色淘宝, 订单缺 → 仍报
    db_session.flush()
    n = scan_refill_unmatched(db_session)
    assert n == 1
    rows = db_session.query(DataException).filter_by(exception_type="refill_unmatched").all()
    assert len(rows) == 1
    assert "3300000000000000123" in rows[0].description