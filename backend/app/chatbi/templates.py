# -*- coding: utf-8 -*-
"""ChatBI 20 模板注册表 (Plan4 v2 §5) —— 口径已审, 命中即 ✅。

三类:
  service : 复用现有报表 service 函数 (dashboard_monthly/sales_analytics…) → 与报表页数字天然一致。
  sql     : 携带受约束 spec, 交 assembler+executor 走指标字典拼 SQL (可 SQL 直算的口径)。
  pointer : 依赖尚未落库/口径过复杂的 → 不给数, 指向报表页 (拒答优于错数; D5 可接 service)。
每个模板带触发词 (router 打分) + 示例问法 (联想)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional


@dataclass
class TemplateResult:
    columns: list[dict]                 # [{name,label,kind}]
    rows: list[list]
    chart: dict
    caliber_notes: list[str]
    sql: Optional[str] = None


@dataclass(frozen=True)
class Template:
    key: str
    cn: str
    keywords: tuple[str, ...]           # router 关键词打分
    examples: tuple[str, ...]           # 联想问题
    kind: str                           # service / sql / pointer
    verify_ref: str = ""
    handler: Optional[Callable] = None  # service: (db, time_range)->TemplateResult
    spec: Optional[dict] = None         # sql: 受约束 spec
    default_days: int = 30              # sql: 无时间词时的默认窗口
    caliber_notes: tuple[str, ...] = ()
    pointer: str = ""                   # pointer: 指向哪个报表页


# ------------------------------- service 处理器 ------------------------------- #

def _default_month(tr):
    if tr is not None:
        return tr.start, tr.end, tr.label
    today = date.today()
    return date(today.year, today.month, 1), today, "本月"


def _h_net_profit(db, tr) -> TemplateResult:
    from app.services import order_financials as ofin
    start, end, label = _default_month(tr)
    s = ofin.accounting_summary(db, start, end)
    rows = [
        ["净利润", float(s["net"])],
        ["营收(实付−退款)", float(s["revenue"])],
        ["逐单成本", float(s["order_cost"])],
        ["区间费用(推广+人员+固定+补单)", float(s["period_cost"])],
        ["总成本", float(s["total_cost"])],
        ["净利率%", round(float(s["net_margin"]), 2)],
    ]
    return TemplateResult(
        columns=[{"name": "项目", "label": "项目", "kind": "category"},
                 {"name": "金额", "label": "金额(元/%)", "kind": "number"}],
        rows=rows, chart={"type": "table"},
        caliber_notes=[f"区间 {start}~{end} ({label})", "月度P&L 同口径: 排补单/关闭单不计成交",
                       "成本含物流/安装/平台扣点/税/售后 + 推广/人员/固定/补单成本"],
    )


def _h_product_rank(metric: str):
    def _run(db, tr) -> TemplateResult:
        from app.services import sales_analytics as sa
        r = sa.product_ranking(db, metric=metric, limit=10)
        ranking = r.get("ranking", [])
        if metric == "profit":
            cols = [{"name": "产品", "label": "产品", "kind": "category"},
                    {"name": "利润率%", "label": "利润率%", "kind": "number"},
                    {"name": "利润额", "label": "利润额(元)", "kind": "number"}]
            rows = [[x["product_name"], round(x["profit_rate"] * 100, 2), round(x["net_profit"], 2)]
                    for x in ranking]
        else:
            cols = [{"name": "产品", "label": "产品", "kind": "category"},
                    {"name": "销售额", "label": "销售额(元)", "kind": "number"},
                    {"name": "销量", "label": "销量", "kind": "number"}]
            rows = [[x["product_name"], round(x["revenue"], 2), int(x["qty"])] for x in ranking]
        return TemplateResult(
            columns=cols, rows=rows,
            chart={"type": "bar", "x": "产品", "y": cols[1]["name"], "orient": "horizontal", "order": "desc"},
            caliber_notes=[f"周期: {r.get('selected_period')}", "与利润率榜/逐单核对同口径(排补单/非产品)",
                           "净利=实付−退款−会计总成本" if metric == "profit" else "销售额=实付−退款"],
        )
    return _run


def _h_stock_advice(db, tr) -> TemplateResult:
    from app.services import sales_analytics as sa
    r = sa.stock_advice(db)
    prods = [p for p in r.get("products", []) if p.get("need_to_produce", 0) > 0][:15]
    rows = [[p["product_name"], int(p["forecast_30d"]), float(p["in_stock"]),
             float(p.get("in_production_free", 0)), int(p["need_to_produce"])] for p in prods]
    return TemplateResult(
        columns=[{"name": "产品", "label": "产品", "kind": "category"},
                 {"name": "30天预测", "label": "30天预测", "kind": "number"},
                 {"name": "现货", "label": "现货", "kind": "number"},
                 {"name": "备货在产", "label": "备货在产", "kind": "number"},
                 {"name": "需生产", "label": "需生产", "kind": "number"}],
        rows=rows, chart={"type": "table"},
        caliber_notes=["R1 口径: 需生产=max(预测−现货−自由在产,0)", "客户单在产不抵未来缺口"],
    )


def _sql(metric, dims, *, order="desc", top_n=100):
    return {"metric": metric, "dimensions": dims, "order": order, "top_n": top_n}


# ------------------------------- 20 模板 ------------------------------- #
TEMPLATES: list[Template] = [
    Template("monthly_net_profit", "本月/上月净利润", ("净利", "利润", "赚了", "盈利", "净利润"),
             ("本月净利润是多少", "上月盈利多少"), "service", "月度经营页", handler=_h_net_profit),
    Template("product_margin_rank", "产品毛利率排行", ("毛利率", "利润率", "利润排行", "哪个产品赚"),
             ("产品毛利率排行", "哪个产品利润率最高"), "service", "利润率榜",
             handler=_h_product_rank("profit")),
    Template("margin_drop", "毛利率环比掉最多", ("环比", "掉最多", "下滑", "利润下降"),
             ("哪个产品毛利率环比掉最多",), "pointer", "利润率榜",
             pointer="请到「利润率榜」选两个周期对比 (环比归因 P2 后置)"),
    Template("product_revenue_rank", "产品销售额排行", ("销售额排行", "卖得最好", "销量排行", "热销"),
             ("卖得最好的产品", "销售额排行榜"), "service", "产品总表",
             handler=_h_product_rank("revenue")),
    Template("refund_rate_trend", "退款率趋势(月)", ("退款率", "退货率"),
             ("退款率趋势", "每月退款率"), "sql", "退款报表",
             spec=_sql("refund_rate", ["month"], order="asc"), default_days=180,
             caliber_notes=("退款率=Σ退款/Σ实付, 成交单口径",)),
    Template("refund_top_product", "退款金额TOP产品", ("退款最多", "退款金额", "哪个产品退款"),
             ("退款最多的产品", "本月退款金额TOP"), "sql", "退款报表",
             spec=_sql("refund_amount", ["product"]), default_days=30),
    Template("aov_trend", "客单价趋势", ("客单价", "平均订单", "单均"),
             ("客单价趋势", "每月客单价"), "sql", "月度经营页",
             spec=_sql("aov", ["month"], order="asc"), default_days=180),
    Template("ad_roi", "广告花费与真实ROI", ("广告", "roi", "投放", "直通车", "万相台"),
             ("广告真实ROI",), "pointer", "-",
             pointer="广告数据尚未接入 (依赖 Plan3 淘宝广告投放自动化落库后开放)"),
    Template("restock_advice", "需生产/备货建议", ("备货", "需生产", "要生产", "补货", "生产建议"),
             ("需要生产哪些产品", "备货建议"), "service", "备货页", handler=_h_stock_advice),
    Template("product_sales", "某产品近30天销量与销售额", ("某产品销量", "这个产品卖", "产品销售明细"),
             ("餐边柜近30天卖了多少",), "sql", "产品总表",
             spec=_sql("net_revenue", ["product"]), default_days=30),
    Template("ship_stats", "本月发货单数与金额", ("发货", "出货", "发了多少单", "发货金额"),
             ("本月发货多少单", "发货金额"), "sql", "发货报表",
             spec=_sql("ship_amount", ["month"], order="asc"), default_days=90,
             caliber_notes=("时间按发货日 ship_date",)),
    Template("custom_ratio", "定制单占比与均价", ("定制单占比", "定制占比", "定制均价"),
             ("定制单占比多少",), "pointer", "定制成本v2",
             pointer="定制单成本含决策树/floor, 口径复杂 → 请到「定制成本」页 (半生成/直出禁用防错数)"),
    Template("promo_compare", "大促窗口销售对比", ("618", "双11", "双十一", "大促"),
             ("618卖了多少",), "pointer", "月度经营页",
             pointer="大促窗口起止日期待用户拍板配置后开放 (settings: chatbi_promo_windows)"),
    Template("channel_recon", "各渠道收款对账差异", ("对账", "收款差异", "渠道对账", "收款对账"),
             ("各渠道对账差异",), "pointer", "对账中心页",
             pointer="收款对账口径复杂(含微信/消费券/垫付) → 请到「月结对账中心」页"),
    Template("monthly_fees", "月度物流费/打包费", ("物流费", "打包费", "运费合计"),
             ("本月物流费多少", "打包费合计"), "pointer", "月结对账中心页",
             pointer="物流/打包费走供应商月结 AP 口径 → 请到「月结对账中心」页"),
    Template("supplier_payable", "供应商月结应付", ("应付", "供应商结算", "月结应付"),
             ("供应商应付多少",), "pointer", "月结对账中心页",
             pointer="供应商应付 AP 口径 → 请到「月结对账中心」页"),
    Template("refill_stats", "补单(刷单)本月笔数与金额", ("补单", "刷单", "假单"),
             ("本月补单多少", "刷单金额"), "sql", "刷单台账",
             spec=_sql("refill_gmv", ["month"], order="asc"), default_days=90,
             caliber_notes=("⚠对账口径, 非经营数字",)),
    Template("npd_perf", "新品上架后表现", ("新品", "npd", "新款表现"),
             ("新品上架后卖得怎样",), "pointer", "NPD看板",
             pointer="新品表现关联 NPD 项目 → 请到「新品开发」看板"),
    Template("order_detail", "某订单状态/成本明细(按订单号)", ("订单号", "这个订单", "查订单"),
             ("查订单 PS20250601 的状态",), "pointer", "订单页",
             pointer="按订单号查单据明细 → 请到「订单」页搜订单号 (行级明细 P1 接入, 避免聚合口径误答)"),
    Template("today_deals", "今日成交", ("今天成交", "今日", "当日成交", "今天卖"),
             ("今天成交多少",), "sql", "当日订单",
             spec=_sql("net_revenue", []), default_days=1,
             caliber_notes=("数据截至最近取数完成时间(取数 18:00)",)),
]

TEMPLATES_BY_KEY: dict[str, Template] = {t.key: t for t in TEMPLATES}


def suggestions(limit: int = 8) -> list[str]:
    """联想问题池 (各模板首个示例问法轮换)。"""
    out = []
    for t in TEMPLATES:
        if t.kind == "pointer":
            continue
        if t.examples:
            out.append(t.examples[0])
        if len(out) >= limit:
            break
    return out
