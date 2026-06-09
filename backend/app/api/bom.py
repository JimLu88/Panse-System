from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bom import BomLine
from app.models.material import Material
from app.models.product import Product
from app.schemas.bom import BomLineGroup, BomLineOut, BomLineUpdate

router = APIRouter(prefix="/api/bom", tags=["bom"])


def _line_out(db: Session, line: BomLine) -> BomLineOut:
    """单条 BOM 行 → 输出(带产品名/图 + 物料名)。"""
    p = db.execute(select(Product.name, Product.image_url).where(Product.code == line.product_code)).first()
    mat = db.execute(select(Material.name).where(Material.code == line.material_code)).scalar()
    return BomLineOut(
        id=line.id, product_code=line.product_code,
        product_name=p.name if p else line.product_name,
        product_image_url=p.image_url if p else None,
        sku=line.sku, sku_code=line.sku_code, material_code=line.material_code,
        material_name=line.material_name or mat, unit=line.unit, qty_per_product=line.qty_per_product,
    )


@router.delete("/lines/{line_id}", status_code=204)
def delete_bom_line(line_id: int, db: Session = Depends(get_db)):
    """删除单条 BOM 行 (清理串料 / 错挂到别的 SKU 的料)。BOM 行无订单直接外键, 删除安全。"""
    line = db.get(BomLine, line_id)
    if not line:
        raise HTTPException(404, "bom line not found")
    db.delete(line)
    db.commit()


@router.patch("/lines/{line_id}", response_model=BomLineOut)
def update_bom_line(line_id: int, payload: BomLineUpdate, db: Session = Depends(get_db)):
    """编辑单条 BOM 行 (改 SKU 归属 / 料号 / 单耗 / 单位等)。改料号会校验该物料存在。"""
    line = db.get(BomLine, line_id)
    if not line:
        raise HTTPException(404, "bom line not found")
    data = payload.model_dump(exclude_unset=True)
    new_code = data.get("material_code")
    if new_code and new_code != line.material_code:
        if not db.execute(select(Material.code).where(Material.code == new_code)).first():
            raise HTTPException(400, f"物料编码不存在: {new_code} (请先在物料库新建)")
    for k, v in data.items():
        setattr(line, k, v)
    db.commit()
    db.refresh(line)
    return _line_out(db, line)


@router.get("/{product_code}", response_model=list[BomLineGroup])
def list_bom_for_product(
    product_code: str,
    db: Session = Depends(get_db),
):
    """返回某产品所有 SKU 的 BOM，按 SKU 分组。"""
    stmt = (
        select(BomLine, Material.name.label("material_name"))
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .where(BomLine.product_code == product_code)
        .order_by(BomLine.sku_code, BomLine.id)
    )
    rows = db.execute(stmt).all()

    grouped: OrderedDict[tuple[str | None, str | None], BomLineGroup] = OrderedDict()
    for line, material_name in rows:
        key = (line.sku, line.sku_code)
        if key not in grouped:
            grouped[key] = BomLineGroup(sku=line.sku, sku_code=line.sku_code, lines=[])
        grouped[key].lines.append(
            BomLineOut(
                id=line.id,
                product_code=line.product_code,
                sku=line.sku,
                sku_code=line.sku_code,
                material_code=line.material_code,
                material_name=material_name,
                unit=line.unit,
                qty_per_product=line.qty_per_product,
            )
        )
    return list(grouped.values())


@router.get("", response_model=list[BomLineOut])
def list_bom_lines(
    product_code: str | None = None,
    material_code: str | None = None,
    limit: int = Query(500, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = (
        select(
            BomLine,
            Material.name.label("material_name"),
            Product.name.label("product_name"),
            Product.image_url.label("product_image_url"),
        )
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .join(Product, BomLine.product_code == Product.code, isouter=True)
    )
    if product_code:
        stmt = stmt.where(BomLine.product_code == product_code)
    if material_code:
        stmt = stmt.where(BomLine.material_code == material_code)
    stmt = stmt.order_by(BomLine.id.desc()).limit(limit).offset(offset)
    return [
        BomLineOut(
            id=line.id,
            product_code=line.product_code,
            product_name=product_name or line.product_name,
            product_image_url=product_image_url,
            sku=line.sku,
            sku_code=line.sku_code,
            material_code=line.material_code,
            material_name=material_name,
            unit=line.unit,
            qty_per_product=line.qty_per_product,
        )
        for line, material_name, product_name, product_image_url in db.execute(stmt).all()
    ]
