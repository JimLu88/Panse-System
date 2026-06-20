"""存量多产品单回填: 解析 order.remark 的"本单含N个商品"其余项 → 建 order_details(source='import')
行级商品行 → 重算成本(按行汇总)。杜绝塌单漏算的一次性历史回填(未来导入已自动写行)。

服务行(商家安装/送货入户)跳过; 解析不到 PricingSku 的标 unresolved 不动(需重导宝贝明细)。
用 --apply 才落库, 默认 dry-run。
"""
import re
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.order import Order, OrderDetail
from app.models.pricing import PricingSku
from app.services import order_cost_service

APPLY = "--apply" in sys.argv
_SERVICE = ("商家安装", "送货入户", "安装", "送货")
_EXTRA_RE = re.compile(r"(.+?)/(.*?)×(\d+)\s*$")


def _is_service(name: str, sku: str) -> bool:
    s = f"{name}{sku}"
    return any(k in s for k in _SERVICE)


def _resolve_sku(db, prod_name: str, sku_name: str):
    """按 sku 名精确 → 包含 解析 PricingSku, 返回 (sku_code, physical_cost) 或 None。"""
    sku_name = (sku_name or "").strip()
    if sku_name:
        ps = db.execute(select(PricingSku).where(PricingSku.sku == sku_name)).scalars().first()
        if ps is None:
            ps = db.execute(select(PricingSku).where(PricingSku.sku.like(f"%{sku_name}%"))).scalars().first()
        if ps is not None and ps.physical_cost is not None:
            return ps.sku_code, ps.physical_cost
    return None


def main():
    db = SessionLocal()
    rows = db.execute(select(Order).where(Order.remark.like("%本单含%个商品%"))).scalars().all()
    print(f"存量多产品单: {len(rows)}\n")
    for o in rows:
        m = re.search(r"其余:\s*(.+)$", o.remark or "")
        extras = []
        unresolved = []
        if m:
            for seg in m.group(1).split(";"):
                seg = seg.strip()
                em = _EXTRA_RE.match(seg)
                if not em:
                    pn, sn, q = seg, seg, 1
                else:
                    pn, sn, q = em.group(1).strip(), em.group(2).strip(), int(em.group(3))
                if _is_service(pn, sn):
                    continue
                r = _resolve_sku(db, pn, sn)
                if r:
                    extras.append((r[0], int(q), r[1], sn))
                else:
                    unresolved.append(sn or pn)
        lines = []
        if o.sku_code:
            ps = db.execute(select(PricingSku).where(PricingSku.sku_code == o.sku_code)).scalars().first()
            pc = ps.physical_cost if ps else None
            lines.append((o.sku_code, int(o.qty or 1), pc, "主"))
        lines += [(c, q, pcx, sn) for (c, q, pcx, sn) in extras]
        priced = [l for l in lines if l[2] is not None]
        tag = "✓回填" if (len(priced) >= 2 and APPLY) else ("可回填" if len(priced) >= 2 else "跳过")
        total = sum((l[2] * l[1] for l in priced), 0) if priced else None
        print(f"{o.order_no} st={o.status} 付{o.paid_amount} 理(旧){o.theoretical_cost} "
              f"| 解析{len(lines)}行(可定价{len(priced)}) 汇总≈{total} | 未解析{unresolved} | {tag}")
        for c, q, pcx, sn in lines:
            print(f"    - {c} x{q} 成本{pcx} [{(sn or '')[:18]}]")
        if len(priced) >= 2 and APPLY:
            for idx, (c, q, pcx, sn) in enumerate(lines):
                sk = f"line:{o.order_no}:{idx}"
                ex = db.execute(select(OrderDetail).where(OrderDetail.sync_key == sk)).scalars().first()
                if ex:
                    ex.sku_code = c; ex.qty = q; ex.source = "import"
                else:
                    db.add(OrderDetail(sync_key=sk, order_no=o.order_no, sku_code=c, qty=q, source="import"))
            db.flush()
            order_cost_service.recompute_and_save(db, o)
            print(f"    -> 理(新){o.theoretical_cost}")
    if APPLY:
        db.commit()
        print("\n已落库。")
    else:
        print("\nDRY-RUN(加 --apply 落库)。")
    db.close()


if __name__ == "__main__":
    main()
