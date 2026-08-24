# -*- coding: utf-8 -*-
"""销售排行榜 #19/#25: 内部短名替淘宝长名 + P↔PPS前缀漂移合并去重 + 总销售额去退款。"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.models.product import Product
from app.services import sales_analytics as sa


def test_ranking_internal_name_pps_merge_and_refund(db_session):
    db_session.add(Product(code="PPS24210070901", name="榉木岩板餐桌"))
    # 同一款两笔: 一笔用规范码 PPS, 一笔用漂移码 P → 应合并成一行, 显示内部短名(非淘宝长标题)
    db_session.add(Order(platform="淘宝", order_no="O1", product_code="PPS24210070901",
                         product_name="畔色 岩板实木餐桌日式简约长方形榉木家用饭桌超长淘宝标题",
                         qty=1, order_date=date(2026, 6, 10), status="paid",
                         paid_amount=Decimal("3000")))
    db_session.add(Order(platform="淘宝", order_no="O2", product_code="P24210070901",
                         product_name="畔色 另一个长标题变体",
                         qty=1, order_date=date(2026, 6, 11), status="paid",
                         paid_amount=Decimal("3000"), refund_amount=Decimal("1000")))
    db_session.flush()

    r = sa.product_ranking(db_session, granularity="month", period="2026-06")
    assert r["refund_excluded"] is True

    # P/PPS 合并 → 仅一行
    assert len(r["ranking"]) == 1
    row = r["ranking"][0]
    # 内部短名, 非淘宝长标题
    assert row["product_name"] == "榉木岩板餐桌"
    assert row["product_code"] == "PPS24210070901"
    # 总销售额去退款: 3000 + (3000 - 1000) = 5000
    assert abs(row["revenue"] - 5000) < 0.01


def test_window_summary_recent_sales_and_top(db_session):
    """近N天销售速览(经营日报用): 窗口内正式成交计销售额+订单数, 窗口外/补单排除, top按销售额。"""
    from datetime import timedelta
    today = date.today()
    db_session.add(Product(code="PPS30000000001", name="爆款餐桌"))
    db_session.add(Product(code="PPS30000000002", name="次款椅子"))
    db_session.add(Order(platform="淘宝", order_no="W1", product_code="PPS30000000001",
                         product_name="爆款餐桌", qty=1, order_date=today - timedelta(days=1),
                         status="paid", paid_amount=Decimal("3000")))
    db_session.add(Order(platform="淘宝", order_no="W2", product_code="PPS30000000001",
                         product_name="爆款餐桌", qty=1, order_date=today - timedelta(days=3),
                         status="paid", paid_amount=Decimal("2000")))
    db_session.add(Order(platform="淘宝", order_no="W3", product_code="PPS30000000002",
                         product_name="次款椅子", qty=1, order_date=today - timedelta(days=5),
                         status="paid", paid_amount=Decimal("1000")))
    db_session.add(Order(platform="淘宝", order_no="W4", product_code="PPS30000000001",  # 20天前
                         product_name="爆款餐桌", qty=1, order_date=today - timedelta(days=20),
                         status="paid", paid_amount=Decimal("4000")))
    db_session.add(Order(platform="淘宝", order_no="W5", product_code="PPS30000000001",  # 补单排除
                         product_name="爆款餐桌", qty=1, order_date=today - timedelta(days=2),
                         status="paid", paid_amount=Decimal("9999"), is_refill=True))
    db_session.add(Order(platform="淘宝", order_no="W6", product_code="PPS30000000002",  # 恰好7天前，不属于含今天的近7天
                         product_name="次款椅子", qty=1, order_date=today - timedelta(days=7),
                         status="paid", paid_amount=Decimal("700")))
    db_session.flush()

    s7 = sa.window_summary(db_session, days=7)
    assert s7["order_count"] == 3                       # W1,W2,W3 (补单W5排除, W4窗口外)
    assert abs(s7["revenue"] - 6000) < 0.01             # 3000+2000+1000
    assert s7["top"][0]["name"] == "爆款餐桌" and abs(s7["top"][0]["revenue"] - 5000) < 0.01

    s30 = sa.window_summary(db_session, days=30, top_n=3)
    assert s30["order_count"] == 5                       # +W4 + W6
    assert abs(s30["revenue"] - 10700) < 0.01            # 6000 + 4000 + 700


def test_date_summary_only_counts_requested_day(db_session):
    """昨日销售额不能混入今天或前天，且继续排除补单。"""
    from datetime import timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)
    db_session.add_all([
        Order(platform="淘宝", order_no="D-Y", product_name="樱桃木床", qty=1,
              order_date=yesterday, status="paid", paid_amount=Decimal("3200")),
        Order(platform="淘宝", order_no="D-T", product_name="岩板餐桌", qty=1,
              order_date=today, status="paid", paid_amount=Decimal("5000")),
        Order(platform="淘宝", order_no="D-R", product_name="补单", qty=1,
              order_date=yesterday, status="paid", paid_amount=Decimal("9999"), is_refill=True),
    ])
    db_session.flush()

    result = sa.date_summary(db_session, on_date=yesterday)

    assert result["date"] == yesterday.isoformat()
    assert result["order_count"] == 1
    assert result["revenue"] == 3200.0


def test_ranking_falls_back_to_taobao_when_no_internal(db_session):
    """产品档案查不到 → 回退淘宝名(不致崩, 仍按 product_code 聚合去重)。"""
    db_session.add(Order(platform="淘宝", order_no="O3", product_code="P99999999999",
                         product_name="某未建档产品长标题", qty=2,
                         order_date=date(2026, 6, 12), status="paid",
                         paid_amount=Decimal("1000")))
    db_session.flush()
    r = sa.product_ranking(db_session, granularity="month", period="2026-06")
    assert len(r["ranking"]) == 1
    assert r["ranking"][0]["product_name"] == "某未建档产品长标题"


def test_ranking_profit_metric_sorts_by_rate_and_exposes_amount(db_session):
    """metric=profit (利润率, 用户 2026-06-25): 按净利率排序, 每行同时给出利润额(¥)与利润率(%)。

    两单同实付(10000)、同月(平台扣点/税相同) → 利润率高低只由成本决定:
      高毛利品 theoretical_cost=1000 → 净利率 > 低毛利品 theoretical_cost=8000。
    """
    db_session.add(Product(code="PPS10000000001", name="高毛利柜"))
    db_session.add(Product(code="PPS10000000002", name="低毛利桌"))
    # 高毛利: 成本低 → 利润率高
    db_session.add(Order(platform="淘宝", order_no="H1", product_code="PPS10000000001",
                         product_name="高毛利柜", qty=1, order_date=date(2026, 6, 5),
                         status="paid", paid_amount=Decimal("10000"),
                         theoretical_cost=Decimal("1000")))
    # 低毛利: 成本高 → 利润率低
    db_session.add(Order(platform="淘宝", order_no="L1", product_code="PPS10000000002",
                         product_name="低毛利桌", qty=1, order_date=date(2026, 6, 6),
                         status="paid", paid_amount=Decimal("10000"),
                         theoretical_cost=Decimal("8000")))
    db_session.flush()

    r = sa.product_ranking(db_session, granularity="month", metric="profit", period="2026-06")
    assert r["metric"] == "profit"
    assert len(r["ranking"]) == 2

    top, second = r["ranking"][0], r["ranking"][1]
    # 利润额 + 利润率 两个字段都在 (用户: 排行榜旁边也要有利润数字)
    for row in (top, second):
        assert "net_profit" in row and "profit_rate" in row
        # profit_rate == net_profit / revenue (自洽)
        assert abs(row["profit_rate"] - row["net_profit"] / row["revenue"]) < 1e-6
    # 按利润率降序: 高毛利柜在前, 且利润额也更高 (同实付下)
    assert top["product_name"] == "高毛利柜"
    assert top["profit_rate"] > second["profit_rate"]
    assert top["net_profit"] > second["net_profit"]

    # 周期冠军时间线带利润字段, 冠军=利润率最高者
    p = next(x for x in r["periods"] if x["period"] == "2026-06")
    assert p["champion_name"] == "高毛利柜"
    assert abs(p["champion_profit_rate"] - top["profit_rate"]) < 1e-6
    assert abs(p["champion_profit"] - top["net_profit"]) < 1e-6
    # 合计利润 = 两单净利之和; 合计利润率 = 合计利润 / 合计销售额
    assert abs(p["total_profit"] - (top["net_profit"] + second["net_profit"])) < 1e-6
    assert abs(p["total_profit_rate"] - p["total_profit"] / p["total_revenue"]) < 1e-6


def test_ranking_revenue_metric_still_backward_compatible(db_session):
    """老口径不回归: metric=revenue 仍按销售额排序, 既有字段保留 (新增 net_profit/profit_rate 不破坏)。"""
    db_session.add(Order(platform="淘宝", order_no="R1", product_code="PPS20000000001",
                         product_name="大单品", qty=1, order_date=date(2026, 6, 5),
                         status="paid", paid_amount=Decimal("9000"),
                         theoretical_cost=Decimal("3000")))
    db_session.add(Order(platform="淘宝", order_no="R2", product_code="PPS20000000002",
                         product_name="小单品", qty=1, order_date=date(2026, 6, 6),
                         status="paid", paid_amount=Decimal("2000"),
                         theoretical_cost=Decimal("500")))
    db_session.flush()
    r = sa.product_ranking(db_session, granularity="month", metric="revenue", period="2026-06")
    assert r["metric"] == "revenue"
    assert [row["product_name"] for row in r["ranking"]] == ["大单品", "小单品"]
    # 既有字段仍在
    for row in r["ranking"]:
        assert {"rank", "product_code", "product_name", "qty", "revenue", "order_count"} <= set(row)
