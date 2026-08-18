"""超大促 SKU 轮换生成器 (2026-07-11 拍板, 见 价格体系设置.md §九)。

把"尺寸标签在 SKU 槽位间循环下移"算成:
  1. 千牛指令: 每个物理 skuId 这轮要改成的 (商家编码 / 规格 / 价格);
  2. ERP 新映射: 轮换后 sku_code ↔ taobao_sku_id (商家编码永远跟尺寸走)。

铁律: 商家编码(=sku_code)永远绑死尺寸, 只有它对应的 skuId 在轮换 → ERP 不串位。
plan_rotation() 只算不改; apply_mapping() 才写 PricingSkuPromo.taobao_sku_id (dry_run 默认)。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

_SIZE_RE = re.compile(r"(\d+\.?\d*)\s*米")
_RETIRED_MERCHANT_CODE_RE = re.compile(r"\d{1,4}")


def _size_of(name: str) -> float | None:
    m = _SIZE_RE.search(name or "")
    return float(m.group(1)) if m else None


def _ladder_key(name: str) -> str:
    """去掉尺寸后的名字 = 阶梯键 (同材质同类型只尺寸不同 → 同阶梯)。"""
    return _SIZE_RE.sub("", name or "").replace("  ", " ").strip()


def _f(x):
    return None if x is None else float(x)


def plan_rotation(db: Session, product_code: str) -> dict:
    """产品的轮换计划(只算不改)。按尺寸阶梯分组, 每条阶梯配一个定制 buffer 槽, 标签整体下移一位。"""
    from app.services import campaign_price_protection_service

    if not campaign_price_protection_service.rotation_enabled(db):
        return campaign_price_protection_service.rotation_block_result()
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo

    skus = db.execute(select(PricingSku).where(
        PricingSku.product_code == product_code).order_by(PricingSku.sku_code)).scalars().all()
    if not skus:
        return {"ok": False, "error": f"产品 {product_code} 无 SKU"}
    promo = {p.sku_code: p for p in db.execute(
        select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_([s.sku_code for s in skus]))).scalars().all()}

    def sid(sc: str):
        p = promo.get(sc)
        return str(p.taobao_sku_id) if p and p.taobao_sku_id else None

    # 分组: 真实(非占位)按阶梯键分组; 占位符(定制)单列作 buffer 候选池
    ladders: dict[str, list] = defaultdict(list)
    buffers: list = []
    for s in skus:
        if getattr(s, "is_custom_placeholder", False):
            buffers.append(s)
        else:
            ladders[_ladder_key(s.sku or "")].append(s)

    _used: set = set()

    def pick_buffer(ladder_name: str):
        """按材质关键词(榉木/松木/黑胡桃等)给阶梯挑一个还没被占用的定制 buffer 槽。"""
        for mat in ("榉木", "松木", "黑胡桃", "樱桃", "橡木"):
            if mat in ladder_name:
                for b in buffers:
                    if b not in _used and mat in (b.sku or ""):
                        return b
        for b in buffers:  # 兜底: 任意未用 buffer
            if b not in _used:
                return b
        return None

    out_ladders = []
    for key, group in ladders.items():
        # 按尺寸降序 (2.1 → 1.2); 无尺寸的排最后
        group = sorted(group, key=lambda s: (_size_of(s.sku or "") or -1), reverse=True)
        buf = pick_buffer(key)
        warnings = []
        if buf is None:
            warnings.append("该阶梯缺定制 buffer 槽, 需先建一个定制占位符再轮换")
        else:
            _used.add(buf)
        phys = [(s.sku_code, sid(s.sku_code)) for s in group]
        buf_pair = (buf.sku_code, sid(buf.sku_code)) if buf else (None, None)
        qn_instructions = []   # 千牛: 物理skuId → 新(商家编码/规格/价格)
        erp_mapping = []       # ERP: sku_code → 新skuId
        if buf and all(p[1] for p in phys) and buf_pair[1]:
            n = len(group)
            phys_skuids = [p[1] for p in phys]        # 降序尺寸各槽 skuId
            top = group[0]
            qn_instructions.append({"skuId": buf_pair[1], "new_sku_code": top.sku_code,
                                    "new_size": top.sku, "new_price": _f(top.daily_price)})
            erp_mapping.append({"sku_code": top.sku_code, "new_skuId": buf_pair[1]})
            for i in range(1, n):
                s = group[i]
                qn_instructions.append({"skuId": phys_skuids[i - 1], "new_sku_code": s.sku_code,
                                        "new_size": s.sku, "new_price": _f(s.daily_price)})
                erp_mapping.append({"sku_code": s.sku_code, "new_skuId": phys_skuids[i - 1]})
            hi_price = max((_f(s.daily_price) or 0) for s in group) * 1.5 or None
            qn_instructions.append({"skuId": phys_skuids[n - 1], "new_sku_code": buf_pair[0],
                                    "new_size": (buf.sku if buf else "定制"),
                                    "new_price": round(hi_price, 2) if hi_price else None})
            erp_mapping.append({"sku_code": buf_pair[0], "new_skuId": phys_skuids[n - 1]})
        elif not warnings:
            warnings.append("阶梯或 buffer 缺 skuId 映射, 补齐淘宝映射后再轮换")
        out_ladders.append({
            "ladder": key, "sizes": [s.sku for s in group], "buffer": (buf.sku_code if buf else None),
            "qn_instructions": qn_instructions, "erp_mapping": erp_mapping, "warnings": warnings,
        })

    return {"ok": True, "product_code": product_code,
            "ladder_count": len(out_ladders), "buffer_pool": [b.sku_code for b in buffers],
            "ladders": out_ladders}


def apply_mapping(db: Session, product_code: str, erp_mapping: list[dict], *, dry_run: bool = True) -> dict:
    """轮换后同步 ERP: 按 [{sku_code, new_skuId}] 重写 PricingSkuPromo.taobao_sku_id。
    dry_run=True 只报变化不落库。★这是唯一写库的口, 千牛真轮换完才 dry_run=False。"""
    from app.models.pricing_ext import PricingSkuPromo
    from app.services import campaign_price_protection_service

    if not campaign_price_protection_service.rotation_enabled(db):
        return campaign_price_protection_service.rotation_block_result()
    changes = []
    for row in erp_mapping:
        sc, new_sid = row.get("sku_code"), row.get("new_skuId")
        if not sc or not new_sid:
            continue
        p = db.execute(select(PricingSkuPromo).where(PricingSkuPromo.sku_code == sc)).scalar_one_or_none()
        old = (str(p.taobao_sku_id) if p and p.taobao_sku_id else None)
        if old != str(new_sid):
            changes.append({"sku_code": sc, "old_skuId": old, "new_skuId": str(new_sid)})
            if not dry_run and p is not None:
                p.taobao_sku_id = str(new_sid)
                # 券后线和已生效活动价都属于物理 skuId 的平台历史，不属于商家编码。
                # 轮换后若继续挂在新 skuId 上，会把旧槽位的低价历史误当成新槽位限制。
                p.coupon_floor_price = None
                p.enrolled_floor_price = None
    if not dry_run:
        db.flush()
    return {"ok": True, "dry_run": dry_run, "changed": len(changes), "changes": changes}


def preview_export_mapping_refresh(
        db: Session,
        workbooks: Iterable[bytes],
        *,
        item_ids: Iterable[str],
) -> dict:
    """Read exact ``商家编码 -> skuId`` pairs from Taobao product exports.

    This is the safe counterpart to :func:`plan_rotation` when operators have
    already created new physical SKU rows in Taobao.  Only rows carrying an
    exact SKU-level merchant code are considered; old rows whose merchant code
    was removed and name/spec similarities can never influence the mapping.
    """
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.models.taobao_listing import TaobaoListing
    from app.services import taobao_listing_service

    requested = {str(value or "").strip() for value in item_ids}
    if not requested or any(not value.isdigit() for value in requested):
        return {"ok": False, "error": "item_ids 必须是非空的数字商品ID集合"}

    records: list[dict] = []
    warnings: list[str] = []
    for file_bytes in workbooks:
        parsed, file_warnings = taobao_listing_service.parse_rows(file_bytes)
        records.extend(parsed)
        warnings.extend(file_warnings)

    scoped = [
        row for row in records
        if str(row.get("taobao_item_id") or "").strip() in requested
        and str(row.get("sku_code_raw") or "").strip()
    ]
    retired_marker_rows = [
        row for row in scoped
        if _RETIRED_MERCHANT_CODE_RE.fullmatch(
            str(row.get("sku_code_raw") or "").strip())
    ]
    explicit = [row for row in scoped if row not in retired_marker_rows]
    found_items = {str(row.get("taobao_item_id") or "").strip() for row in explicit}
    missing_items = sorted(requested - found_items)
    if missing_items:
        return {
            "ok": False,
            "error": "导出表缺少带SKU商家编码的新行",
            "missing_item_ids": missing_items,
            "warnings": warnings,
        }

    sku_codes = {str(row.get("sku_code_raw") or "").strip() for row in explicit}
    skus = {
        row.sku_code: row
        for row in db.execute(
            select(PricingSku).where(PricingSku.sku_code.in_(sku_codes))
        ).scalars()
    }
    promos = {
        row.sku_code: row
        for row in db.execute(
            select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(sku_codes))
        ).scalars()
    }
    all_promos = db.execute(select(PricingSkuPromo)).scalars().all()
    all_promos_by_sid: dict[str, str] = {}
    all_promos_by_code = {promo.sku_code: promo for promo in all_promos}
    for promo in all_promos:
        sid = str(promo.taobao_sku_id or "").strip()
        if sid:
            all_promos_by_sid.setdefault(sid, promo.sku_code)

    # Operators clear retired Taobao rows by replacing the SKU merchant code
    # with a short numeric marker (for example 73/97/98).  Those rows are not
    # ERP mappings.  If their physical skuId is still owned by this same item,
    # preserve the historical mapping but register the skuId as retired so the
    # campaign builders cannot accidentally submit it again.
    errors: list[str] = []
    retired_rows: list[dict] = []
    for row in retired_marker_rows:
        item_id = str(row.get("taobao_item_id") or "").strip()
        sku_id = str(row.get("taobao_sku_id") or "").strip()
        marker = str(row.get("sku_code_raw") or "").strip()
        owner = all_promos_by_sid.get(sku_id)
        promo = all_promos_by_code.get(owner) if owner else None
        if not sku_id:
            warnings.append(f"{item_id}/退役标记{marker}缺少skuId，已忽略")
            continue
        if promo is None:
            warnings.append(f"{item_id}/{sku_id}退役标记{marker}无现存ERP映射，已忽略")
            continue
        if str(promo.taobao_item_id or "").strip() != item_id:
            errors.append(
                f"退役 skuId {sku_id} 当前属于其它商品: {promo.taobao_item_id}/{owner}"
            )
            continue
        retired_rows.append({
            "taobao_item_id": item_id,
            "taobao_sku_id": sku_id,
            "sku_code": owner,
            "retired_marker": marker,
            "sku_spec": row.get("sku_spec"),
        })

    mappings: list[dict] = []
    seen_codes: set[str] = set()
    seen_sids: set[str] = set()
    for row in explicit:
        item_id = str(row.get("taobao_item_id") or "").strip()
        sku_id = str(row.get("taobao_sku_id") or "").strip()
        sku_code = str(row.get("sku_code_raw") or "").strip()
        product_code = str(row.get("merchant_code") or "").strip()
        if not sku_id:
            errors.append(f"{item_id}/{sku_code} 缺少 skuId")
            continue
        if sku_code in seen_codes:
            errors.append(f"导出表中SKU商家编码重复: {sku_code}")
            continue
        if sku_id in seen_sids:
            errors.append(f"导出表中新 skuId 重复: {sku_id}")
            continue
        seen_codes.add(sku_code)
        seen_sids.add(sku_id)

        sku = skus.get(sku_code)
        promo = promos.get(sku_code)
        if sku is None:
            errors.append(f"ERP不存在SKU商家编码: {sku_code}")
            continue
        if promo is None:
            errors.append(f"ERP活动映射不存在SKU商家编码: {sku_code}")
            continue
        if product_code and product_code != str(sku.product_code or ""):
            errors.append(
                f"{sku_code} 的产品商家编码不符: 导出={product_code}, ERP={sku.product_code}"
            )
            continue
        if str(promo.taobao_item_id or "").strip() != item_id:
            errors.append(
                f"{sku_code} 商品ID不符: 导出={item_id}, ERP={promo.taobao_item_id}"
            )
            continue
        owner = all_promos_by_sid.get(sku_id)
        if owner and owner != sku_code:
            errors.append(f"新 skuId {sku_id} 已绑定其它ERP SKU: {owner}")
            continue
        listing_conflicts = db.execute(
            select(TaobaoListing).where(TaobaoListing.taobao_sku_id == sku_id)
        ).scalars().all()
        for listing in listing_conflicts:
            if (
                str(listing.taobao_item_id or "") != item_id
                or (listing.sku_code and listing.sku_code != sku_code)
            ):
                errors.append(
                    f"新 skuId {sku_id} 已存在冲突商品映射: "
                    f"{listing.taobao_item_id}/{listing.sku_code}"
                )
                break
        mappings.append({
            "taobao_item_id": item_id,
            "taobao_sku_id": sku_id,
            "sku_code": sku_code,
            "product_code": sku.product_code,
            "old_sku_id": str(promo.taobao_sku_id or "").strip() or None,
            "changed": str(promo.taobao_sku_id or "").strip() != sku_id,
            "title": row.get("title"),
            "merchant_code": row.get("merchant_code"),
            "sku_spec": row.get("sku_spec"),
            "category_name": row.get("category_name"),
            "list_price": row.get("list_price"),
            "sku_price": row.get("sku_price"),
            "stock": row.get("stock"),
        })

    if errors:
        return {"ok": False, "error": "；".join(errors), "errors": errors,
                "warnings": warnings}
    mappings.sort(key=lambda row: (row["taobao_item_id"], row["sku_code"]))
    return {
        "ok": True,
        "requested_item_ids": sorted(requested),
        "explicit_mapping_rows": len(mappings),
        "changed_rows": sum(1 for row in mappings if row["changed"]),
        "retired_mapping_rows": len(retired_rows),
        "retired_rows": sorted(
            retired_rows,
            key=lambda row: (row["taobao_item_id"], row["taobao_sku_id"]),
        ),
        "warnings": warnings,
        "mappings": mappings,
    }


def apply_export_mapping_refresh(
        db: Session,
        workbooks: Iterable[bytes],
        *,
        item_ids: Iterable[str],
        dry_run: bool = True,
) -> dict:
    """Apply a verified Taobao-export mapping refresh in one transaction."""
    import json

    from app.models.pricing_ext import PricingSkuPromo
    from app.models.taobao_listing import TaobaoListing
    from app.services import delisted_sku_service, settings_service

    report = preview_export_mapping_refresh(db, workbooks, item_ids=item_ids)
    if not report.get("ok") or dry_run:
        return {**report, "dry_run": dry_run}

    changed: list[dict] = []
    for mapping in report["mappings"]:
        promo = db.execute(
            select(PricingSkuPromo).where(
                PricingSkuPromo.sku_code == mapping["sku_code"]
            )
        ).scalar_one()
        listing = db.execute(
            select(TaobaoListing).where(
                TaobaoListing.taobao_item_id == mapping["taobao_item_id"],
                TaobaoListing.taobao_sku_id == mapping["taobao_sku_id"],
            )
        ).scalar_one_or_none()
        listing_payload = {
            key: mapping.get(key) for key in (
                "taobao_item_id", "taobao_sku_id", "sku_code", "product_code",
                "title", "merchant_code", "sku_spec", "category_name",
                "list_price", "sku_price", "stock",
            )
        }
        listing_payload["matched"] = True
        if listing is None:
            db.add(TaobaoListing(**listing_payload))
        else:
            for key, value in listing_payload.items():
                setattr(listing, key, value)

        if mapping["changed"]:
            promo.taobao_sku_id = mapping["taobao_sku_id"]
            # Old physical SKU IDs must remain only in taobao_listings for order
            # history.  Keeping them as alternatives would re-import the very
            # history line the operator rotated away from.
            promo.alt_taobao_sku_ids = []
            promo.coupon_floor_price = None
            promo.enrolled_floor_price = None
            changed.append({
                "taobao_item_id": mapping["taobao_item_id"],
                "sku_code": mapping["sku_code"],
                "old_sku_id": mapping["old_sku_id"],
                "new_sku_id": mapping["taobao_sku_id"],
            })
    retired_ids = {
        str(row.get("taobao_sku_id") or "").strip()
        for row in report.get("retired_rows") or []
    } - {""}
    if retired_ids:
        current = delisted_sku_service.get_delisted(db)
        settings_service.set_value(
            db,
            "delisted_skuids",
            json.dumps(sorted(current | retired_ids), ensure_ascii=False),
            description="下架SKU登记(报名自动排除: 在售全报、下架不报)",
        )
    db.commit()
    return {
        **report,
        "dry_run": False,
        "changed": changed,
        "retired_sku_ids": sorted(retired_ids),
    }
