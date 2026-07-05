# -*- coding: utf-8 -*-
"""ChatBI 图表自动选择 (Plan4 v2 §4.7) —— 确定性规则, 不用模型。

输入: 结果列的形状 (每列 name + kind: time/number/category) + 可选行数 + 问句(取意图关键词)。
输出: 前端 echarts 渲染指令 {type, ...}。永远的兜底是 table。
对标 WrenAI text-to-chart / Quick BI 的"结果形状→图型"映射。
"""
from __future__ import annotations

from typing import Optional

_TREND_KW = ("趋势", "走势", "变化", "增长", "曲线")
_RANK_KW = ("排名", "排行", "top", "TOP", "最高", "最多", "最大", "最低", "最少", "前几", "榜")
_SHARE_KW = ("占比", "构成", "比例", "分布", "组成", "份额")

_PIE_MAX_SLICES = 7


def _intent(question: str) -> Optional[str]:
    q = question or ""
    if any(k in q for k in _SHARE_KW):
        return "share"
    if any(k in q for k in _RANK_KW):
        return "rank"
    if any(k in q for k in _TREND_KW):
        return "trend"
    return None


def pick_chart(columns: list[dict], row_count: Optional[int] = None,
               question: str = "") -> dict:
    """列形状 → 图表指令。columns 每项 {"name","kind"} kind∈{time,number,category}。"""
    times = [c["name"] for c in columns if c.get("kind") == "time"]
    nums = [c["name"] for c in columns if c.get("kind") == "number"]
    cats = [c["name"] for c in columns if c.get("kind") == "category"]
    intent = _intent(question)

    # 1 行 1(+) 数值, 无时间/分类 → KPI 大数字卡
    if row_count == 1 and nums and not times and not cats:
        return {"type": "kpi", "values": nums}

    # 有时间列 → 折线 (有分类列则多序列)
    if times and nums:
        chart = {"type": "line", "x": times[0], "y": nums}
        if cats:
            chart["series"] = cats[0]
        return chart

    # 分类 + 数值
    if cats and nums:
        few = row_count is None or row_count <= _PIE_MAX_SLICES
        if intent == "share" and few:
            return {"type": "pie", "name": cats[0], "value": nums[0]}
        # 排行/对比 默认横向条形, 降序
        return {"type": "bar", "x": cats[0], "y": nums[0], "orient": "horizontal", "order": "desc"}

    # 两个纯数值列 → 散点 (相关性)
    if len(nums) >= 2 and not cats and not times:
        return {"type": "scatter", "x": nums[0], "y": nums[1]}

    # 兜底: 表格
    return {"type": "table"}
