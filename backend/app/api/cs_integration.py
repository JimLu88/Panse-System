"""AI 客服系统 只读集成 API (用户拍板 2026-06-15)。

给独立的 AI 客服系统(D:\\AI\\AI 客服系统)提供**只读**访问:
  - 产品: 价格(标价/日常价)、尺寸(解析+原文)、定制范围、主辅材、文案、淘宝ID、图引用
  - 订单: 按订单号/客户名查 状态/产品/金额/物流/退款
  - (v2) 定制估价: 报价引擎需板级输入, 暂不开放; 客服先用标准SKU价 + 定制范围答客户
  - (v2) 图库按标签筛选: 待客服系统给图片打标后再加

鉴权: 请求头 X-API-Key, 校验 settings.cs_api_key, 与系统用户登录(JWT)隔离。只读、绝不写库。
知识库: 系统暂无独立客服FAQ库(ai_knowledge 是"AI异常解决库"且为空), 产品详情即产品知识。
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.product import Product

router = APIRouter(prefix="/api/cs", tags=["cs-integration"])


def require_cs_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> bool:
    """X-API-Key 鉴权: 校验 settings.cs_api_key。未配置→503; 不匹配→401。"""
    from app.services import settings_service
    key = settings_service.get(db, "cs_api_key", env_fallback=False)
    if not key:
        raise HTTPException(503, "客服集成未启用(管理员未配置 cs_api_key)")
    if not x_api_key or x_api_key.strip() != key.strip():
        raise HTTPException(401, "无效的 X-API-Key")
    return True


# 尺寸文本 → 长/宽/深/高(mm)。size_detail 例: "宽度：1250mm；长度：2100mm；高度950" /
# "长度：450mm\n深度：400mm\n高度：450mm"。无法解析(如"暂无")返回 {}。
_DIM_KEYS = [("length", ["长度", "长"]), ("width", ["宽度", "宽"]),
             ("depth", ["深度", "深"]), ("height", ["高度", "高"])]


def _parse_size(size_detail: Optional[str]) -> dict:
    if not size_detail or "暂无" in size_detail:
        return {}
    out: dict[str, int] = {}
    for key, kws in _DIM_KEYS:
        for kw in kws:
            m = re.search(kw + r"[：:\s]*?(\d{2,5})", size_detail)
            if m:
                out[key] = int(m.group(1))
                break
    return out


def _price_range(db: Session, code: str) -> dict:
    rows = db.execute(select(PricingSku).where(PricingSku.product_code == code)).scalars().all()
    lps = [float(r.list_price) for r in rows if r.list_price is not None]
    dps = [float(r.daily_price) for r in rows if r.daily_price is not None]
    return {
        "sku_count": len(rows),
        "list_price_min": min(lps) if lps else None,
        "list_price_max": max(lps) if lps else None,
        "daily_price_min": min(dps) if dps else None,
        "daily_price_max": max(dps) if dps else None,
    }


def _summary(db: Session, p: Product) -> dict:
    return {
        "code": p.code, "name": p.name, "category": p.category,
        "custom_scope": p.custom_scope,
        "size": _parse_size(p.size_detail), "size_detail": p.size_detail,
        "image_url": p.image_url, "taobao_id": p.taobao_id,
        "prices": _price_range(db, p.code),
    }


@router.get("/ping")
def cs_ping(_: bool = Depends(require_cs_key)):
    """连通+鉴权自测: 200 即 Key 有效。"""
    return {"ok": True, "service": "panse-cs-integration", "version": 1}


@router.get("/products")
def cs_products(
    q: Optional[str] = Query(None, description="模糊搜产品码/名称"),
    category: Optional[str] = Query(None, description="按类目精确筛"),
    limit: int = Query(50, le=200),
    _: bool = Depends(require_cs_key),
    db: Session = Depends(get_db),
):
    """产品搜索/列表: 含价格区间、尺寸(解析)、定制范围、主图、淘宝ID。"""
    stmt = select(Product)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.code.like(like), Product.name.like(like),
                              Product.sub_name.like(like)))
    if category:
        stmt = stmt.where(Product.category == category)
    rows = db.execute(stmt.order_by(Product.code).limit(limit)).scalars().all()
    return {"count": len(rows), "products": [_summary(db, p) for p in rows]}


@router.get("/products/{code}")
def cs_product_detail(
    code: str,
    _: bool = Depends(require_cs_key),
    db: Session = Depends(get_db),
):
    """单产品全量: 尺寸/定制范围/主辅材/文案/各SKU价格/图引用。"""
    p = db.execute(select(Product).where(Product.code == code)).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "产品不存在")
    skus = db.execute(select(PricingSku).where(PricingSku.product_code == code)).scalars().all()
    gallery: list[dict] = []
    try:
        from app.services import gallery_lookup
        main = gallery_lookup.main_image_rel(code)
        if main:
            gallery.append({"type": "main", "path": main})
    except Exception:  # noqa: BLE001 - 图库取不到不影响产品信息
        pass
    return {
        "code": p.code, "name": p.name, "sub_name": p.sub_name, "brand": p.brand,
        "category": p.category, "custom_scope": p.custom_scope,
        "size": _parse_size(p.size_detail), "size_detail": p.size_detail, "size_value": p.size_value,
        "main_material": p.main_material, "aux_material": p.aux_material,
        "description": p.description, "taobao_id": p.taobao_id,
        "image_url": p.image_url,   # 图库另由下方 gallery 字段提供 (Product 无 gallery_image_url, 误访问致500)
        "skus": [{
            "sku_code": s.sku_code, "sku": s.sku,
            "list_price": float(s.list_price) if s.list_price is not None else None,
            "daily_price": float(s.daily_price) if s.daily_price is not None else None,
        } for s in skus],
        "gallery": gallery,
    }


@router.get("/orders")
def cs_orders(
    order_no: Optional[str] = Query(None, description="精确订单号"),
    customer: Optional[str] = Query(None, description="客户名(模糊)"),
    limit: int = Query(50, le=200),
    _: bool = Depends(require_cs_key),
    db: Session = Depends(get_db),
):
    """订单查询(按订单号或客户名, 二选一必填): 状态/产品/金额/物流/退款。"""
    if not order_no and not customer:
        raise HTTPException(400, "请提供 order_no 或 customer")
    stmt = select(Order).where(Order.is_historical == False)  # noqa: E712
    if order_no:
        stmt = stmt.where(Order.order_no == order_no)
    if customer:
        stmt = stmt.where(Order.customer_name.like(f"%{customer}%"))
    rows = db.execute(stmt.order_by(Order.order_date.desc().nullslast()).limit(limit)).scalars().all()
    return {"count": len(rows), "orders": [{
        "order_no": o.order_no, "customer_name": o.customer_name, "status": o.status,
        "product_name": o.product_name, "sku": o.sku, "qty": o.qty,
        "shop_received_amount": float(o.shop_received_amount) if o.shop_received_amount is not None else None,
        "order_date": o.order_date.isoformat() if o.order_date else None,
        "tracking_no": o.tracking_no, "refund_status": o.refund_status,
    } for o in rows]}
