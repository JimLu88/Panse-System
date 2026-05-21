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


def _build_diff(
    db: Session, base_sku_code: str, dimension_changes: dict
) -> list[BomDiffLine]:
    lines = db.execute(
        select(BomLine, Material.name.label("mat_name"))
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .where(BomLine.sku_code == base_sku_code)
    ).all()
    diff: list[BomDiffLine] = []
    for line, mat_name in lines:
        note = None
        new_qty = line.qty_per_product or Decimal("1")
        if line.size_type == "组合" and dimension_changes:
            # 整套件随尺寸变, 标个尺寸 tag, qty 不动
            tag = " / ".join(f"{k}={v}" for k, v in dimension_changes.items())
            note = f"按定制尺寸 ({tag}) 重做"
        diff.append(BomDiffLine(
            material_code=line.material_code,
            material_name=mat_name,
            original_qty=line.qty_per_product or Decimal("1"),
            new_qty=new_qty,
            note=note,
        ))
    return diff


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
