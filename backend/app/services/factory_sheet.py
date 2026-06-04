"""制单图生成服务 (业务需求 §1).

把一个订单的 (产品 / 客户 / 时间 / BOM 物料) 聚合成一份可打印的「制单图」数据,
给工厂排单用。前端拿这个数据渲染打印页 / 导出 PDF。

含两道防护:
  - 地址加密检测 (业务需求 §6): 若客户地址被打码, 在 result 里附 warnings
    让前端弹警告, 阻止打印 (除非用户强制)
  - 缺 BOM 检测: 若引用了未建 BOM 的 SKU, 提示
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.custom_variant import CustomVariant
from app.models.material import Material
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import validation


@dataclass
class FactorySheetMaterial:
    material_code: str
    material_name: Optional[str]
    qty_per_product: Decimal
    total_qty: Decimal           # qty_per_product × order qty
    unit: Optional[str]
    spec: Optional[str] = None   # 材料规格 (size_type / 物料备注)
    source: str = "bom"          # bom = BOM 自动带出; 客户备注 = 截图备注里识别的新增配件
    note: Optional[str] = None   # 客户备注原文 (source=客户备注 时)


@dataclass
class FactorySheetWarning:
    code: str             # encrypted_address / encrypted_phone / no_bom / unknown_product
    message: str
    severity: str = "warning"


@dataclass
class FactorySheet:
    order_no: str
    sheet_title: str
    order_date: Optional[date]
    ship_date: Optional[date]

    # 产品段
    product_code: Optional[str]
    product_name: Optional[str]
    sku: Optional[str]
    sku_code: Optional[str]
    image_url: Optional[str]
    material_desc: Optional[str]     # 材质介绍
    dimension_desc: Optional[str]    # 尺寸描述

    # 客户段
    customer_name: Optional[str]
    customer_phone: Optional[str]
    customer_address: Optional[str]

    qty: int
    remark: Optional[str]

    # BOM 物料明细 (业务需求 §1: 自动写入便于配件采购)
    materials: list[FactorySheetMaterial] = field(default_factory=list)

    # 定制信息 (如果是 改 SKU)
    is_custom_variant: bool = False
    dimension_changes: Optional[dict] = None

    warnings: list[FactorySheetWarning] = field(default_factory=list)


def _merge_extra_accessories(
    db: Session,
    materials: list[FactorySheetMaterial],
    extra_accessories: Optional[list[dict]],
    order_qty: int,
) -> int:
    """把客户备注里识别的新增配件合并进物料明细。

    每项 dict 形如 {name, qty?, note?}。尝试按名称匹配 Material 拿到真实编码/单位,
    匹配不到则用占位编码 (客户新增) 并保留备注原文。返回新增条数。
    """
    if not extra_accessories:
        return 0
    existing_codes = {m.material_code for m in materials}
    added = 0
    for item in extra_accessories:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        try:
            per = Decimal(str(item.get("qty") or 1))
        except (ValueError, ArithmeticError):
            per = Decimal(1)
        note = item.get("note") or None

        mat = db.execute(
            select(Material).where(Material.name == name)
        ).scalar_one_or_none()
        if mat:
            code = mat.code
            unit = mat.unit
            mat_name = mat.name
        else:
            code = f"NEW-{name[:8]}"   # 占位编码: 库里还没有这个配件
            unit = None
            mat_name = name
        if code in existing_codes:
            continue   # 已在 BOM 里, 不重复加
        existing_codes.add(code)
        # 备注配件的 qty 是整单绝对数量 (如"加2个抱枕"=2), 不随产品件数翻倍
        materials.append(FactorySheetMaterial(
            material_code=code,
            material_name=mat_name,
            qty_per_product=per,
            total_qty=per,
            unit=unit,
            spec=note,
            source="客户备注",
            note=note,
        ))
        added += 1
    return added


def _sheet_title(order_no: str, order_date: Optional[date]) -> str:
    """5月31日 151单 这种格式 (取订单号尾段)."""
    if not order_date:
        return f"订单 {order_no}"
    return f"{order_date.month}月{order_date.day}日 订单 {order_no[-4:]}"


def build(db: Session, order_id: int) -> FactorySheet:
    from app.models.order import OrderAccessoryItem

    order = db.get(Order, order_id)
    if order is None:
        raise ValueError(f"order {order_id} not found")

    # 已入库订单: 把配件清单里「客户备注」来源的配件带进下单图
    extra = [
        {"name": it.material_name, "qty": it.qty_required, "note": it.remark}
        for it in db.execute(
            select(OrderAccessoryItem).where(
                OrderAccessoryItem.order_id == order_id,
                OrderAccessoryItem.source == "客户备注",
            )
        ).scalars().all()
    ]
    return build_from_fields(
        db,
        order_no=order.order_no,
        product_code=order.product_code,
        product_name=order.product_name,
        sku=order.sku,
        sku_code=order.sku_code,
        qty=order.qty,
        customer_name=order.customer_name,
        customer_phone=order.customer_phone,
        customer_address=order.customer_address,
        order_date=order.order_date,
        ship_date=order.ship_date,
        remark=order.remark,
        extra_accessories=extra,
    )


def build_from_fields(
    db: Session,
    *,
    order_no: str,
    product_code: Optional[str],
    product_name: Optional[str],
    sku: Optional[str],
    sku_code: Optional[str],
    qty: int,
    customer_name: Optional[str],
    customer_phone: Optional[str],
    customer_address: Optional[str],
    order_date: Optional[date],
    ship_date: Optional[date],
    remark: Optional[str],
    extra_accessories: Optional[list[dict]] = None,
) -> FactorySheet:
    """从订单字段直接生成制单图 (不要求订单已入库, 供千牛截图预览「生成下单图」用)。

    extra_accessories: 客户备注里识别出的新增配件 (业务需求: 截图 OCR 时若客户备注
    提到额外配件, 自动加入下单图, 让工厂照单备料)。每项 {name, qty?, note?}。
    """
    qty = qty or 1
    warnings: list[FactorySheetWarning] = []

    # 1. 客户地址加密检测
    addr_check = validation.is_address_encrypted(customer_address)
    if addr_check.is_encrypted:
        warnings.append(FactorySheetWarning(
            code="encrypted_address",
            severity="error",
            message=(
                f"客户地址被打码: {', '.join(addr_check.reasons)}. "
                "请到客服后台上传未加密版本后重新生成制单图。"
            ),
        ))
    if validation.is_phone_encrypted(customer_phone):
        warnings.append(FactorySheetWarning(
            code="encrypted_phone",
            severity="warning",
            message="客户电话疑被打码, 建议核对后再发工厂。",
        ))

    # 2. 找产品 + SKU 详情
    product = None
    pricing_sku = None
    image_url = material_desc = None
    if product_code:
        product = db.execute(
            select(Product).where(Product.code == product_code)
        ).scalar_one_or_none()
        if product is None:
            warnings.append(FactorySheetWarning(
                code="unknown_product",
                severity="error",
                message=f"订单 product_code={product_code} 在产品总表里找不到。",
            ))

    # 通过 SKU 名找 sku_code (Order.sku 存的是 SKU 名字, 不是 code)
    if sku and not sku_code:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku == sku)
        ).scalar_one_or_none()
        if ps:
            sku_code = ps.sku_code
            pricing_sku = ps
    elif sku_code:
        pricing_sku = db.execute(
            select(PricingSku).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
    if pricing_sku:
        image_url = pricing_sku.image_url

    # 是否定制 sku
    is_custom = False
    dim_changes = None
    if sku_code and "改" in sku_code:
        cv = db.execute(
            select(CustomVariant).where(CustomVariant.custom_sku_code == sku_code)
        ).scalar_one_or_none()
        if cv:
            is_custom = True
            dim_changes = cv.dimension_overrides

    # 3. BOM 物料明细 (业务需求 §1)
    materials: list[FactorySheetMaterial] = []
    if sku_code:
        bom = db.execute(
            select(BomLine, Material.name.label("mat_name"), Material.unit.label("mat_unit"))
            .join(Material, BomLine.material_code == Material.code, isouter=True)
            .where(BomLine.sku_code == sku_code)
        ).all()
        for line, mat_name, mat_unit in bom:
            qty_per = Decimal(line.qty_per_product or 1)
            materials.append(FactorySheetMaterial(
                material_code=line.material_code,
                material_name=mat_name,
                qty_per_product=qty_per,
                total_qty=qty_per * Decimal(qty),
                unit=line.unit or mat_unit,
                spec=line.remark,
            ))

    if not materials:
        warnings.append(FactorySheetWarning(
            code="no_bom",
            severity="warning",
            message="该 SKU 没有 BOM, 工厂没法直接照单备料。",
        ))

    # 3b. 客户备注新增配件 (业务需求: 截图 OCR 备注里识别的额外配件也要进下单图)
    extra_added = _merge_extra_accessories(db, materials, extra_accessories, qty)
    if extra_added:
        warnings.append(FactorySheetWarning(
            code="extra_accessory",
            severity="warning",
            message=f"客户备注含 {extra_added} 项新增配件, 已加入下单图, 请工厂确认备料。",
        ))

    return FactorySheet(
        order_no=order_no,
        sheet_title=_sheet_title(order_no, order_date),
        order_date=order_date,
        ship_date=ship_date,
        product_code=product_code,
        product_name=product.name if product else product_name,
        sku=sku,
        sku_code=sku_code,
        image_url=image_url,
        material_desc=product.remark if product else None,
        dimension_desc=sku,  # SKU 名通常含尺寸信息
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_address=customer_address,
        qty=qty,
        remark=remark,
        materials=materials,
        is_custom_variant=is_custom,
        dimension_changes=dim_changes,
        warnings=warnings,
    )
