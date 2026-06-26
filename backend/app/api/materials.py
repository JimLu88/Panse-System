from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import Material
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate
from app.services import material_coder

router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.get("", response_model=list[MaterialOut])
def list_materials(
    q: Optional[str] = Query(None, description="按编码或名称模糊"),
    is_custom: Optional[bool] = None,
    category: Optional[str] = Query(None, description="按分类筛选"),
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Material)
    if q:
        # 全站统一模糊搜索: 空格分词 + 物料名字符间隙
        from app.services.fuzzy_search import fuzzy_clause
        fc = fuzzy_clause(q, like_cols=[Material.code, Material.name],
                          gap_cols=[Material.name])
        if fc is not None:
            stmt = stmt.where(fc)
    if is_custom is not None:
        stmt = stmt.where(Material.is_custom == is_custom)
    if category:
        stmt = stmt.where(Material.category == category)
    stmt = stmt.order_by(Material.code).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.get("/categories", response_model=dict)
def list_material_categories(db: Session = Depends(get_db)):
    """配件库里已用到的分类(去重, 供前端下拉/折叠)。"""
    from sqlalchemy import func
    cats = db.execute(
        select(func.distinct(Material.category)).where(Material.category.isnot(None))
        .order_by(Material.category)
    ).scalars().all()
    return {"categories": [c for c in cats if c]}


class MaterialUsedInOut(BaseModel):
    product_code: str
    product_name: Optional[str] = None
    qty_per_product: float
    sku_count: int


@router.get("/{code}/used-in-products", response_model=list[MaterialUsedInOut])
def material_used_in_products(code: str, db: Session = Depends(get_db)):
    """物料反推产品 (BOM 反查): 列出 BOM 里用到该物料的产品 + 单产品用量 + 涉及 SKU 数。"""
    from app.models.bom import BomLine
    rows = db.execute(
        select(BomLine.product_code, BomLine.product_name, BomLine.qty_per_product, BomLine.sku_code)
        .where(BomLine.material_code == code)
    ).all()
    agg: dict[str, dict] = {}
    for pc, pname, qty, skuc in rows:
        e = agg.setdefault(pc, {"product_code": pc, "product_name": pname, "qty": 0.0, "skus": set()})
        if pname and not e["product_name"]:
            e["product_name"] = pname
        e["qty"] = max(e["qty"], float(qty or 0))
        if skuc:
            e["skus"].add(skuc)
    out = [
        MaterialUsedInOut(
            product_code=e["product_code"], product_name=e["product_name"],
            qty_per_product=e["qty"], sku_count=len(e["skus"]),
        )
        for e in agg.values()
    ]
    out.sort(key=lambda x: x.product_code)
    return out


class NextCodeOut(BaseModel):
    code: str


@router.get("/next-code", response_model=NextCodeOut)
def preview_next_code(
    prefix: str = Query("AC", description="AC / MP / MW / SP"),
    db: Session = Depends(get_db),
):
    """预览下一个配件编码 (不写库)。"""
    try:
        code = material_coder.next_code(db, prefix)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return NextCodeOut(code=code)


@router.post("", response_model=MaterialOut, status_code=201)
def create_material(payload: MaterialCreate, db: Session = Depends(get_db)):
    if payload.code:
        existing = db.execute(select(Material).where(Material.code == payload.code)).scalar_one_or_none()
        if existing:
            raise HTTPException(409, f"material code {payload.code} already exists")
        code = payload.code
    else:
        try:
            code = material_coder.next_code(db, payload.prefix)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    mat = Material(
        code=code,
        name=payload.name,
        size_type=payload.size_type,
        unit=payload.unit,
        price=payload.price,
        remark=payload.remark,
        category=payload.category,
        is_custom=False,
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)
    return mat


@router.patch("/{material_id}", response_model=MaterialOut)
def update_material(material_id: int, payload: MaterialUpdate, db: Session = Depends(get_db)):
    mat = db.get(Material, material_id)
    if not mat:
        raise HTTPException(404, "material not found")
    data = payload.model_dump(exclude_unset=True)
    price_changed = "price" in data and data["price"] != mat.price
    # 人工编辑 → 统一历史档案 (本路由无登录依赖, actor 记空, 来源 web)
    from app.services import field_change_service
    field_change_service.diff_and_apply(
        db, mat, data, table="materials", pk=mat.code,
        row_label=mat.name,
        field_labels={"price": "单价", "name": "名称", "unit": "单位",
                      "lead_time_days": "提前期(天)", "safety_stock": "安全库存"},
    )
    # BOM漂移检查已停用 (用户拍板 2026-06-12): BOM 单价只用于预估/定制报价,
    # 不与批量定价对照, 物料改价不再触发 stale 标记/异常。
    _ = price_changed
    db.commit()
    db.refresh(mat)
    return mat


@router.post("/auto-categorize", response_model=dict)
def auto_categorize(
    apply: bool = Query(False, description="True=写库; False=只出预览"),
    only_empty: bool = Query(True, description="True=只补未分类(不覆盖人工已设)"),
    db: Session = Depends(get_db),
):
    """按名字规则批量给物料归类(配件分类)。默认 dry-run 预览, apply=True 落库。"""
    from app.services import material_category_service
    return material_category_service.auto_categorize(db, apply=apply, only_empty=only_empty)


@router.post("/ensure-consumables", response_model=dict)
def ensure_consumables(
    apply: bool = Query(False, description="True=建物料+加BOM落库; False=只预览"),
    db: Session = Depends(get_db),
):
    """通用消耗配件(双面胶/螺丝)建成 AC 物料 + 加进每个产品每个 SKU 的 BOM(0.1元/个, 幂等)。"""
    from app.services import material_category_service
    return material_category_service.ensure_consumables_in_boms(db, apply=apply)
