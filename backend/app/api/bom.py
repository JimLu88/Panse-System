from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bom import BomLine
from app.models.material import Material
from app.models.product import Product
from app.schemas.bom import BomLineCreate, BomLineGroup, BomLineOut, BomLineUpdate
from app.services import field_change_service

router = APIRouter(prefix="/api/bom", tags=["bom"])


def _resolve_product_code(db: Session, code: str | None) -> str | None:
    """返回产品总表里真实存在的产品编码; 容错 订单/旧BOM 的 P+11 → 产品库 PPS+11。
    都不存在返回 None (调用方据此报错, 防止建出挂空产品的幽灵 BOM 行)。"""
    if not code:
        return None
    code = code.strip()
    if db.execute(select(Product.code).where(Product.code == code)).first():
        return code
    if code.startswith("P") and not code.startswith("PPS"):
        alt = "PPS" + code[1:]
        if db.execute(select(Product.code).where(Product.code == alt)).first():
            return alt
    return None


def _rename_material(db: Session, material_code: str, new_name: str) -> None:
    """改物料库 Material.name = 单一来源, 全站(BOM/看板配件/下单图)随 join 同步。
    name 有唯一索引 → 撞别的物料名时报错让用户改名/合并; 记一条字段历史。"""
    new_name = new_name.strip()
    if not new_name:
        return
    mat = db.execute(select(Material).where(Material.code == material_code)).scalar_one_or_none()
    if not mat or (mat.name or "") == new_name:
        return
    clash = db.execute(
        select(Material.code).where(Material.name == new_name, Material.code != mat.code)
    ).first()
    if clash:
        raise HTTPException(
            400, f"物料名「{new_name}」已被物料 {clash[0]} 占用; 换个名, 或去物料库把两个物料合并。")
    old = mat.name
    mat.name = new_name
    field_change_service.record(
        db, table="materials", pk=mat.code, field="name", old=old, new=new_name,
        actor="BOM编辑", source="web", row_label=f"物料 {mat.code}", field_label="物料名称")


def _auto_resync_orders(db: Session, product_code) -> None:
    """BOM 改/增/删后自动重算该产品在制订单的配件清单 (用户拍板 2026-06-12 改实时)。
    失败不影响 BOM 编辑本身 (配件清单可随时再对齐)。"""
    try:
        from app.services import accessory_checklist_service
        accessory_checklist_service.resync_product_orders(db, product_code)
    except Exception:  # noqa: BLE001 — 配件重算失败不阻断 BOM 保存
        pass


def _line_out(db: Session, line: BomLine) -> BomLineOut:
    """单条 BOM 行 → 输出(带产品名/图/类目 + 物料名)。"""
    p = db.execute(
        select(Product.name, Product.image_url, Product.category).where(Product.code == line.product_code)
    ).first()
    mat = db.execute(select(Material.name).where(Material.code == line.material_code)).scalar()
    return BomLineOut(
        id=line.id, product_code=line.product_code,
        product_name=p.name if p else line.product_name,
        product_image_url=p.image_url if p else None,
        product_category=p.category if p else None,
        sku=line.sku, sku_code=line.sku_code, material_code=line.material_code,
        material_name=mat or line.material_name,  # 物料库名为单一来源, 行内冗余名只兜底
        unit=line.unit, qty_per_product=line.qty_per_product,
    )


def _bom_drift_check(db, sku_code: str | None) -> None:
    """已停用 (用户拍板 2026-06-12): BOM 单价只用于预估/定制报价,
    不与批量定价对照 — 漂移检查整体关闭, 保留空函数防调用点崩。"""
    return None


def _bom_drift_check_disabled(db, sku_code: str | None) -> None:
    """原 Plan L7 实现 (留档不再调用)。"""
    if not sku_code:
        return
    try:
        from app.services import pricing_bom_sync_service
        pricing_bom_sync_service.check_sku(db, sku_code)
    except Exception:  # pragma: no cover
        pass


@router.delete("/lines/{line_id}", status_code=204)
def delete_bom_line(line_id: int, db: Session = Depends(get_db)):
    """删除单条 BOM 行 (清理串料 / 错挂到别的 SKU 的料)。BOM 行无订单直接外键, 删除安全。"""
    line = db.get(BomLine, line_id)
    if not line:
        raise HTTPException(404, "bom line not found")
    sku_code = line.sku_code
    pc = line.product_code
    db.delete(line)
    db.flush()
    _bom_drift_check(db, sku_code)
    db.commit()
    _auto_resync_orders(db, pc)


@router.patch("/lines/{line_id}", response_model=BomLineOut)
def update_bom_line(line_id: int, payload: BomLineUpdate, db: Session = Depends(get_db)):
    """编辑单条 BOM 行 (改 SKU 归属 / 料号 / 单耗 / 单位等)。改料号会校验该物料存在。"""
    line = db.get(BomLine, line_id)
    if not line:
        raise HTTPException(404, "bom line not found")
    data = payload.model_dump(exclude_unset=True)
    old_pc = line.product_code
    # 物料名称: 不再写 BOM 行冗余字段, 而是直接改物料库 (单一来源, 全站同步)
    new_mat_name = data.pop("material_name", None)
    new_code = data.get("material_code")
    if new_code and new_code != line.material_code:
        if not db.execute(select(Material.code).where(Material.code == new_code)).first():
            raise HTTPException(400, f"物料编码不存在: {new_code} (请先在物料库新建)")
    # 改了产品归属: 校验产品码真实存在 (容错 P+11→PPS+11), 防改成挂空产品的幽灵行
    if "product_code" in data and data["product_code"]:
        pc = _resolve_product_code(db, data["product_code"])
        if pc is None:
            raise HTTPException(
                400, f"产品编码不存在: {data['product_code']} (请填产品总表里的产品编码, 如 PPS...)")
        data["product_code"] = pc
    # 改物料库名 (验证唯一/记历史) — 在改 setattr 前做, 失败即整单回滚不动
    if new_mat_name is not None:
        _rename_material(db, new_code or line.material_code, str(new_mat_name))
    for k, v in data.items():
        setattr(line, k, v)
    db.flush()
    _bom_drift_check(db, line.sku_code)
    db.commit()
    db.refresh(line)
    _auto_resync_orders(db, line.product_code)
    if old_pc and old_pc != line.product_code:
        _auto_resync_orders(db, old_pc)   # 改了归属产品: 原产品订单也要重算
    return _line_out(db, line)


