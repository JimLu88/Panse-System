"""微定制 AI 报价服务 (Module ④).

Accepts an uploaded screenshot image, uses vision AI to extract:
  - base product / sku name
  - target dimensions (width/depth/height in mm)
  - material changes

Then looks up the matching product in the database, finds the closest
PricingSku size_category, and computes an estimated price.

Falls back to a deterministic response when AI is not configured.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import ai_provider as ai_mod, settings_service
from app.services.product_match_service import match


@dataclass
class PriceBreakdownItem:
    label: str
    amount: float
    note: str = ""


@dataclass
class CustomizationAiResult:
    base_product: Optional[str]
    base_sku: Optional[str]
    base_size: Optional[str]
    changes: list[str]
    est_price: Optional[float]
    breakdown: list[PriceBreakdownItem]
    ai_used: bool
    model: Optional[str] = None
    error: Optional[str] = None


_SYSTEM = """\
你是一个家具微定制报价助手。从用户发来的截图中提取以下信息，用 JSON 回答：
{
  "product_name": "产品名称（中文）",
  "sku_text": "SKU 描述（可选）",
  "dimensions": {"width_mm": null, "depth_mm": null, "height_mm": null},
  "material_changes": ["变更描述1", "变更描述2"],
  "notes": "其他备注"
}
只输出 JSON，不要解释。维度单位统一转换为毫米（mm）。"""


def _build_provider(db: Session):
    cfg = settings_service.get_ai_config(db, "primary")
    return ai_mod.build_provider(cfg)


def _parse_json_safe(text: str) -> dict:
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {}


def _size_score(size_category: Optional[str]) -> int:
    """Map size category to a numeric score for comparison."""
    mapping = {"小型": 1, "中型": 2, "大型": 3}
    return mapping.get(size_category or "", 2)


def _estimate_size_category(dims: dict) -> str:
    w = dims.get("width_mm") or 0
    if w >= 1800:
        return "大型"
    if w >= 1200:
        return "中型"
    return "小型"


def _find_base_sku(db: Session, product_code: str, dims: dict) -> Optional[PricingSku]:
    skus = db.query(PricingSku).filter(PricingSku.product_code == product_code).all()
    if not skus:
        return None
    est_cat = _estimate_size_category(dims)
    # prefer matching size_category
    for sku in skus:
        if sku.size_category == est_cat:
            return sku
    return skus[0]


def _compute_price(
    base_sku: Optional[PricingSku],
    dims: dict,
    material_changes: list[str],
    db: Session,
) -> tuple[Optional[float], list[PriceBreakdownItem]]:
    breakdown: list[PriceBreakdownItem] = []

    if base_sku is None:
        return None, breakdown

    base_price = float(base_sku.daily_price or base_sku.list_price or 0)
    if base_price == 0:
        return None, breakdown

    breakdown.append(PriceBreakdownItem(
        label="基础定价",
        amount=base_price,
        note=f"{base_sku.size_category or ''} - {base_sku.sku or ''}",
    ))

    # Dimension adjustment: ±1% per 100mm deviation from nominal 1400mm width
    w = dims.get("width_mm")
    if w and base_sku.size_category:
        nominal = {"小型": 1000, "中型": 1400, "大型": 1800}.get(base_sku.size_category, 1400)
        ratio = (float(w) - nominal) / nominal
        adj = base_price * ratio * 0.5  # half-sensitivity: 50% pass-through
        if abs(adj) > 1:
            breakdown.append(PriceBreakdownItem(
                label="尺寸调整",
                amount=round(adj, 2),
                note=f"{w}mm vs 基准 {nominal}mm",
            ))

    # Material change surcharge: +200/item (rough estimate)
    for change in material_changes:
        # Try to match material name to get unit price
        mats = db.query(Material).filter(
            Material.name.ilike(f"%{change[:6]}%"),
            Material.price.isnot(None),
        ).limit(1).all()
        if mats:
            m = mats[0]
            area = float(m.area or Decimal("1.0"))
            unit_price = float(m.price or 0)
            surcharge = area * unit_price
        else:
            surcharge = 200.0
        breakdown.append(PriceBreakdownItem(
            label=f"物料变更: {change[:20]}",
            amount=round(surcharge, 2),
            note="按面积×单价估算" if mats else "默认估算",
        ))

    total = sum(b.amount for b in breakdown)
    return round(total, 2), breakdown


def ai_quote(db: Session, image_bytes: bytes, mime: str) -> CustomizationAiResult:
    """Main entry point: extract info from screenshot, match product, compute price."""
    # Step 1: Vision AI extraction
    extracted: dict = {}
    ai_used = False
    model_name = None
    error_msg = None

    try:
        prov = _build_provider(db)
        resp = prov.chat_with_image(
            system=_SYSTEM,
            user="请从这张截图中提取定制需求信息，以 JSON 格式返回。",
            image_bytes=image_bytes,
            mime=mime,
            max_tokens=512,
        )
        extracted = _parse_json_safe(resp.text)
        ai_used = True
        model_name = resp.model
    except ai_mod.AiUnavailable as e:
        error_msg = str(e)
    except Exception as e:
        error_msg = f"AI 调用失败: {e}"

    product_name = extracted.get("product_name", "")
    sku_text = extracted.get("sku_text", "")
    dims = extracted.get("dimensions") or {}
    material_changes = extracted.get("material_changes") or []

    # Step 2: Product matching
    match_result = None
    if product_name:
        match_result = match(db, product_name, sku_text or None)

    base_product_code = match_result["product_code"] if match_result else None
    base_product_name = match_result["product_name"] if match_result else product_name or None
    base_sku_desc = match_result["sku"] if match_result else None

    # Step 3: Price estimation
    base_sku = None
    if base_product_code:
        base_sku = _find_base_sku(db, base_product_code, dims)
    est_price, breakdown = _compute_price(base_sku, dims, material_changes, db)

    changes_list = material_changes.copy()
    for k, v in dims.items():
        if v:
            dim_label = {"width_mm": "宽", "depth_mm": "深", "height_mm": "高"}.get(k, k)
            changes_list.insert(0, f"{dim_label} {v}mm")

    return CustomizationAiResult(
        base_product=base_product_name,
        base_sku=base_sku_desc or (base_sku.sku if base_sku else None),
        base_size=base_sku.size_category if base_sku else None,
        changes=changes_list,
        est_price=est_price,
        breakdown=breakdown,
        ai_used=ai_used,
        model=model_name,
        error=error_msg,
    )


_BOARD_SYSTEM = """\
你是家具全定制下料助手。看设计图(含整体尺寸标注), 按每一块板拆解, 用 JSON 回答:
{
  "product_type": "品类(床/床头柜/电视柜/餐边柜/餐桌/斗柜/书柜/...)",
  "overall": {"length_mm": null, "width_mm": null, "height_mm": null},
  "boards": [
    {"part":"部位(如顶板/侧板/层板/门板/抽屉面板/背板)",
     "material":"材料(如 樱桃木-2.2cm / 黑胡桃木-2.2cm / 实木多层板1.8cm)",
     "length_cm": 0, "width_cm": 0, "qty": 1,
     "is_accessory": false}
  ]
}
长宽统一换算成厘米(cm)。背板/抽屉底板常用多层板。只输出 JSON。"""


def extract_boards(db: Session, image_bytes: bytes, mime: str) -> dict:
    """从设计图抽板单。AI 不可用时返回空板单 (前端转手动录入)。"""
    try:
        prov = _build_provider(db)
        resp = prov.chat_with_image(
            system=_BOARD_SYSTEM,
            user="请把这张设计图按每一块板拆成 JSON 板单。",
            image_bytes=image_bytes, mime=mime, max_tokens=1500,
        )
        data = _parse_json_safe(resp.text)
        return {
            "ai_used": True,
            "model": resp.model,
            "product_type": data.get("product_type"),
            "overall": data.get("overall") or {},
            "boards": data.get("boards") or [],
            "error": None,
        }
    except ai_mod.AiUnavailable as e:
        return {"ai_used": False, "product_type": None, "overall": {}, "boards": [], "error": str(e)}
    except Exception as e:
        return {"ai_used": False, "product_type": None, "overall": {}, "boards": [], "error": f"AI 调用失败: {e}"}
