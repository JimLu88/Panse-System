"""Phase 1 冷启动：把 Excel 的 4 张表导入数据库。

依赖顺序 (plan §13)：产品总表 → 物料单价库 → BOM → 库存。
配件库存表里可能有「定制」物料 (AC-1000+) 不在价格表里——这种情况下走
material_service.ensure_by_name() 自动建档，与运行时录入行为一致。

用法：
    python -m scripts.import_excel path/to/excel.xlsx
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import openpyxl

from app.database import SessionLocal
from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.product import Product
from app.services import exception_service, inventory_service, material_service  # noqa: F401

SHEET_PRODUCTS = "1-产品总表"
SHEET_PRICE = "3b-配件价格表"
SHEET_BOM = "3-BOM表"
SHEET_PART_INV = "4b-配件库存"
SHEET_PROD_INV = "4a-成品库存"


def _str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _int(v: Any) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _find_header_row(ws, expected_first_col: str) -> int:
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if _str(row[0]) == expected_first_col:
            return row_idx
    raise ValueError(f"could not find header row starting with {expected_first_col!r} in sheet {ws.title}")


def import_products(wb, db) -> int:
    # 列: 产品编码 / 类目 / 产品名称 / 图片 / SKU / SKU编码 / ...
    # 同一 产品编码 在 Excel 里出现多行（每个 SKU 一行），导入时按编码去重。
    ws = wb[SHEET_PRODUCTS]
    header_row = _find_header_row(ws, "产品编码")
    count = 0
    seen: set[str] = set()
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        code = _str(row[0])
        category = _str(row[1])
        name = _str(row[2])
        if not code or not name or code in seen:
            continue
        if db.query(Product).filter_by(code=code).first():
            seen.add(code)
            continue
        db.add(Product(code=code, name=name, category=category))
        seen.add(code)
        count += 1
    db.commit()
    return count


def import_materials(wb, db) -> int:
    ws = wb[SHEET_PRICE]
    header_row = _find_header_row(ws, "物料编码")
    count = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        code = _str(row[0])
        name = _str(row[1])
        if not code or not name:
            continue
        if db.query(Material).filter_by(code=code).first():
            continue
        # 判断是否定制：AC 前缀且序号 >= 1000
        is_custom = False
        if code.startswith("AC-"):
            try:
                is_custom = int(code.split("-", 1)[1]) >= 1000
            except (IndexError, ValueError):
                pass
        db.add(Material(
            code=code,
            name=name,
            size_type=_str(row[2]),
            unit=_str(row[3]),
            price=_decimal(row[4]),
            remark=_str(row[5]),
            is_custom=is_custom,
        ))
        count += 1
    db.commit()
    return count


def import_bom(wb, db) -> int:
    ws = wb[SHEET_BOM]
    header_row = _find_header_row(ws, "产品编码")
    count = 0
    skipped_missing_material = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        product_code = _str(row[0])
        material_code = _str(row[4])
        if not product_code or not material_code:
            continue
        # 物料缺失则跳过（这是数据问题，不在冷启动阶段自动建档）
        if not db.query(Material).filter_by(code=material_code).first():
            skipped_missing_material += 1
            continue
        db.add(BomLine(
            product_code=product_code,
            sku=_str(row[2]),
            sku_code=_str(row[3]),
            material_code=material_code,
            unit=_str(row[8]),
            qty_per_product=_decimal(row[9]) or Decimal("1"),
        ))
        count += 1
    db.commit()
    if skipped_missing_material:
        print(f"  ! BOM 中 {skipped_missing_material} 行因物料缺失被跳过")
    return count


def import_part_inventory(wb, db) -> int:
    ws = wb[SHEET_PART_INV]
    header_row = _find_header_row(ws, "仓库名称")
    count = 0
    autocreated = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        warehouse = _str(row[0])
        code = _str(row[1])
        name = _str(row[2])
        if not warehouse or (not code and not name):
            continue
        # 优先按编码走，编码缺失时按名称走
        if code:
            existing = db.query(Material).filter_by(code=code).first()
            if not existing and name:
                # 编码在库存里但价格表里没有 → 这就是定制物料场景
                # 直接按 Excel 给出的编码建档，但要 ensure 名字以「定制」起头一致
                display_name = name if name.startswith("定制") else f"定制{name}"
                is_custom = code.startswith("AC-") and int(code.split("-", 1)[1]) >= 1000
                db.add(Material(code=code, name=display_name, is_custom=is_custom, unit=_str(row[4])))
                db.flush()
                autocreated += 1
                if is_custom:
                    exception_service.record(
                        db,
                        source_table="materials",
                        source_pk=code,
                        exception_type="missing_material_autocreated",
                        severity="warning",
                        description=(
                            f"冷启动从 4b-配件库存 自动建档定制物料 {code} ({display_name})。"
                            f"请补全 价格 / 尺寸类型 / 备注 后再投入使用。"
                        ),
                        suggestion_action="fill_material_fields",
                        context={"original_name": name, "assigned_code": code, "source": "import_excel"},
                    )
            material_code = code
        else:
            res = material_service.ensure_by_name(db, name)
            if res.created:
                autocreated += 1
            material_code = res.material.code

        db.add(PartInventory(
            warehouse=warehouse,
            material_code=material_code,
            spec=_str(row[3]),
            unit=_str(row[4]),
            physical_qty=_int(row[5]),
            locked_qty=_int(row[6]),
            remark=_str(row[18]) if len(row) > 18 else None,
        ))
        count += 1
    db.commit()
    if autocreated:
        print(f"  ! 配件库存中 {autocreated} 条物料自动建档 (Excel 价格表里缺失)")
    return count


def import_product_inventory(wb, db) -> int:
    ws = wb[SHEET_PROD_INV]
    header_row = _find_header_row(ws, "仓库名称")
    count = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        warehouse = _str(row[0])
        product_code = _str(row[1])
        if not warehouse or not product_code:
            continue
        db.add(ProductInventory(
            warehouse=warehouse,
            product_code=product_code,
            sku=_str(row[3]),
            spec=_str(row[4]),
            unit=_str(row[5]),
            physical_qty=_int(row[6]),
            locked_qty=_int(row[7]),
        ))
        count += 1
    db.commit()
    return count


def run(excel_path: Path) -> None:
    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    db = SessionLocal()
    try:
        print(f"=== Importing {excel_path} ===")
        print(f"产品总表: {import_products(wb, db)}")
        print(f"物料单价库: {import_materials(wb, db)}")
        print(f"BOM 表: {import_bom(wb, db)}")
        print(f"配件库存: {import_part_inventory(wb, db)}")
        print(f"成品库存: {import_product_inventory(wb, db)}")
        print("=== Done ===")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path", type=Path)
    args = parser.parse_args()
    run(args.excel_path)


if __name__ == "__main__":
    main()
