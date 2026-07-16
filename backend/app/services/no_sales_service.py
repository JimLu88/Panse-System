"""无动销商品登记 (2026-07-17 用户拍板: 动销不达标的品报不进大促活动 →
挂单品立减把到手打到【中促价 − 1 元】, 永久规则, 对无动销品永远这么定)。

存 system_settings 键 `no_sales_item_ids` = JSON [taobao_item_id, ...] (商品维度——
平台动销校验是商品级"近60天销量≥1")。
- 来源①: 报名回执"动销不达标"自动登记(自愈);
- 来源②: 手动登记/移除(卖出转正、重报成功后移除);
- 单品立减 nosales builder 只对登记的商品出行。

镜像 delisted_sku_service 的存储与自愈模式。
"""
from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy.orm import Session

_KEY = "no_sales_item_ids"
_NO_SALES_MARKERS = ("动销", "销售件数≥1", "销售件数&ge;1")


def get_no_sales(db: Session) -> set[str]:
    """当前登记的无动销商品 item_id 集合。"""
    from app.services import settings_service
    raw = settings_service.get(db, _KEY, env_fallback=False)
    try:
        items = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        items = []
    return {str(x).strip() for x in items if str(x).strip()}


def _save(db: Session, ids: set[str]) -> None:
    from app.services import settings_service
    settings_service.set_value(
        db, _KEY, json.dumps(sorted(ids), ensure_ascii=False),
        description="无动销商品登记(报不进大促 → 单品立减到手=中促价−1)")
    db.commit()


def add_no_sales(db: Session, item_ids: Iterable[str]) -> set[str]:
    """登记无动销商品(并集)。返回登记后的全集。无变化不写库。"""
    cur = get_no_sales(db)
    new = cur | {str(x).strip() for x in (item_ids or []) if str(x).strip()}
    if new != cur:
        _save(db, new)
    return new


def remove_no_sales(db: Session, item_ids: Iterable[str]) -> set[str]:
    """移除登记(卖出转正/重报成功后)。返回剩余全集。"""
    cur = get_no_sales(db)
    new = cur - {str(x).strip() for x in (item_ids or [])}
    if new != cur:
        _save(db, new)
    return new


def extract_no_sales_from_feedback(failed_items) -> set[str]:
    """从报名失败明细抽"动销不达标"的商品 item_id(供自愈登记)。
    failed_items = [{item_id, sku_id, reason, raw}, ...]。"""
    out: set[str] = set()
    for it in failed_items or []:
        raw = str((it or {}).get("raw") or "") + " " + str((it or {}).get("reason") or "")
        if any(m in raw for m in _NO_SALES_MARKERS):
            iid = str((it or {}).get("item_id") or "").strip()
            if iid:
                out.add(iid)
    return out
