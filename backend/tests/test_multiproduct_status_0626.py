# -*- coding: utf-8 -*-
"""多宝贝订单状态纠偏 (用户实测 2026-06-26, 订单 5115237121779012546)。

病根: _parse 聚合一单多商品时, 整单「订单状态」只取该主订单号【首个子订单行】。
若首行恰是被退款/关闭的子单 → 整单被误标 cancelled/aftersales, 把另一件真实成交产品一起漏出销售口径。
修复: 多商品单 (len(lines)>1) 若有真实收款 (店铺实收>0 或 已付款单的部分退款) 且非全额退款 → 纠正为 signed。
红线: 单商品关闭单 / 全额退款单 / 拍下未付款多件单 (实收0+无退款) 一律不动。
"""
import csv
import io

from app.models.order import Order
from app.services import taobao_order_import as tio


def _csv(rows) -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("gbk")


_HEAD = ["子订单编号", "主订单编号", "商品标题", "购买数量", "商家编码", "商品属性",
         "订单状态", "买家应付货款", "买家实付金额", "打款商家金额", "退款金额", "订单创建时间"]


def test_multi_partial_refund_paid_is_settled(db_session):
    """5115237121779012546 形态: 2 商品, 首行(被退)交易关闭, 次行(留)交易成功, 实收>0 → 纠正为 signed。"""
    rows = [_HEAD,
            # 首行 = 被退款的子单 (交易关闭) —— 病根: 整单状态原本被它定调成 cancelled
            ["T1", "T700", "畔色岩板实木餐桌", "2", "23210020201", "颜色分类:榉木1.4米",
             "交易关闭", "906.88", "906.88", "0.00", "906.88", "2026-05-08 10:00:00"],
            # 次行 = 留下的真实成交产品 (交易成功), 店铺实收 883.30
            ["T2", "T700", "畔色岩板实木餐桌", "1", "23210020201", "颜色分类:榉木1.4米白岩板",
             "交易成功", "2636.73", "2636.73", "883.30", "0.00", "2026-05-08 10:00:00"]]
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _csv(rows))
    assert rep.inserted == 1
    o = db_session.query(Order).filter_by(order_no="T700").one()
    assert o.status == "signed"                      # 不再是 cancelled
    assert str(o.paid_amount) == "3543.61"           # 两子单实付求和
    assert str(o.refund_amount) == "906.88"          # 退款随行求和
    # 真实净销售 = 实付 - 退款 = 2636.73, 应被计入成交
    from app.services.sales_analytics import is_settled_sale
    assert is_settled_sale(o) is True


def test_multi_never_paid_cart_stays_cancelled(db_session):
    """5116831308695006739 形态: 多件拍下未付款 (全行交易关闭, 实收0, 无退款, paid=应付兜底假值) → 必须仍 cancelled。"""
    rows = [_HEAD,
            ["U1", "U800", "畔色全实木榉木床头柜", "1", "23210020201", "颜色分类:榉木",
             "交易关闭", "1980.00", "", "", "", "2026-05-21 09:00:00"],
            ["U2", "U800", "畔色日式悬浮榉木双人床", "1", "23250050202", "颜色分类:榉木1.8米",
             "交易关闭", "8630.00", "", "", "", "2026-05-21 09:00:00"]]
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _csv(rows))
    assert rep.inserted == 1
    o = db_session.query(Order).filter_by(order_no="U800").one()
    assert o.status == "cancelled"                   # 实收0+无退款 → 不翻正 (拍下未付款假单)
    from app.services.sales_analytics import is_settled_sale
    assert is_settled_sale(o) is False               # 仍被排除出成交


def test_multi_full_refund_stays_terminated(db_session):
    """全额退款的多商品单 (退光) → 非真实成交, 仍排除 (不被纠偏误翻正)。"""
    rows = [_HEAD,
            ["V1", "V900", "畔色实木餐椅", "2", "23250050202", "颜色分类:胡桃木",
             "退款成功", "1000.00", "1000.00", "0.00", "1000.00", "2026-05-10 09:00:00"],
            ["V2", "V900", "畔色实木餐桌", "1", "23210020201", "颜色分类:砂白2.0米",
             "退款成功", "2000.00", "2000.00", "0.00", "2000.00", "2026-05-10 09:00:00"]]
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _csv(rows))
    assert rep.inserted == 1
    o = db_session.query(Order).filter_by(order_no="V900").one()
    assert o.status != "signed"                      # 全退不算成交
    from app.services.sales_analytics import is_settled_sale
    assert is_settled_sale(o) is False


def test_single_line_cancelled_untouched(db_session):
    """单商品关闭单 (即便实收>0) 不在本次纠偏范围 (那是另一类问题) → 行为零变化, 仍 cancelled。"""
    rows = [_HEAD,
            ["W1", "W950", "畔色实木岩板餐边柜", "1", "23210020201", "颜色分类:樱桃木160cm",
             "交易关闭", "5000.00", "5000.00", "4000.00", "0.00", "2026-05-12 09:00:00"]]
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _csv(rows))
    assert rep.inserted == 1
    o = db_session.query(Order).filter_by(order_no="W950").one()
    assert o.status == "cancelled"                   # 单商品单不动 (len(lines)==1)
