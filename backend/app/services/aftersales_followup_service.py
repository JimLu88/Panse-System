"""售后超时智能追踪推送 (Feature 3).

功能:
- 找未解决 (status 非 resolved/closed) 且未处理 (processed_at 为空或超 3 天) 的售后记录
- 按原因分组, 给出智能建议
- 通过 notify_service 推送
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.marketing import AfterSales
from app.services import notify_service

_logger = logging.getLogger("panse.aftersales_followup")

# 原因 → 建议操作映射
_REASON_ACTION_MAP: dict[str, str] = {
    "安装损坏": "联系万师傅核实安装记录，确认是否需要二次上门",
    "安装问题": "联系万师傅核实安装记录，确认是否需要二次上门",
    "物流损坏": "联系物流公司索赔，填写物流赔偿金额",
    "快递损坏": "联系物流公司索赔，填写物流赔偿金额",
    "产品质量": "联系工厂确认责任，填写工厂赔付",
    "质量问题": "联系工厂确认责任，填写工厂赔付",
    "好评返现": "核对支付宝流水，完成返款并填写流水号",
    "差价返": "核对支付宝流水，完成返款并填写流水号",
    "好评/差价返": "核对支付宝流水，完成返款并填写流水号",
    "补发": "在千牛发出补发订单，填写补发运单号",
    "漏发": "在千牛发出补发订单，填写补发运单号",
    "商品破损": "拍照留存，联系物流/工厂确认责任，填写赔偿金额",
    "尺寸问题": "核对订单尺寸要求，联系工厂确认是否需要重新生产",
    "颜色问题": "核对订单颜色要求，联系工厂确认是否需要重新生产",
    "退货退款": "确认退货物流情况，收货后退款并更新订单状态",
    "客诉": "与客户沟通了解诉求，给出解决方案并记录处理详情",
}

_DEFAULT_ACTION = "请核实情况并录入处理详情"
_OVERDUE_DAYS = 3
# 不需要人工跟进的状态白名单 —— 这些不算超时待处理。
# 用户 2026-07-03 踩坑: 实际数据 status 是中文「已完成」(导入的), 但原来只认英文
# resolved/closed → 857 条已完成的被误报成待处理, 把提醒从 ~237 撑成 1091。补齐中文值。
# auto/auto_linked 是系统从平台退款、支付宝流水和万师傅记录自动生成并已处理/关联的
# 财务售后台账。它们用于对账追溯，不是等待人工处理的售后案件。
_DONE_STATUSES = (
    "resolved",
    "closed",
    "已完成",
    "已解决",
    "已处理",
    "auto",
    "auto_linked",
)


def _get_suggested_action(reason: str) -> str:
    """根据原因返回建议操作。支持模糊匹配（包含关键词）。"""
    if not reason:
        return _DEFAULT_ACTION
    for key, action in _REASON_ACTION_MAP.items():
        if key in reason:
            return action
    return _DEFAULT_ACTION


def check_and_push(db: Session) -> dict:
    """查找超时未处理的售后记录, 分组推送.

    返回:
        {overdue_count, pushed}
    """
    cutoff = date.today() - timedelta(days=_OVERDUE_DAYS)

    # 只追踪真正待人工处理的记录；自动生成/已关联台账不进入提醒。
    stmt = select(AfterSales).where(
        or_(
            AfterSales.status.is_(None),
            ~AfterSales.status.in_(_DONE_STATUSES),
        ),
        or_(
            AfterSales.processed_at.is_(None),
            AfterSales.processed_at <= cutoff,
        ),
    )
    overdue_records = db.execute(stmt).scalars().all()

    if not overdue_records:
        return {"overdue_count": 0, "pushed": False}

    # 按原因分组
    reason_groups: dict[str, list[AfterSales]] = {}
    for record in overdue_records:
        reason = record.reason or "未填写原因"
        reason_groups.setdefault(reason, []).append(record)

    # 格式化消息
    lines = [
        f"⚠️ 售后超时提醒: 共 {len(overdue_records)} 条待处理",
        "",
    ]
    for reason, records in sorted(reason_groups.items(), key=lambda x: -len(x[1])):
        action = _get_suggested_action(reason)
        lines.append(f"【{reason}】 {len(records)} 条")
        lines.append(f"  建议: {action}")
        # 列出部分订单号示例
        sample_nos = [r.platform_order_no for r in records[:3]]
        lines.append(f"  示例: {', '.join(sample_nos)}")
        lines.append("")

    msg = "\n".join(lines).rstrip()
    notify_service.notify(db, msg, level="warn", title="畔色ERP | 售后超时追踪提醒")

    return {"overdue_count": len(overdue_records), "pushed": True}
