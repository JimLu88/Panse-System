"""Generate the human-readable final dimension notes published to Panse ERP.

The text files live beside the final SVG/PNG files.  They are intentionally
plain UTF-8 files so a non-technical user can edit them directly.  Existing
files are preserved unless ``--force`` is supplied.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path


DEFAULT_ROOT = Path(r"D:\SynologyDrive\2026\尺寸图_矢量编辑")


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _variant_summary(variants: list[dict]) -> list[str]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for variant in variants:
        for measurement in variant.get("resolved_dimensions") or []:
            label = str(measurement.get("label") or "尺寸").strip()
            value = measurement.get("value_mm")
            if isinstance(value, (int, float)):
                rounded = int(value) if float(value).is_integer() else value
                if rounded not in grouped[label]:
                    grouped[label].append(rounded)
    lines: list[str] = []
    for label, values in grouped.items():
        ordered = sorted(values)
        lines.append(f"{label}：{'/'.join(str(value) for value in ordered)} mm")
    return lines


def build_text(item: dict) -> str:
    product = str(item.get("product") or item.get("erp_name") or "未命名产品").strip()
    erp_name = str(item.get("erp_name") or product).strip()
    code = str(item.get("erp_code") or "").strip()
    confirmed = _unique(
        f"{dimension.get('label', '尺寸')}：{dimension.get('value', '')}"
        for dimension in (item.get("erp_dimensions") or [])
    )
    variants = _variant_summary(item.get("erp_variants") or [])

    known = "\n".join(confirmed + variants)
    supplements: list[str] = []
    for label in item.get("dimension_labels") or []:
        label = str(label or "").strip()
        if not label or re.fullmatch(r"[\d.]+(?:\s*mm)?", label, re.I):
            continue
        if label in known or any(label in line or line in label for line in confirmed):
            continue
        supplements.append(label)
    supplements = _unique(supplements)

    lines = [f"产品：{erp_name}", f"ERP 编码：{code}", "", "已确认尺寸："]
    lines.extend(f"- {line}" for line in (confirmed or ["暂无 ERP 已确认尺寸"]))
    if variants:
        lines.extend(["", "SKU 可选规格："])
        lines.extend(f"- {line}" for line in variants)
    if supplements:
        lines.extend(["", "图中补充说明："])
        lines.extend(f"- {line}" for line in supplements)
    if item.get("review_required"):
        lines.extend(["", "状态：产品映射或推测尺寸仍需人工复核。"])
    else:
        lines.extend(["", "状态：已匹配 ERP 产品。"])
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成最终尺寸文字说明 TXT")
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--force", action="store_true", help="覆盖已经人工修改的说明文件")
    args = parser.parse_args()

    root = args.asset_root.resolve()
    index_path = root / "manual_asset_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    products = index.get("products") or []
    if len(products) != 39:
        raise SystemExit(f"资产索引应为 39 份，实际 {len(products)}")

    created = 0
    preserved = 0
    for item in products:
        target = root / f"{item['product']}_说明.txt"
        if target.exists() and not args.force:
            preserved += 1
            continue
        target.write_text(build_text(item), encoding="utf-8")
        created += 1
    print(json.dumps({"ok": True, "created": created, "preserved": preserved}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
