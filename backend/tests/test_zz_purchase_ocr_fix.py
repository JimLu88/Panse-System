# -*- coding: utf-8 -*-
"""采购 OCR 入库两修 (用户 2026-07-16):

1. 每行 total_amount 用【行金额】, 不是发票总额 (多行发票每行 total 曾被错填成整单合计);
2. _date 支持发票表头「月.日」无年份格式(3.26 / 3月26日 / 3/26)→ 补当前年。
"""
from datetime import date
from decimal import Decimal

from app.models.order import PartPurchase
from app.services import screenshot_ingest_service as sis


def test_date_parses_month_day_no_year():
    y = date.today().year
    assert sis._date("3.26") == date(y, 3, 26)
    assert sis._date("3月26日") == date(y, 3, 26)
    assert sis._date("3/26") == date(y, 3, 26)
    assert sis._date("2026-03-26") == date(2026, 3, 26)   # 带年份仍走原路径
    assert sis._date("2026年3月26日") == date(2026, 3, 26)
    assert sis._date("") is None
    assert sis._date("废话") is None
    assert sis._date("13.40") is None                     # 非法月日


def test_commit_uses_line_amount_not_invoice_total(db_session):
    """多行发票: 每行 total_amount = 该行 amount, 不是整单 total_amount(1050)。"""
    parsed = {"purchase": {
        "supplier_name": "山东采购", "purchase_date": "3.26", "total_amount": 1050,
        "lines": [
            {"material_name": "纯黑岩板", "spec": "1800*500", "qty": 1, "unit_price": 260, "amount": 260},
            {"material_name": "纯黑岩板", "spec": "900*500", "qty": 1, "unit_price": 180, "amount": 180},
            {"material_name": "纯白岩板", "spec": "2000*850", "qty": 1, "unit_price": 290, "amount": 290},
        ],
    }}
    r = sis.commit_purchase_parsed(db_session, parsed)
    db_session.commit()
    assert r["inserted"] == 3
    rows = db_session.query(PartPurchase).order_by(PartPurchase.id).all()[-3:]
    totals = sorted(float(p.total_amount) for p in rows)
    assert totals == [180.0, 260.0, 290.0]                # 各行金额, 不是 1050
    assert all(p.purchase_date == date(date.today().year, 3, 26) for p in rows)   # 日期入库
    assert sum(float(p.total_amount) for p in rows) == 730.0


def test_single_line_amount_fallback(db_session):
    """单行 amount 缺失 → 用 单价×数量 兜底, total_amount 同步。"""
    parsed = {"purchase": {"supplier_name": "X", "purchase_date": "2026-03-26",
                           "lines": [{"material_name": "岩板", "qty": 2, "unit_price": 100}]}}
    sis.commit_purchase_parsed(db_session, parsed)
    db_session.commit()
    p = db_session.query(PartPurchase).order_by(PartPurchase.id).all()[-1]
    assert float(p.amount) == 200.0 and float(p.total_amount) == 200.0
