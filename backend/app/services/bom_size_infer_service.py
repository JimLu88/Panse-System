# -*- coding: utf-8 -*-
"""BOM 尺寸 AI 推演 — 配件成本 epic 阶段1 (用户 2026-06-28)。

很多面积料 BOM 行(岩板/玻璃/洞石饰面板)尺寸缺失(remark 无 ≥2 个数字, 多为 None)。
做多单配件成本「按 BOM 用量(面积)占比分摊」(阶段3)时缺面积无法分。

本服务按 **SKU 尺寸**推演一个 est_size(标 size_status='inferred', 预估)写入, **不动 remark**:
  - 长: 从 SKU 抽 "2.1米"/"120cm"/"180*80cm"。实测岩板 49 缺尺寸行里 48 行 SKU 带长度。
  - 深: SKU 的 "长*深cm" 显式深度优先, 否则品类默认(餐桌 800 / 床头柜 400 / 柜类 480, 实测岩板柜 2100*480)。
  - SKU 完全无长度的(极少, 如"床头柜-岩板台面") → 调本地 qwen3.5:9b 推, 失败回退品类默认。

人工在前端复核页可编辑 est_size → size_status='confirmed'(二次确认)。
计算面积(阶段3 分摊)时 **remark 优先、缺则用 est_size**。

幂等: 只填 size_status 为空或 'inferred' 的行, 绝不动 'confirmed'(人工确认过的)。
run(db, apply=False) 预览 / apply=True 落库。口径红线: 只标预估、人工可纠、不污染 remark。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.material import Material
from app.services.parts_recon_service import _size_area

_log = logging.getLogger("panse.bom_size_infer")

# 面积料: 多单成本按「面积」占比分摊的分类(长×宽随尺寸变)。
# 五金/电力轨道/杂项是「个数」料 → 按 qty 分摊、不需要 est_size, 不在此列。
AREA_CATEGORIES = ("岩板", "玻璃", "洞石饰面板")

# 品类默认深度(mm) — SKU 无显式深度时按产品类型兜底(实测岩板柜深 480 / 餐桌深 ~800)。
_DEPTH_NIGHTSTAND = 400   # 床头柜台面
_DEPTH_TABLE = 800        # 餐桌/桌面
_DEPTH_CABINET = 480      # 柜类(实测 2100*480)
# 床头柜台面无长度时的兜底长度(mm) — 仅 qwen 不可达时使用。
_DEFAULT_NIGHTSTAND_LEN = 500

_AI_MODEL = "qwen3.5:9b"   # 本地文本推理(think=False 由取数 agent /api/ai/chat 关思考) — 见 project_panse-local-llm


def _parse_sku_size(sku: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """从 SKU 抽 (长mm, 深mm|None)。支持 '2.1米' / '120cm' / '180*80cm'。无→(None,None)。"""
    if not sku:
        return None, None
    # "长*深cm"(显式两维): 180*80cm / 180×80cm
    m = re.search(r"(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)\s*(?:cm|厘米)", sku)
    if m:
        return float(m.group(1)) * 10, float(m.group(2)) * 10
    # 单值"米"
    m = re.search(r"(\d+(?:\.\d+)?)\s*米", sku)
    if m:
        return float(m.group(1)) * 1000, None
    # 单值"cm/厘米"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:cm|厘米)", sku)
    if m:
        return float(m.group(1)) * 10, None
    return None, None


def _default_depth_mm(product_name: Optional[str], sku: Optional[str]) -> int:
    """按产品类型给默认深度(mm)。床头柜 400 / 餐桌(非柜) 800 / 柜类 480。"""
    t = (product_name or "") + "|" + (sku or "")
    if "床头柜" in t:
        return _DEPTH_NIGHTSTAND
    if ("餐桌" in t or "桌" in t) and "柜" not in t:
        return _DEPTH_TABLE
    return _DEPTH_CABINET


def _qwen_infer(db: Session, product_name: str, material_name: str) -> tuple[Optional[float], Optional[float]]:
    """SKU 无长度时调本地 qwen3.5 推部件尺寸。返回 (长mm, 深mm)。Agent 不可达→(None,None)不抛。"""
    from app.services import web_agent_service
    sys = ("你是家具部件尺寸推演助手。根据产品名和部件名, 估算该部件的长、深(单位 mm)。"
           "只返回 JSON, 形如 {\"length_mm\": 数字, \"depth_mm\": 数字}, 不要任何解释。")
    user = f"产品: {product_name or ''}\n部件(配件): {material_name or ''}\n请给出该部件的长 length_mm 和 深 depth_mm。"
    resp = web_agent_service._post(db, "/api/ai/chat",
                                   {"system": sys, "user": user, "max_tokens": 120,
                                    "model": _AI_MODEL, "temperature": 0.0}, timeout=130)
    if not resp.get("ok"):
        _log.info("qwen 推演不可达: %s", resp.get("error"))
        return None, None
    m = re.search(r"\{.*\}", resp.get("text") or "", re.S)
    if not m:
        return None, None
    try:
        d = json.loads(m.group(0))
        ln = float(d.get("length_mm")) if d.get("length_mm") is not None else None
        dp = float(d.get("depth_mm")) if d.get("depth_mm") is not None else None
        # 合理性护栏: 部件尺寸在 50mm~3000mm 之间才采信
        if ln is not None and not (50 <= ln <= 3000):
            ln = None
        if dp is not None and not (50 <= dp <= 3000):
            dp = None
        return ln, dp
    except Exception:  # noqa: BLE001
        return None, None


def _infer_one(db: Session, b: BomLine, *, use_ai: bool) -> Optional[dict]:
    """对单行推演尺寸。返回 {est_size, length_mm, depth_mm, source} 或 None(无法推)。"""
    ln, dp = _parse_sku_size(b.sku)
    if ln is None:
        ln2, dp2 = _parse_sku_size(b.sku_code)  # SKU 编码偶尔也带尺寸
        ln, dp = ln2, (dp2 if dp2 is not None else dp)
    source = None
    if ln is not None:
        if dp is not None:
            source = "sku长×sku深"
        else:
            dp = _default_depth_mm(b.product_name, b.sku)
            source = "sku长×品类默认深"
    else:
        # SKU 无长度 → qwen 推 → 仍失败回退品类默认
        if use_ai:
            ai_ln, ai_dp = _qwen_infer(db, b.product_name or "", b.material_name or "")
            if ai_ln is not None:
                ln = ai_ln
                dp = ai_dp if ai_dp is not None else _default_depth_mm(b.product_name, b.sku)
                source = "qwen3.5推演"
        if ln is None:
            # 兜底: 床头柜台面之类给品类默认长×深, 标"默认兜底"供人工纠
            ln = _DEFAULT_NIGHTSTAND_LEN if "床头柜" in ((b.product_name or "") + (b.sku or "")) else None
            if ln is None:
                return None
            dp = _default_depth_mm(b.product_name, b.sku)
            source = "品类默认兜底"
    est = f"{int(round(ln))}*{int(round(dp))}"
    return {"est_size": est, "length_mm": int(round(ln)), "depth_mm": int(round(dp)), "source": source}


def run(db: Session, *, categories: Optional[tuple] = None, apply: bool = False,
        use_ai: bool = True, limit: Optional[int] = None) -> dict:
    """推演面积料缺尺寸 BOM 行的 est_size。

    categories: 限定分类(默认 AREA_CATEGORIES)。apply=False 只预览不落库。
    use_ai: SKU 无长度时是否调 qwen(False 则只用 SKU 解析+品类默认, 离线可跑)。
    返回 {ok, missing, inferred, applied, by_source, items[...]}。
    """
    cats = tuple(categories) if categories else AREA_CATEGORIES
    mat_codes = [m[0] for m in db.execute(
        select(Material.code).where(Material.category.in_(cats))
    ).all()]
    if not mat_codes:
        return {"ok": True, "categories": list(cats), "missing": 0, "inferred": 0,
                "applied": 0, "by_source": {}, "items": [], "note": "该分类无物料"}

    rows = db.execute(
        select(BomLine).where(BomLine.material_code.in_(mat_codes))
    ).scalars().all()

    items: list[dict] = []
    by_source: dict[str, int] = {}
    inferred = applied = missing = 0
    for b in rows:
        # 已有可解析尺寸(remark) → 跳过, 不需推演
        if _size_area(b.remark) > 0:
            continue
        # 已人工确认 → 幂等不动
        if (b.size_status or "") == "confirmed":
            continue
        missing += 1
        if limit is not None and inferred >= limit:
            continue
        out = _infer_one(db, b, use_ai=use_ai)
        if out is None:
            items.append({"bom_id": b.id, "product_code": b.product_code, "sku": b.sku,
                          "product_name": b.product_name, "material_name": b.material_name,
                          "material_code": b.material_code, "old_remark": b.remark,
                          "est_size": None, "source": "无法推演", "area": 0})
            continue
        inferred += 1
        by_source[out["source"]] = by_source.get(out["source"], 0) + 1
        area = out["length_mm"] * out["depth_mm"]
        items.append({"bom_id": b.id, "product_code": b.product_code, "sku": b.sku,
                      "product_name": b.product_name, "material_name": b.material_name,
                      "material_code": b.material_code, "old_remark": b.remark,
                      "est_size": out["est_size"], "source": out["source"], "area": area})
        if apply:
            b.est_size = out["est_size"]
            b.size_status = "inferred"
            applied += 1

    if apply:
        db.commit()
    return {"ok": True, "categories": list(cats), "missing": missing, "inferred": inferred,
            "applied": applied, "by_source": by_source, "items": items}
