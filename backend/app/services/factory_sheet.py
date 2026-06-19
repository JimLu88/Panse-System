"""制单图生成服务 (业务需求 §1).

把一个订单的 (产品 / 客户 / 时间 / BOM 物料) 聚合成一份可打印的「制单图」数据,
给工厂排单用。前端拿这个数据渲染打印页 / 导出 PDF。

含两道防护:
  - 地址加密检测 (业务需求 §6): 若客户地址被打码, 在 result 里附 warnings
    让前端弹警告, 阻止打印 (除非用户强制)
  - 缺 BOM 检测: 若引用了未建 BOM 的 SKU, 提示
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
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

    # 下单图规范化 (用户 6 点要求, 2026-06)
    ship_eta_auto: bool = False              # ship_date 是否为"下单+25天"自动推算
    size_info: Optional[str] = None          # SKU 完整尺寸 (产品表 size_value/size_detail)
    production_note: Optional[str] = None    # 店铺/生产备注 (与客户备注一并完整显示)

    # 工厂制单编号 (用户拍板 2026-06-19: 工厂按"畔色 X 单"编号下单)
    factory_no: Optional[int] = None         # 工厂下单编号; 无则下单图标"未能匹配工厂订单号"
    made_date: Optional[date] = None         # 制单日期 = 生成下单图当天
    urgent: bool = False                     # 加急 (备注含加急/急件/尽快 或 要求工期<下单+25天 → 红敲印)

    # 图库配图 (2026-06-11 用户需求: 主图之外再配 SKU 尺寸图, 下单更标准)
    # 相对图库根路径, 前端拼 /api/gallery/file?path=…&max_edge=1600 显示
    gallery_main_image: Optional[str] = None
    sku_image: Optional[str] = None

    # 主材 / 辅材 (图4, 2026-06-12): 取产品总表 main_material / aux_material, 下单图先主材后辅材
    main_material: Optional[str] = None
    aux_material: Optional[str] = None


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


_URGENT_KW = ("加急", "急件", "尽快", "赶工", "越快越好", "尽早", "催货", "催单", "快点", "急要")


def _detect_urgent(texts: list[Optional[str]], order_date: Optional[date]) -> bool:
    """加急判定: 备注/生产备注含加急词, 或备注里要求日期早于 下单+25天 (用户拍板 2026-06-19)。"""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return False
    if any(k in blob for k in _URGENT_KW):
        return True
    if order_date:
        deadline = order_date + timedelta(days=25)
        for mo in re.finditer(r"(\d{1,2})\s*[月./\-]\s*(\d{1,2})", blob):
            try:
                d = date(order_date.year, int(mo.group(1)), int(mo.group(2)))
            except ValueError:
                continue
            if order_date <= d < deadline:
                return True
    return False


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
        # 客户备注 = 买家留言(平台, 随重导更新) 优先, 回退 ERP 人工备注
        remark=getattr(order, "buyer_message", None) or order.remark,
        extra_accessories=extra,
        # 店铺/生产备注 = 人工生产备注 优先, 回退 商家备注(平台)
        production_note=(getattr(order, "production_note", None)
                         or getattr(order, "seller_memo", None)),
        factory_no=getattr(order, "factory_no", None),   # 工厂制单编号
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
    production_note: Optional[str] = None,
    factory_no: Optional[int] = None,
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
    # 订单缺 product_code 但有 sku_code → 从定价表反查 product_code (孚格PFG单、或 sku→编码回填未跑完的单)。
    # 否则下方所有按 product_code 的查找(产品总表/图库主图/SKU尺寸图/配图兜底)全落空 → 下单图无产品图。
    if not product_code and sku_code:
        _pc = db.execute(
            select(PricingSku.product_code).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if _pc:
            product_code = _pc
    if product_code:
        product = db.execute(
            select(Product).where(Product.code == product_code)
        ).scalar_one_or_none()
        # 编码前缀错配兜底 (2026-06-12): 订单/BOM 用 P+11位, 产品总表用 PPS+11位 →
        # 精确查不到时, 用 PPS 前缀再查一次 (修"产品总表找不到"+主辅材空)。
        if product is None and product_code.startswith("P") and not product_code.startswith("PPS"):
            product = db.execute(
                select(Product).where(Product.code == "PPS" + product_code[1:])
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
    # item 页链接(item.htm/item.taobao)是商品详情页、不是图片, wkhtmltoimage 渲不出 → 当无图,
    # 走下面同产品配图兜底 / 图库 (修"产品图空白": 部分定价行 image_url 被导成了 item 页链接)。
    if image_url and ("item.htm" in image_url or "item.taobao" in image_url):
        image_url = None
    # 配图回退 (2026-06-17): SKU 匹配不到/该 SKU 无图时(定制单、缺 sku_code 单), 用同产品
    # 任一有图 SKU 的图 —— 淘宝 CDN 链接 wkhtmltoimage 能直接取, 让下单图不再"无产品图"。
    if not image_url and product_code:
        image_url = db.execute(
            select(PricingSku.image_url).where(
                PricingSku.product_code == product_code,
                PricingSku.image_url.isnot(None),
                PricingSku.image_url != "",
                ~PricingSku.image_url.like("%item.htm%"),
                ~PricingSku.image_url.like("%item.taobao%"),
            ).limit(1)
        ).scalar_one_or_none()

    # 是否定制 sku — "-改" 编码即定制单 (custom_variants 没录档案也算定制, 2026-06-12)
    is_custom = False
    dim_changes = None
    if sku_code and "改" in sku_code:
        is_custom = True
        cv = db.execute(
            select(CustomVariant).where(CustomVariant.custom_sku_code == sku_code)
        ).scalar_one_or_none()
        if cv:
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
            # 用户规则: "占位"物料 = 该产品的木作部分, 下单图上按产品名表述
            display_name = mat_name
            if display_name and "占位" in display_name:
                base = product_name or (product.name if product else None) or "本产品"
                display_name = f"{base}-木作部分"
            materials.append(FactorySheetMaterial(
                material_code=line.material_code,
                material_name=display_name,
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

    # 用户规则: 发货时间缺省 = 下单 + 25 天 (自动写明)
    ship_eta_auto = False
    if not ship_date and order_date:
        from datetime import timedelta as _td
        ship_date = order_date + _td(days=25)
        ship_eta_auto = True

    # 尺寸信息: 优先产品总表的尺寸字段, SKU 名兜底。
    # 占位文本按空处理 (2026-06-12 用户: 产品表 size_value 整列是"待定", 下单图不能照抄)
    def _clean_size(v: Optional[str]) -> Optional[str]:
        s = str(v).strip() if v else ""
        if not s or s in ("待定", "-", "无", "暂无", "/"):
            return None
        return s.replace("\n", "；")   # size_detail 是多行 mm 明细, 压成一行
    size_info = None
    # 定制单优先级 (用户确认 2026-06-12): 定制档案尺寸 > SKU 名带尺寸 >
    # 定制单一律"以客户备注为准"(绝不显示默认款尺寸误导工厂) > 产品默认 > SKU 名。
    # 备注只展示不解析 — 自由文本机器提尺寸容易错单, 以人工核对为准。
    if dim_changes:
        size_info = "定制: " + "；".join(f"{k} {v}" for k, v in dim_changes.items())
    elif pricing_sku and getattr(pricing_sku, "size_info", None):
        # 该 SKU 自己录的成品尺寸 (2026-06-19: 从 SKU 尺寸图读出, 按 sku_code 取)。
        # 多规格(标准/窄款...)从此精确取本变体尺寸, 不再整段堆产品表 size_detail / 选错款。
        size_info = pricing_sku.size_info
    elif sku and re.search(r"\d+(?:\.\d+)?\s*(?:米|m|M|cm|CM|mm|MM)", sku):
        # SKU 名带明确尺寸 (1.4米/45cm/1200mm) → 以 SKU 为准:
        # 产品表 size_detail 是默认款尺寸, 对非默认尺寸的 SKU 会误导工厂备料。
        size_info = sku
    elif is_custom or any(h in (sku or "") for h in ("定制", "咨询", "联系客服")):
        size_info = "定制尺寸 — 以客户备注为准"
        warnings.append(FactorySheetWarning(
            code="custom_size_in_remark",
            severity="warning" if remark else "error",
            message=("定制单未录定制尺寸, 下单图以客户备注为准, 请核对备注后再发工厂。"
                     if remark else
                     "定制单未录定制尺寸且客户备注为空 — 请先补尺寸再发工厂!"),
        ))
    if size_info is None and product is not None:
        size_info = _clean_size(product.size_value) or _clean_size(product.size_detail)
    # 无任何真实尺寸来源 → size_info 留 None, 下单图标红"未对应尺寸" (2026-06-19, 便于找出缺尺寸的SKU)

    # 图库配图: 主图 + SKU 尺寸图 (图库缺失/未挂载时悄悄留空, 不影响下单图)
    gallery_main = sku_img = None
    if product_code:
        from app.services import gallery_lookup
        gallery_main = gallery_lookup.main_image_rel(product_code)
        sku_img = gallery_lookup.sku_image_rel(product_code, sku_code, sku)

    return FactorySheet(
        order_no=order_no,
        sheet_title=_sheet_title(order_no, order_date),
        order_date=order_date,
        ship_date=ship_date,
        ship_eta_auto=ship_eta_auto,
        size_info=size_info,
        production_note=production_note,
        gallery_main_image=gallery_main,
        sku_image=sku_img,
        product_code=product_code,
        product_name=product.name if product else product_name,
        sku=sku,
        sku_code=sku_code,
        image_url=image_url,
        material_desc=product.remark if product else None,
        main_material=product.main_material if product else None,
        aux_material=product.aux_material if product else None,
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
        factory_no=factory_no,
        made_date=date.today(),
        urgent=_detect_urgent([remark, production_note], order_date),
    )
