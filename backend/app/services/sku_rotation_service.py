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

from sqlalchemy import select
from sqlalchemy.orm import Session

_SIZE_RE = re.compile(r"(\d+\.?\d*)\s*米")


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
