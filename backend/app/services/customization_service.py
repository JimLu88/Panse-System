"""尺寸微定制服务 (业务需求 §2).

两步流:
  preview(base_sku_code, dimension_changes) → 返回 BOM diff 让用户确认 (不写库)
  confirm(base_sku_code, dimension_changes, order_no, user) →
        生成新 custom_sku_code = base + "改NN"
        克隆 BOM 行, 按尺寸调整 qty 或换物料
        若 BOM 行引用了不存在的尺寸材料, 走 material_service.ensure_by_name (复用定制建料路径)
        写一行 CustomVariant 留痕

BOM 行的调整规则 (本期最简):
  - size_type == "组合" → 视为整套打包件, qty 不变, 但 description 加 "@尺寸"
  - 其它情况 → qty 不变
  详细的尺寸 → 物料数量映射后续可加, 这里先把 hook 打好。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.custom_variant import CustomVariant
from app.models.material import Material


@dataclass
class BomDiffLine:
    material_code: str
    material_name: Optional[str]
    original_qty: Decimal
    new_qty: Decimal
    note: Optional[str] = None
    requires_new_material: bool = False


@dataclass
class PreviewResult:
    base_sku_code: str
    proposed_custom_sku_code: str
    dimension_changes: dict
    diff_lines: list[BomDiffLine]


def _next_custom_sku_code(db: Session, base: str) -> str:
    """同一 base sku 下找最大 改NN 序号, +1.

    若没有任何 改 子项 → base + "改01"
    """
    pattern = f"{base}改%"
    existing = db.execute(
        select(CustomVariant.custom_sku_code)
        .where(CustomVariant.custom_sku_code.like(pattern))
    ).scalars().all()
    max_n = 0
    for code in existing:
        suffix = code[len(base) + 1:]  # "01" / "02" / ...
        try:
            n = int(suffix)
            if n > max_n:
                max_n = n
        except (ValueError, IndexError):
            continue
    return f"{base}改{max_n + 1:02d}"


def _size_signature(dimension_changes: dict) -> str:
    """把尺寸字典转成有序签名, 用于 fuzzy 匹配现有定制物料."""
    parts = sorted(f"{k}={v}" for k, v in dimension_changes.items())
    return "|".join(parts)


def _dims_of(changes: dict) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """从尺寸字典提取 宽/高 数值 (键名兼容 宽/width / 高/height), 提不出返回 None。"""
    w = h = None
    for k, v in (changes or {}).items():
        kl = str(k).lower()
        try:
            dv = Decimal(str(v))
        except Exception:
            continue
        if "宽" in kl or "width" in kl:
            w = dv
        elif "高" in kl or "height" in kl:
            h = dv
    return w, h


def _find_reusable_material(
    db: Session, *, base_material_code: str, base_material_name: Optional[str],
    size_sig: str, width_mm: Optional[Decimal] = None, height_mm: Optional[Decimal] = None,
) -> Optional[Material]:
    """Phase 7 P1-8 + Plan C3 防串料: 找"同基础物料 + 同尺寸"定制件复用.

    正式口径: is_custom + base_material_code 精确对照 + width/height 数值比对;
    旧数据兜底: 名称前缀 + remark 含 size_sig, 但标了不同 base 的绝不复用 (防串料)。
    """
    # 1) 正式口径: base_material_code 精确匹配
    rows = db.execute(
        select(Material).where(
            Material.is_custom == True,  # noqa: E712
            Material.base_material_code == base_material_code,
        )
    ).scalars().all()
    for m in rows:
        if (width_mm is not None and height_mm is not None
                and m.width_mm is not None and m.height_mm is not None):
            if Decimal(m.width_mm) == width_mm and Decimal(m.height_mm) == height_mm:
                return m
            continue   # 双方尺寸都明确但不等 → 不是同一件
        if m.remark and size_sig and size_sig in m.remark:
            return m
    # 2) 旧数据兜底: 名称前缀 (未回填 base_material_code 的历史定制件)
    if not base_material_name:
        return None
    name_prefix = base_material_name.split("(")[0].strip()
    rows = db.execute(
        select(Material).where(
            Material.is_custom == True,  # noqa: E712
            Material.name.like(f"{name_prefix}%"),
        )
    ).scalars().all()
    for m in rows:
        if m.base_material_code and m.base_material_code != base_material_code:
            continue   # C3: 同前缀不同基础料 → 串料风险, 跳过
        if m.remark and size_sig in m.remark:
            return m
    return None


def _build_diff(
    db: Session, base_sku_code: str, dimension_changes: dict
) -> list[BomDiffLine]:
    lines = db.execute(
        select(BomLine, Material.name.label("mat_name"))
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .where(BomLine.sku_code == base_sku_code)
    ).all()
    size_sig = _size_signature(dimension_changes) if dimension_changes else ""
    diff: list[BomDiffLine] = []
    for line, mat_name in lines:
        note = None
        new_qty = line.qty_per_product or Decimal("1")
        target_material_code = line.material_code
        if line.size_type == "组合" and dimension_changes:
            tag = " / ".join(f"{k}={v}" for k, v in dimension_changes.items())
            note = f"按定制尺寸 ({tag}) 重做"
            # P1-8: 找有没有现成的同尺寸定制件复用 (C3: 带宽高数值精确比对)
            w_mm, h_mm = _dims_of(dimension_changes)
            reuse = _find_reusable_material(
                db,
                base_material_code=line.material_code,
                base_material_name=mat_name,
                size_sig=size_sig,
                width_mm=w_mm, height_mm=h_mm,
            )
            if reuse is not None:
                target_material_code = reuse.code
                note = f"复用已有定制物料 {reuse.code} ({tag})"
        diff.append(BomDiffLine(
            material_code=target_material_code,
            material_name=mat_name,
            original_qty=line.qty_per_product or Decimal("1"),
            new_qty=new_qty,
            note=note,
            requires_new_material=(target_material_code == line.material_code
                                    and line.size_type == "组合"
                                    and bool(dimension_changes)),
        ))
    return diff


def precheck_stock(db: Session, diff_lines: list[BomDiffLine]) -> dict:
    """Plan F5: 定制确认前库存预检 — 按可用量分组: 现货够 / 需采购 / 需新开料。"""
    from app.models.inventory import PartInventory
    in_stock: list[dict] = []
    need_purchase: list[dict] = []
    need_new_material: list[dict] = []
    for d in diff_lines:
        need = float(d.new_qty or 0)
        item = {"material_code": d.material_code, "material_name": d.material_name,
                "need": need}
        if d.requires_new_material:
            need_new_material.append(item)
            continue
        inv = db.execute(
            select(PartInventory).where(PartInventory.material_code == d.material_code)
        ).scalar_one_or_none()
        avail = float(inv.available_qty) if inv is not None else 0.0
        item["available"] = avail
        if avail >= need:
            in_stock.append(item)
        else:
            item["shortage"] = round(need - avail, 3)
            need_purchase.append(item)
    return {
        "in_stock": in_stock,
        "need_purchase": need_purchase,
        "need_new_material": need_new_material,
        "has_shortage": bool(need_purchase or need_new_material),
    }


def preview(
    db: Session, *, base_sku_code: str, dimension_changes: dict,
) -> PreviewResult:
    if not base_sku_code:
        raise ValueError("base_sku_code is required")
    if not dimension_changes:
        raise ValueError("dimension_changes 不能为空 — 没有变更就不是定制")
    diff = _build_diff(db, base_sku_code, dimension_changes)
    if not diff:
        raise ValueError(f"sku {base_sku_code!r} 没有 BOM 行, 无法做定制")
    return PreviewResult(
        base_sku_code=base_sku_code,
        proposed_custom_sku_code=_next_custom_sku_code(db, base_sku_code),
        dimension_changes=dimension_changes,
        diff_lines=diff,
    )


@dataclass
class ConfirmResult:
    custom_variant_id: int
    custom_sku_code: str
    cloned_bom_lines: int
    auto_created_materials: list[str] = field(default_factory=list)


def confirm(
    db: Session,
    *,
    base_sku_code: str,
    dimension_changes: dict,
    order_no: Optional[str] = None,
    note: Optional[str] = None,
    created_by: Optional[str] = None,
    qty_overrides: Optional[dict[str, Decimal]] = None,
) -> ConfirmResult:
    """落库: 生成 custom_sku_code + 克隆 BOM + 写 CustomVariant."""
    diff = _build_diff(db, base_sku_code, dimension_changes)
    if not diff:
        raise ValueError(f"sku {base_sku_code!r} 没有 BOM 行")

    custom_sku = _next_custom_sku_code(db, base_sku_code)

    # 找原 sku 的 product_code (从 BOM 行任意一行借)
    src = db.execute(
        select(BomLine).where(BomLine.sku_code == base_sku_code).limit(1)
    ).scalar_one()
    src_product_code = src.product_code

    # 克隆 BOM
    cloned = 0
    for d in diff:
        final_qty = qty_overrides.get(d.material_code) if qty_overrides else None
        if final_qty is None:
            final_qty = d.new_qty
        db.add(BomLine(
            product_code=src_product_code,
            sku=src.sku,
            sku_code=custom_sku,
            material_code=d.material_code,
            unit=src.unit,
            qty_per_product=Decimal(str(final_qty)),
            remark=d.note,
        ))
        cloned += 1

    cv = CustomVariant(
        base_sku_code=base_sku_code,
        custom_sku_code=custom_sku,
        related_order_no=order_no,
        product_code=src_product_code,
        dimension_overrides=dimension_changes,
        bom_overrides={d.material_code: str(d.new_qty) for d in diff},
        note=note,
        created_by=created_by,
    )
    db.add(cv)
    db.flush()

    return ConfirmResult(
        custom_variant_id=cv.id,
        custom_sku_code=custom_sku,
        cloned_bom_lines=cloned,
    )