@router.post("/lines", response_model=BomLineOut, status_code=201)
def create_bom_line(payload: BomLineCreate, db: Session = Depends(get_db)):
    """行内新增 BOM 行 (图2): 选已有物料编码, 或给新物料名 → 自动生成编码并建物料。
    用于把一条配件拆成多条 (如金属侧板 → 宽板/窄板) 而无需先跳物料库手动建。"""
    # 校验产品码真实存在 (容错 P+11→PPS+11), 防敲错码建出挂空产品、没图的幽灵 BOM 行
    product_code = _resolve_product_code(db, payload.product_code)
    if product_code is None:
        raise HTTPException(
            400, f"产品编码不存在: {payload.product_code} (请填产品总表里的产品编码, 如 PPS26380040225)")
    code = (payload.material_code or "").strip() or None
    if payload.new_material_name and not code:
        # 全新物料: 自动生成下一个可用编码 (与 /api/materials/next-code 同源)
        from app.services import material_coder
        try:
            code = material_coder.next_code(db, payload.material_prefix or "AC")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if not code:
        raise HTTPException(400, "请选择物料编码, 或填写新物料名称(系统自动生成编码)")
    exists = db.execute(select(Material.code).where(Material.code == code)).first()
    if not exists:
        if payload.new_material_name:
            db.add(Material(code=code, name=payload.new_material_name,
                            unit=payload.unit, is_custom=False))
            db.flush()
        else:
            raise HTTPException(400, f"物料编码不存在: {code} (填新物料名称可自动新建)")
    line = BomLine(
        product_code=product_code, sku=payload.sku, sku_code=payload.sku_code,
        material_code=code, unit=payload.unit,
        qty_per_product=payload.qty_per_product if payload.qty_per_product is not None else 1,
    )
    db.add(line)
    db.flush()
    db.commit()
    db.refresh(line)
    _auto_resync_orders(db, product_code)
    return _line_out(db, line)


@router.get("/{product_code}", response_model=list[BomLineGroup])
def list_bom_for_product(
    product_code: str,
    db: Session = Depends(get_db),
):
    """返回某产品所有 SKU 的 BOM，按 SKU 分组。"""
    stmt = (
        select(
            BomLine,
            Material.name.label("material_name"),
            Material.width_mm.label("material_width_mm"),
            Material.height_mm.label("material_height_mm"),
            Material.area.label("material_area"),
            Material.size_type.label("material_size_type"),
        )
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .where(BomLine.product_code == product_code)
        .order_by(BomLine.sku_code, BomLine.id)
    )
    rows = db.execute(stmt).all()

    grouped: OrderedDict[tuple[str | None, str | None], BomLineGroup] = OrderedDict()
    for line, material_name, width_mm, height_mm, area, size_type in rows:
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
                material_width_mm=width_mm,
                material_height_mm=height_mm,
                material_area=area,
                material_size_type=size_type,
            )
        )
    return list(grouped.values())


@router.get("", response_model=list[BomLineOut])
def list_bom_lines(
    product_code: str | None = None,
    product: str | None = Query(None, description="按产品名称模糊搜 (支持中间插词)"),
    material_code: str | None = None,
    category: str | None = Query(None, description="按产品类目筛 (join 产品总表 category)"),
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
            Product.category.label("product_category"),
        )
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .join(Product, BomLine.product_code == Product.code, isouter=True)
    )
    if product_code:
        stmt = stmt.where(BomLine.product_code == product_code)
    if product:
        # 全站统一模糊搜索: 产品名/副名称/BOM行产品名, 支持中间插词
        from app.services.fuzzy_search import fuzzy_clause
        fc = fuzzy_clause(product,
                          like_cols=[Product.name, Product.sub_name, BomLine.product_name],
                          gap_cols=[Product.name, Product.sub_name, BomLine.product_name])
        if fc is not None:
            stmt = stmt.where(fc)
    if material_code:
        stmt = stmt.where(BomLine.material_code == material_code)
    if category:
        stmt = stmt.where(Product.category == category)
    stmt = stmt.order_by(BomLine.id.desc()).limit(limit).offset(offset)
    return [
        BomLineOut(
            id=line.id,
            product_code=line.product_code,
            product_name=product_name or line.product_name,
            product_image_url=product_image_url,
            product_category=product_category,
            sku=line.sku,
            sku_code=line.sku_code,
            material_code=line.material_code,
            material_name=material_name,
            unit=line.unit,
            qty_per_product=line.qty_per_product,
        )
        for line, material_name, product_name, product_image_url, product_category in db.execute(stmt).all()
    ]
