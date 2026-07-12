# -*- coding: utf-8 -*-
"""物流配单候选 + 「订单库无此单」误报修复测试 (用户 2026-07-12)。

背景: 壹米滴答 01-08 王先生单(316927…6979)人工指定后, 订单在库(status=signed)但
customer_name 为 NULL, 前端拿"客户名为空"当"订单库无此单" → 23 条全是误报。
修法: LogisticsBillOut 加 order_exists(存在性与有没有名字分开); 配单加打包费同款候选下拉。
"""
from datetime import date
from decimal import Decimal

from app.models.finance import LogisticsBill
from app.models.order import Order
from app.services import logistics_bill_match


def _order(db, no, name, addr, *, status="signed", odate=date(2026, 1, 5), paid="100"):
    db.add(Order(
        platform="淘宝", order_no=no, qty=1, status=status,
        order_date=odate, paid_amount=Decimal(paid),
        customer_name=name, customer_address=addr,
    ))
    db.flush()


def _bill(db, **kw):
    kw.setdefault("freight_amount", Decimal("312"))
    kw.setdefault("row_type", "line")
    b = LogisticsBill(bill_date=date(2026, 1, 8), carrier="壹米滴答", **kw)
    db.add(b)
    db.flush()
    return b


# ---------- order_exists: 存在性与客户名分开 ----------

def test_enrich_order_exists_but_no_name(db_session):
    """订单在库但没存客户名 → order_exists=True + 客户名空 (曾被误报成'订单库无此单')."""
    from app.api.finance import _enrich_logistics_bills
    _order(db_session, "3169278769829196979", None, None)   # 王先生案: 在库、无名、无地址
    b = _bill(db_session, order_no="3169278769829196979", match_method="manual",
              recipient_name="王先生", destination="内蒙古自治区-兴安盟")
    out = _enrich_logistics_bills(db_session, [b])[0]
    assert out.order_exists is True
    assert out.order_customer_name is None


def test_enrich_order_truly_missing(db_session):
    """订单号真的不在库 → order_exists=False (这才是'订单库无此单')."""
    from app.api.finance import _enrich_logistics_bills
    b = _bill(db_session, order_no="9999999999", match_method="manual", recipient_name="张三")
    out = _enrich_logistics_bills(db_session, [b])[0]
    assert out.order_exists is False
    assert out.order_customer_name is None


def test_enrich_order_exists_with_name(db_session):
    """订单在库且有客户名 → order_exists=True + 名字带回."""
    from app.api.finance import _enrich_logistics_bills
    _order(db_session, "O1", "李四", "浙江省杭州市西湖区")
    b = _bill(db_session, order_no="O1", match_method="track", recipient_name="李四")
    out = _enrich_logistics_bills(db_session, [b])[0]
    assert out.order_exists is True
    assert out.order_customer_name == "李四"


# ---------- match_candidates: 按客户名找候选(人工筛) ----------

def test_candidates_sorted_by_name_score(db_session):
    """全等 1.0 > 包含 0.9 > 相似度低的; 与订单无关的名字排后."""
    _order(db_session, "C1", "王先生", "上海市浦东新区")
    _order(db_session, "C2", "王先生生", "上海市浦东新区")
    _order(db_session, "C3", "完全无关名", "上海市浦东新区")
    b = _bill(db_session, recipient_name="王先生", destination="上海市")
    cands = logistics_bill_match.match_candidates(db_session, b.id)
    assert [c["order_no"] for c in cands][:2] == ["C1", "C2"]
    assert cands[0]["score"] == 1.0
    assert cands[1]["score"] == 0.9


def test_candidates_addr_hit_breaks_tie(db_session):
    """同名同分 → 目的地省市对上订单地址的(addr_hit)排前 (王先生同名不同地场景)."""
    _order(db_session, "A_wrong", "王先生", "广东省深圳市南山区")
    _order(db_session, "A_right", "王先生", "内蒙古自治区兴安盟扎赉特旗")
    b = _bill(db_session, recipient_name="王先生", destination="内蒙古自治区-兴安盟-扎赉特旗-音德尔镇")
    cands = logistics_bill_match.match_candidates(db_session, b.id)
    assert cands[0]["order_no"] == "A_right"
    assert cands[0]["addr_hit"] is True
    assert cands[1]["order_no"] == "A_wrong"
    assert cands[1]["addr_hit"] is False


def test_candidates_name_override_and_excludes_cancelled(db_session):
    """name_override 即时生效(改名没保存也能刷候选); 关闭单按铁律不进候选."""
    _order(db_session, "N1", "赵六", "江苏省南京市")
    _order(db_session, "N2", "赵六", "江苏省南京市", status="cancelled")
    b = _bill(db_session, recipient_name="识别错的名", destination="江苏省南京市")
    assert logistics_bill_match.match_candidates(db_session, b.id) == [] or \
        all(c["score"] < 1.0 for c in logistics_bill_match.match_candidates(db_session, b.id))
    cands = logistics_bill_match.match_candidates(db_session, b.id, name_override="赵六")
    assert [c["order_no"] for c in cands] == ["N1"]   # 关闭单 N2 不出现
    assert cands[0]["score"] == 1.0


def test_candidates_limit_and_day_gap_order(db_session):
    """limit 生效; 同名同分同地 → 下单日离账单日(01-08)近的排前."""
    _order(db_session, "D_far", "钱七", "湖南省长沙市", odate=date(2025, 10, 1))
    _order(db_session, "D_near", "钱七", "湖南省长沙市", odate=date(2026, 1, 6))
    b = _bill(db_session, recipient_name="钱七", destination="湖南省长沙市")
    cands = logistics_bill_match.match_candidates(db_session, b.id, limit=1)
    assert len(cands) == 1
    assert cands[0]["order_no"] == "D_near"


def test_candidates_empty_name_returns_empty(db_session):
    """账单无收货人且没传 override → 空列表, 不报错."""
    b = _bill(db_session, recipient_name=None)
    assert logistics_bill_match.match_candidates(db_session, b.id) == []
