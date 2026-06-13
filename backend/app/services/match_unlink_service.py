"""核销状态双向一致 (Plan L8).

业务行解绑/删除其 alipay_flow_no 时, 反查该流水号是否还被其他业务行引用:
  - 仍有引用 (拆分付款: 一条流水多单) → 不动流水
  - 无引用 → AlipayFlow.reconciliation_status 置回 'open' + 清 reconciliation_type,
    并记 DataException 审计 (flow_unlinked)。

调用约定: 先把业务行的解绑/删除 flush 进会话, 再调 unlink_*。
"""
from __future__ import annotations

import logging
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.marketing import AfterSales, DailyOperation, OutsourcingExpense, PromotionFlow
from app.models.order import FactoryOrder, Order, PartPurchase

_logger = logging.getLogger("panse.match_unlink")

# 所有带 alipay_flow_no 的业务表 (核销写入点的镜像清单)
_REFERENCING_MODELS = (
    Order, PartPurchase, PromotionFlow, OutsourcingExpense,
    DailyOperation, AfterSales, FactoryOrder,
)


def reference_count(db: Session, flow_no: str) -> int:
    """该流水号当前被多少业务行引用 (跨 7 张表)。"""
    total = 0
    for model in _REFERENCING_MODELS:
        col = getattr(model, "alipay_flow_no", None)
        if col is None:  # pragma: no cover - 模型字段变更兜底
            continue
        total += int(db.execute(
            select(func.count()).select_from(model).where(col == flow_no)
        ).scalar() or 0)
    return total


def unlink_flow(db: Session, flow_no: str, *, source: str) -> bool:
    """业务行解绑后调用: 无其他引用 → 流水回 open。返回是否真的解锁。"""
    flow_no = (flow_no or "").strip()
    if not flow_no:
        return False
    if reference_count(db, flow_no) > 0:
        return False   # 拆分付款场景: 还有别的单引用, 不解锁
    flows = db.execute(
        select(AlipayFlow).where(AlipayFlow.transaction_no == flow_no)
    ).scalars().all()
    changed = False
    for f in flows:
        if f.reconciliation_status == "matched":
            f.reconciliation_status = "open"
            f.reconciliation_type = None
            changed = True
    if changed:
        try:
            from app.services import exception_service
            exception_service.record(
                db, source_table="alipay_flows", source_pk=flow_no,
                exception_type="flow_unlinked", severity="info",
                description=f"{source} 解绑流水 {flow_no}, 已无业务行引用 → 核销状态回 open",
            )
        except Exception:  # pragma: no cover - 审计失败不阻断解锁
            _logger.warning("flow_unlinked 审计写入失败 %s", flow_no, exc_info=True)
        db.flush()
    return changed


def unlink_order(db: Session, flow_no: str) -> bool:
    """订单侧解绑 (订单编辑清空流水号/删除订单后调用)。"""
    return unlink_flow(db, flow_no, source="订单")


def unlink_purchase(db: Session, flow_no: str) -> bool:
    """采购侧解绑 (采购记录删除/清理后调用)。"""
    return unlink_flow(db, flow_no, source="配件采购")
