# -*- coding: utf-8 -*-
"""ChatBI 白名单视图 + 字段目录 (Plan4 v2 §4.2) —— LLM 唯一可见面。

只读角色 chatbi_ro 只 GRANT SELECT 于这些 chatbi_v_* 视图 (连基表都不给)。视图在
migration 里建 (0119_chatbi.py), 已剔除收件人/电话/地址等敏感列。本模块是"真源":
  ALLOWED_VIEWS  —— sql_gate 的表白名单 (LLM 生成/半生成 SQL 只能碰这些)。
  VIEW_COLUMNS   —— M-Schema 半结构化字段目录, 供 AI 直出的 prompt (比裸 DDL 省 token,
                    9B 小模型更吃这套; 来自 XiYan-SQL 的 M-Schema 表示法)。
视图字段务必与 migration 的视图 DDL 保持一致 (改一处改两处; 对数脚本会校验)。
"""
from __future__ import annotations

# 每列: (列名, 类型, 说明/口径)
VIEW_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    # 订单主视图 (脱敏: 无收件人/电话/地址)。is_settled_sale 已在视图里物化 settled_sale_clause 口径。
    "chatbi_v_orders": [
        ("order_no", "text", "订单号 (唯一)"),
        ("order_date", "date", "下单日期 (经营口径按此)"),
        ("ship_date", "date", "发货日期 (发货口径按此)"),
        ("product_code", "text", "产品编码"),
        ("product_name", "text", "产品名 (内部短名优先)"),
        ("sku_code", "text", "SKU 编码"),
        ("qty", "int", "件数"),
        ("paid_amount", "numeric", "买家实付金额 (元)"),
        ("refund_amount", "numeric", "退款金额 (元)"),
        ("status", "text", "状态 pending_payment|paid|shipped|signed|aftersales|cancelled"),
        ("platform", "text", "平台 (淘宝/天猫…)"),
        ("shop", "text", "店铺名"),
        ("is_refill", "bool", "是否补单/刷单 (TRUE=刷单, 经营数字须排除)"),
        ("is_settled_sale", "bool", "是否真实成交 (已付款成交·非取消关闭·未全额退款·非0元服务行)"),
    ],
    # 产品档案 (脱敏; 只给报表需要的列)
    "chatbi_v_products": [
        ("code", "text", "产品编码 (唯一)"),
        ("name", "text", "产品名 (内部短名)"),
        ("sku_code", "text", "SKU 编码"),
        ("category", "text", "分类"),
    ],
    # 日销预聚合 (复用 sales_rollup; 已按真实成交·非补单口径预聚合)
    "chatbi_v_daily_sales": [
        ("sale_day", "date", "销售日 (下单日)"),
        ("product_code", "text", "产品编码"),
        ("product_name", "text", "产品名"),
        ("qty", "int", "当日件数"),
        ("revenue", "numeric", "当日销售额 = Σ(实付−退款)"),
    ],
}

# sql_gate 白名单 (只读角色能 SELECT 的全部对象)
ALLOWED_VIEWS: frozenset[str] = frozenset(VIEW_COLUMNS.keys())


def m_schema(views: "list[str] | None" = None) -> str:
    """渲染 M-Schema 文本 (给 AI 直出 prompt)。格式: 视图名(列:类型 -- 说明)。"""
    lines: list[str] = []
    for view in (views or VIEW_COLUMNS.keys()):
        cols = VIEW_COLUMNS.get(view)
        if not cols:
            continue
        lines.append(f"# {view}")
        for name, typ, note in cols:
            lines.append(f"  {name}: {typ}  -- {note}")
    return "\n".join(lines)
