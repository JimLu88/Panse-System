"""月度对账中心 (用户 2026-06-27, 方向三): 统一所有「月结」供应商对账 + 一键导出。

三域(都按发货月对账, 预估=应付基准, 实际=供应商账单):
  1) 配件月结 — 五金/电力轨道/岩板/玻璃, 复用 parts_recon_service.bulk_material_recon 的月结类;
     预估=Σ发货单BOM该类外采配件, 实际=工厂/供应商月度对账总额(PartsMonthlyRecon)。
  2) 打包月结 — 预估=Σ(发货成交单 est_packing, 按 ship_date 分月); 实际=PackingBill 应付(excluded=False, 按 bill_month)。
  3) 运费月结 — 预估=Σ(发货成交单 est_logistics, 按 ship_date 分月); 实际=LogisticsBill 逐单(row_type='line')按 bill_date 月,
     某月无逐单则用月结汇总(row_type='summary')兜底(覆盖德邦逐单 + 壹米滴答月结总额)。

口径红线: 这是【供应商应付(AP)核对】, 只为"这个月供应商收我多少、对不对", **不参与产品成本分摊**——
打包/运费早已通过 physical_cost 的 nz(actual_*, est_*) 计入每单成本(口径第16条), 这里绝不能再加一遍。
只读纯计算, 不写任何表。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.finance import LogisticsBill, PackingBill
from . import parts_recon_service as prs

_CENTS = Decimal("0.01")


def _q(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(_CENTS)


def _variance_row(period: str, est: Decimal, actual: Optional[Decimal],
                  order_count: Optional[int] = None) -> dict:
    est_d = _q(est)
    if actual is None:
        return {"period": period, "estimate": float(est_d), "actual": None,
                "variance": None, "variance_pct": None, "order_count": order_count}
    act_d = _q(actual)
    var = (act_d - est_d).quantize(_CENTS)
    return {"period": period, "estimate": float(est_d), "actual": float(act_d),
            "variance": float(var),
            "variance_pct": float((var / est_d * 100).quantize(_CENTS)) if est_d > 0 else None,
            "order_count": order_count}


# ── 打包/运费的"预估" = 发货成交单的 est_packing / est_logistics 按 ship_date 月汇总 ──────
# 复用 parts_recon 的「已成交、已发货(ship_date非空)、非补单」口径, 保证三域消费窗口一致。
def _order_est_by_month(db: Session):
    pack: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    frgt: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    cnt: dict[str, int] = defaultdict(int)
    for o in prs._settled_shipped_orders(db):
        ym = prs._ym(o.ship_date)
        if not ym:
            continue
        pack[ym] += Decimal(str(o.est_packing or 0))
        frgt[ym] += Decimal(str(o.est_logistics or 0))
        cnt[ym] += 1
    return pack, frgt, cnt


def _packing_actual_by_month(db: Session) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for b in db.execute(select(PackingBill)).scalars().all():
        if b.excluded or not b.bill_month:
            continue
        out[b.bill_month] += (b.packing_fee or Decimal("0"))
    return out


def _freight_actual_by_month(db: Session) -> dict[str, Decimal]:
    """逐单(line)优先, 某月无逐单则用月结汇总(summary)兜底 —— 对齐 reconciliation_service。"""
    line: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    summ: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for b in db.execute(select(LogisticsBill)).scalars().all():
        if not b.bill_date:
            continue
        ym = f"{b.bill_date.year}-{b.bill_date.month:02d}"
        amt = b.freight_amount or Decimal("0")
        if b.row_type == "summary":
            summ[ym] += amt
        else:  # 'line' 或历史无 row_type 的逐单
            line[ym] += amt
    return {ym: (line[ym] if ym in line else summ[ym]) for ym in set(line) | set(summ)}


def _simple_domain(key: str, label: str, hint: str,
                   est_map: dict[str, Decimal], act_map: dict[str, Decimal],
                   cnt_map: dict[str, int]) -> dict:
    periods = sorted(set(est_map) | set(act_map))
    rows, t_est, t_act = [], Decimal("0"), Decimal("0")
    for ym in periods:
        est = _q(est_map.get(ym, 0))
        act = _q(act_map[ym]) if ym in act_map else None
        rows.append(_variance_row(ym, est, act, order_count=cnt_map.get(ym)))
        t_est += est
        if act is not None:
            t_act += act
    t_var = (t_act - t_est).quantize(_CENTS)
    group = {"key": key, "label": label, "rows": rows,
             "total_estimate": float(t_est), "total_actual": float(t_act),
             "total_variance": float(t_var),
             "total_variance_pct": float((t_var / t_est * 100).quantize(_CENTS)) if t_est > 0 else None}
    return {"key": key, "label": label, "settle_hint": hint, "groups": [group]}


def build_center(db: Session) -> dict:
    """月度对账中心: 三域(配件/打包/运费)× 每月 预估|实际|差异|差异%。"""
    domains: list[dict] = []

    # 1) 配件月结 (复用 bulk_material_recon, 只取月结类) ─────────────
    bm = prs.bulk_material_recon(db, granularity="month")
    parts_groups = []
    for c in bm.get("materials", []):
        if c.get("settle_mode") != "月结":
            continue
        rows = [{
            "period": p["period"], "estimate": p["standard_consume"],
            "actual": p["actual"], "variance": p["variance"],
            "variance_pct": p["variance_pct"], "order_count": p["order_count"],
        } for p in c["periods"]]
        parts_groups.append({
            "key": c["key"], "label": c["name"], "rows": rows,
            "total_estimate": c["total_standard"], "total_actual": c["total_actual"],
            "total_variance": c["total_variance"], "total_variance_pct": c["total_variance_pct"],
        })
    domains.append({"key": "parts", "label": "配件月结",
                    "settle_hint": "工厂/供应商按月填月度对账总额", "groups": parts_groups})

    # 2) 打包月结 + 3) 运费月结 ─────────────────────────────────────
    pack_est, frgt_est, cnt = _order_est_by_month(db)
    domains.append(_simple_domain(
        "packing", "打包月结", "打包供应商月度账单(OCR手写账单录入)",
        pack_est, _packing_actual_by_month(db), cnt))
    domains.append(_simple_domain(
        "freight", "运费月结", "物流账单逐单按发货月汇总(德邦逐单/壹米滴答月结)",
        frgt_est, _freight_actual_by_month(db), cnt))

    return {
        "domains": domains,
        "caliber": "供应商应付(AP)核对 · 不参与产品成本分摊(打包/运费已计入每单 physical_cost)",
        "ship_date_basis": True,
    }


def build_export_workbook(db: Session):
    """一键导出: 全部月结账单(每个月×每种月结)。汇总 sheet + 每域一个 sheet。"""
    import openpyxl

    data = build_center(db)
    wb = openpyxl.Workbook()
    head = ["域", "分类", "月份", "预估应付", "实际账单", "差异", "差异%", "发货单数"]

    def _cells(dom_label, group_label, r):
        return [dom_label, group_label, r["period"], r["estimate"],
                r["actual"] if r["actual"] is not None else "未录",
                r["variance"] if r["variance"] is not None else "",
                f'{r["variance_pct"]}%' if r["variance_pct"] is not None else "",
                r.get("order_count") or ""]

    ws = wb.active
    ws.title = "月度对账汇总"
    ws.append(head)
    for dom in data["domains"]:
        for g in dom["groups"]:
            for r in g["rows"]:
                ws.append(_cells(dom["label"], g["label"], r))

    for dom in data["domains"]:
        title = dom["label"][:31] or dom["key"]
        ws = wb.create_sheet(title)
        ws.append(head[1:])  # 该域内不重复"域"列
        for g in dom["groups"]:
            for r in g["rows"]:
                ws.append(_cells(dom["label"], g["label"], r)[1:])
    return wb
