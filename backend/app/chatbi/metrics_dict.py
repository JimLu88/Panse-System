# -*- coding: utf-8 -*-
"""ChatBI 指标字典 (迷你语义层, Plan4 v2 §4.1) —— 口径的唯一定义处。

对标 dbt Semantic Layer / 腾讯 supersonic: 让 LLM 做"选指标"的选择题, 而不是"写 SQL"
的问答题。每个指标声明: 聚合式 + 内置口径过滤 + 时间字段 + 可用维度 + 口径出处 + 对照基准。

⚠ 关键分层 (来自 order_financials.py 核实):
  - **可 SQL 直算** (net_revenue/order_count/refund…): 简单聚合, 半生成/直出可用。
  - **service_only** (net_profit/gross_margin…): 净利/毛利是逐单 Python 循环(系数/售后均值/
    定制成本决策树/floor 封顶), SQL 无法复刻 → 只能走模板复用现有 service, 半生成/直出**禁用**,
    宁可拒答不给错数。

内置过滤口径 (与 sales_analytics.settled_sale_clause / order_financials 一致):
  - is_settled_sale: 已付款成交·非取消关闭·未全额退款·非0元服务行 (视图已物化)。
  - is_refill=FALSE: 补单/刷单排除出一切经营数字 (唯 refill_* 指标反选)。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.chatbi.catalog import ALLOWED_VIEWS, VIEW_COLUMNS

# ---- 内置口径过滤片段 (引用视图已物化的布尔列) ----
F_SETTLED = "is_settled_sale = TRUE"     # 真实成交 (settled_sale_clause 口径)
F_NOT_REFILL = "is_refill = FALSE"       # 排除补单/刷单 (经营口径铁律)
F_REFILL = "is_refill = TRUE"            # 仅补单 (对账口径)
F_SHIPPED = "ship_date IS NOT NULL"      # 已发货 (发货口径)


@dataclass(frozen=True)
class Metric:
    key: str
    cn: str                                   # 中文名
    base_view: str                            # 数据来源视图
    time_field: str                           # 时间过滤字段 (order_date / ship_date / sale_day)
    caliber_ref: str                          # 口径出处 (供答案"口径说明")
    verify_ref: str                           # 对照基准页 (对数验收)
    unit: str = ""                            # 元 / 单 / %
    agg_sql: str | None = None                # 聚合表达式 (service_only 时为 None)
    builtin_filters: tuple[str, ...] = ()     # 内置口径过滤 (WHERE 片段)
    dims: tuple[str, ...] = ()                # 允许的拆解维度 (锁死钻取范围)
    service_only: bool = False                # True=只能走 Python service, 半生成/直出禁用
    service_hint: str = ""                    # service_only 时: 复用哪个 service 函数


# 维度 → SQL 表达式 (半生成 assembler 用)。{t}=指标的 time_field。
DIMENSIONS: dict[str, dict] = {
    "month":    {"sql": "to_char({t}, 'YYYY-MM')", "label": "月份", "kind": "time"},
    "day":      {"sql": "{t}::date",               "label": "日期", "kind": "time"},
    "product":  {"sql": "product_name",            "label": "产品", "kind": "category"},
    "sku":      {"sql": "sku_code",                "label": "SKU",  "kind": "category"},
    "platform": {"sql": "platform",                "label": "平台", "kind": "category"},
    "shop":     {"sql": "shop",                    "label": "店铺", "kind": "category"},
}

_COMMON_DIMS = ("month", "day", "product", "sku", "platform", "shop")

# ── 指标注册表 ──────────────────────────────────────────────────────────────
METRICS: dict[str, Metric] = {m.key: m for m in [
    # —— 可 SQL 直算 (半生成/直出可用) ——
    Metric("net_revenue", "净营收", "chatbi_v_orders", "order_date",
           "营收 = Σ(实付−退款), 排补单/仅真实成交 (口径§一)", "月度经营页/产品总表", "元",
           agg_sql="SUM(paid_amount - COALESCE(refund_amount, 0))",
           builtin_filters=(F_SETTLED, F_NOT_REFILL), dims=_COMMON_DIMS),
    Metric("order_count", "成交订单数", "chatbi_v_orders", "order_date",
           "真实成交单数, 排补单 (口径§一)", "月度经营页", "单",
           agg_sql="COUNT(*)", builtin_filters=(F_SETTLED, F_NOT_REFILL), dims=_COMMON_DIMS),
    Metric("gross_paid", "实付金额(未扣退款)", "chatbi_v_orders", "order_date",
           "Σ实付, 未扣退款; 仅做客单价/退款率分母", "月度经营页", "元",
           agg_sql="SUM(paid_amount)", builtin_filters=(F_SETTLED, F_NOT_REFILL), dims=_COMMON_DIMS),
    Metric("refund_amount", "退款金额", "chatbi_v_orders", "order_date",
           "Σ退款额, 成交单口径", "退款报表", "元",
           agg_sql="SUM(COALESCE(refund_amount, 0))",
           builtin_filters=(F_SETTLED, F_NOT_REFILL), dims=_COMMON_DIMS),
    Metric("refund_rate", "退款率", "chatbi_v_orders", "order_date",
           "退款率 = Σ退款 / Σ实付 (成交单口径)", "退款报表", "%",
           agg_sql="SUM(COALESCE(refund_amount,0)) / NULLIF(SUM(paid_amount), 0)",
           builtin_filters=(F_SETTLED, F_NOT_REFILL), dims=("month", "day", "platform", "shop")),
    Metric("aov", "客单价", "chatbi_v_orders", "order_date",
           "客单价 = Σ(实付−退款) / 成交单数", "月度经营页(推算)", "元",
           agg_sql="SUM(paid_amount - COALESCE(refund_amount,0)) / NULLIF(COUNT(*), 0)",
           builtin_filters=(F_SETTLED, F_NOT_REFILL), dims=("month", "day", "platform", "shop")),
    Metric("ship_count", "发货单数", "chatbi_v_orders", "ship_date",
           "已发货单数, 时间按发货日 (发货口径)", "发货报表", "单",
           agg_sql="COUNT(*)", builtin_filters=(F_SETTLED, F_NOT_REFILL, F_SHIPPED),
           dims=("month", "day", "platform", "shop", "product")),
    Metric("ship_amount", "发货金额", "chatbi_v_orders", "ship_date",
           "已发货单实付合计, 时间按发货日", "发货报表", "元",
           agg_sql="SUM(paid_amount)", builtin_filters=(F_SETTLED, F_NOT_REFILL, F_SHIPPED),
           dims=("month", "day", "platform", "shop", "product")),
    # —— 补单(刷单)对账口径, 单独标注, 唯一反选 is_refill ——
    Metric("refill_count", "补单(刷单)笔数", "chatbi_v_orders", "order_date",
           "⚠对账口径, 非经营数字; 仅补单", "刷单台账", "单",
           agg_sql="COUNT(*)", builtin_filters=(F_REFILL,), dims=("month", "day", "platform", "shop")),
    Metric("refill_gmv", "补单(刷单)流水", "chatbi_v_orders", "order_date",
           "⚠对账口径, 非经营数字; 补单实付合计", "刷单台账", "元",
           agg_sql="SUM(paid_amount)", builtin_filters=(F_REFILL,), dims=("month", "day", "platform", "shop")),

    # —— service_only: 净利/毛利是 Python 逐单口径, SQL 不可复刻 → 半生成/直出禁用 ——
    Metric("net_profit", "净利润", "chatbi_v_orders", "order_date",
           "净利=实付−退款−会计总成本(商品+物流+安装+平台扣点+税+售后)−区间费用(推广+人员+固定+补单成本); 逐单Python口径",
           "月度经营页", "元", service_only=True,
           service_hint="order_financials.accounting_summary(db, start, end)"),
    Metric("gross_margin_rate", "毛利率", "chatbi_v_orders", "order_date",
           "毛利率=(销售额−物理产品成本)/销售额; 物理成本含定制决策树/封顶, 逐单Python口径",
           "产品总表/利润率榜", "%", service_only=True,
           service_hint="sales_analytics.product_breakdown(db, start, end)"),
    Metric("product_profit_rank", "产品利润率排行", "chatbi_v_orders", "order_date",
           "按净利率排序; 与逐单核对/月度P&L 同口径 (order_financials.net_profit)",
           "利润率榜", "%", service_only=True,
           service_hint="sales_analytics.product_ranking(db, metric='profit')"),
]}


def get(key: str) -> Metric | None:
    return METRICS.get(key)


def sql_metric_keys() -> list[str]:
    """可 SQL 直算的指标 key (供半生成 LLM 的枚举清单)。"""
    return [k for k, m in METRICS.items() if not m.service_only]


def service_metric_keys() -> list[str]:
    return [k for k, m in METRICS.items() if m.service_only]


def dim_sql(dim: str, time_field: str) -> str:
    """维度 → SQL 表达式 (填入指标的 time_field)。"""
    return DIMENSIONS[dim]["sql"].format(t=time_field)
