"""下架 SKU 登记 (2026-07-13 用户铁律: 系统报名必须"在售全报、下架不报")。

存 system_settings 键 `delisted_skuids` = JSON [taobao_sku_id, ...]。
- 报名/单品立减 builder 生成表时**自动排除**这些 skuId(不报下架);
- 报名上传时若千牛回"已下架SKU=X / 不属于当前商品"→ 自动登记 X(自愈), 下次报名不带它;
- 在售的照常全部报名(完整性)。

★为什么用登记表而非"淘宝导出快照在售"判定: 发布模板导出含下架SKU、日常导出口径也不稳; 千牛报名
  回执里的"已下架"才是权威真值 → 以它为准自愈登记, 不依赖导出新鲜度。
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from sqlalchemy.orm import Session

_KEY = "delisted_skuids"
# 千牛"已下架/不属于当前商品"回执里抽真实下架skuId: 文案形如 "SKUID=5024477897620不属于当前商品或已下架"
_SKUID_RE = re.compile(r"SKUID[=:：]\s*(\d{6,})")
_DELISTED_MARKERS = ("已下架", "不属于当前商品", "不支持报名")


def get_delisted(db: Session) -> set[str]:
    """当前登记的下架 skuId 集合。"""
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
        description="下架SKU登记(报名自动排除: 在售全报、下架不报)")
    db.commit()


def add_delisted(db: Session, skuids: Iterable[str]) -> set[str]:
    """登记下架 skuId(并集)。返回登记后的全集。无变化则不写库。"""
    cur = get_delisted(db)
    new = cur | {str(x).strip() for x in (skuids or []) if str(x).strip()}
    if new != cur:
        _save(db, new)
    return new


def remove_delisted(db: Session, skuids: Iterable[str]) -> set[str]:
    """撤销登记(SKU 重新上架时)。返回剩余全集。"""
    cur = get_delisted(db)
    new = cur - {str(x).strip() for x in (skuids or [])}
    if new != cur:
        _save(db, new)
    return new


def extract_delisted_from_feedback(failed_items) -> set[str]:
    """从千牛报名失败明细里抽出"已下架/不属于当前商品"的真实 skuId(供自愈登记)。
    failed_items = [{item_id, sku_id, reason, raw}, ...]; 真实下架号在 raw 的 'SKUID=xxx' 里。"""
    out: set[str] = set()
    for it in failed_items or []:
        raw = str((it or {}).get("raw") or "") + " " + str((it or {}).get("reason") or "")
        if any(m in raw for m in _DELISTED_MARKERS):
            out.update(_SKUID_RE.findall(raw))
    return out
