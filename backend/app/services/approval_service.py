"""审批工作流 service + 执行器 (Phase 11, 完成 Tier 2 #4).

业务: 高风险操作 (大订单折扣 / 库存大调整 / 大额退款) → 待审批 → 主管批后才生效.

设计:
    create_request(db, kind, target, payload, requester) -> ApprovalRequest
    approve(db, request_id, approver) -> 执行 payload 里的动作
    reject(db, request_id, approver, reason)

payload 里描述具体改动:
    {"action": "order_discount", "order_id": 1, "new_amount": 5000}
    {"action": "inventory_adjust", "material_code": "M1", "new_physical": 50}

执行动作的注册表 _EXECUTORS 把 payload kind 映射到真实业务函数.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest
from app.services import alert_service

_logger = logging.getLogger("panse.approval")

# 注册表: action 名 -> 执行函数 (db, payload, approver) -> dict
_EXECUTORS: dict[str, Callable] = {}


def register_executor(action: str, fn: Callable) -> None:
    _EXECUTORS[action] = fn


# ----------------------------- 创建 / 列出 ---------------------- #


def create_request(
    db: Session, *, kind: str, title: str,
    payload: dict, requester: str,
    detail: Optional[str] = None,
    target_table: Optional[str] = None, target_id: Optional[int] = None,
) -> ApprovalRequest:
    req = ApprovalRequest(
        kind=kind, target_table=target_table, target_id=target_id,
        title=title, detail=detail, payload_json=payload,
        status="pending", requested_by=requester,
    )
    db.add(req)
    db.flush()
    # 通知 alert 让主管看
    alert_service.upsert(
        db, kind="approval_pending", severity="warn",
        title=f"待审批: {title}",
        body=f"{requester} 提交了一个 {kind} 类型的审批请求.",
        dedupe_key=f"approval_pending:{req.id}",
        related_url=f"/approvals?id={req.id}",
        context={"request_id": req.id, "kind": kind, "requester": requester},
        sticky=True,
    )
    return req


def approve(
    db: Session, request_id: int, *, approver: str,
) -> ApprovalRequest:
    req = db.get(ApprovalRequest, request_id)
    if req is None:
        raise ValueError("审批请求不存在")
    if req.status != "pending":
        raise ValueError(f"已 {req.status}, 不可重复审批")
    if req.requested_by == approver:
        raise ValueError("不能批准自己的请求")

    # 执行动作
    payload = req.payload_json or {}
    action = payload.get("action")
    if action and action in _EXECUTORS:
        try:
            _EXECUTORS[action](db, payload, approver)
        except Exception as e:
            _logger.exception("审批执行失败: %s", e)
            raise ValueError(f"执行失败: {e}")
    req.status = "approved"
    req.approver = approver
    req.approved_at = datetime.now(timezone.utc)
    alert_service.resolve_by_dedupe(
        db, f"approval_pending:{req.id}", resolved_by=approver,
    )
    return req


def reject(
    db: Session, request_id: int, *, approver: str, reason: str,
) -> ApprovalRequest:
    req = db.get(ApprovalRequest, request_id)
    if req is None:
        raise ValueError("审批请求不存在")
    if req.status != "pending":
        raise ValueError(f"已 {req.status}")
    req.status = "rejected"
    req.approver = approver
    req.approved_at = datetime.now(timezone.utc)
    req.reject_reason = reason
    alert_service.resolve_by_dedupe(
        db, f"approval_pending:{req.id}", resolved_by=approver,
    )
    return req


def list_requests(
    db: Session, *, status: Optional[str] = None, limit: int = 100,
) -> list[ApprovalRequest]:
    q = select(ApprovalRequest).order_by(ApprovalRequest.id.desc()).limit(limit)
    if status:
        q = q.where(ApprovalRequest.status == status)
    return list(db.execute(q).scalars())


# ----------------------------- 内置执行器 ----------------------- #


def _exec_inventory_adjust(db: Session, payload: dict, approver: str) -> dict:
    """approve 后真正改库存."""
    from app.services import inventory_lock_service
    mat = payload["material_code"]
    new_qty = Decimal(str(payload["new_physical"]))
    inventory_lock_service.manual_adjust(
        db, material_code=mat, new_physical=new_qty,
        actor=approver, remark=f"审批通过 #{payload.get('request_id', '?')}",
    )
    return {"material_code": mat, "new_physical": float(new_qty)}


def _exec_order_discount(db: Session, payload: dict, approver: str) -> dict:
    """approve 后给订单改实付金额 (大折扣场景)."""
    from app.models.order import Order
    o = db.get(Order, payload["order_id"])
    if o is None:
        raise ValueError("订单不存在")
    o.paid_amount = Decimal(str(payload["new_amount"]))
    o.remark = (o.remark or "") + f"\n[审批 by {approver}: 折扣到 {payload['new_amount']}]"
    return {"order_id": o.id, "new_amount": float(o.paid_amount)}


register_executor("inventory_adjust", _exec_inventory_adjust)
register_executor("order_discount", _exec_order_discount)


# ----------------------------- 自动提示触发器 ------------------- #


# 业务规则: 这些动作如果金额/数量超阈值, 应该走审批
APPROVAL_THRESHOLDS = {
    "order_discount_amount": Decimal("500"),    # 折扣 > 500 元
    "inventory_adjust_qty": Decimal("100"),      # 库存调 > 100 件
    "refund_amount": Decimal("1000"),
}


def requires_approval_for_discount(original: Decimal, new: Decimal) -> bool:
    return abs(original - new) > APPROVAL_THRESHOLDS["order_discount_amount"]


def requires_approval_for_inventory(delta_abs: Decimal) -> bool:
    return delta_abs > APPROVAL_THRESHOLDS["inventory_adjust_qty"]
