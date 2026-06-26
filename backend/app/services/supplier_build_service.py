# -*- coding: utf-8 -*-
"""按配件采购记录自动建供应商 + 按料推断类型 (用户 2026-06-27: 根据主要供应商自动建分类)。

现有 auto_create_suppliers 是"一批一个类型"; 这里**逐供应商**按它采购的物料名推断 supplier_type
(岩板→岩板厂 / 贴皮木皮→贴皮 / 榉木→木材 / 钢板螺丝胶→五金 / 大板→木作 …), 建档时把名字写进
alipay_counterparty_keywords 让后续支付宝备注/对账能按人名归这家。排平台/内部/非采购噪音。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import PartPurchase
from app.models.supplier import Supplier
from app.services import internal_accounts

# 物料名关键词 → supplier_type (顺序敏感, 先匹配先归; 与 SUPPLIER_TYPES 对齐)
_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("rock_slab", ["岩板"]),
    ("finish_panel", ["洞石", "饰面板", "背板", "纹理饰"]),
    ("veneer", ["贴皮", "木皮", "皮料"]),
    ("power_track", ["电力轨道", "xpower", "power轨", "明装电力"]),
    ("beech_wood", ["榉木", "实木", "木方", "木材", "原木"]),
    ("plywood", ["多层板", "夹板", "胶合板", "生态板"]),
    ("glass", ["玻璃", "镜"]),
    ("hardware", ["五金", "钢板", "螺丝", "螺栓", "螺母", "双面胶", "结构胶", "免钉胶", "把手", "拉手",
                  "铰链", "导轨", "滑轨", "轨道", "灯带", "变压器", "磁", "挂", "支撑", "气撑", "锁"]),
    ("logistics", ["物流", "快递", "运费", "德邦", "顺丰", "壹米", "货运"]),
    # 木作只认真正的木作料关键词; 柜/床/桌/定制/备货 是"产品描述词"(配件备注常带, 描述这料给啥产品用),
    # 当木作关键词会把钢板/岩板供应商误判成木作, 故不收。
    ("woodwork", ["大板", "木作", "板材"]),
]

# 非供应商对手方关键词 (平台/支付/内部) — 与 suppliers.py 口径一致
_NON_SUPPLIER_KW = (
    "淘宝", "天猫", "淘天", "支付宝", "余额宝", "红包", "退款", "还款", "手续费", "服务费",
    "工资", "转账", "提现", "花呗", "借呗", "微信", "财付通", "保证金", "理财", "申购",
    "拼多多平台", "平台商户", "收钱码", "代扣", "代付",
)


def infer_supplier_type(material_names: list[str]) -> str:
    """按一组物料名给供应商推断类型 (命中最多的类型胜出, 无命中→other)。"""
    hits: dict[str, int] = defaultdict(int)
    blob = " ".join(n or "" for n in material_names)
    for stype, kws in _TYPE_RULES:
        c = sum(blob.count(k) for k in kws)
        if c:
            hits[stype] += c
    if not hits:
        return "other"
    return max(hits.items(), key=lambda x: x[1])[0]


def _is_noise(name: str) -> bool:
    if not name or not name.strip():
        return True
    if any(k in name for k in _NON_SUPPLIER_KW):
        return True
    # 内部账户主人/内部人员 (爱群/魏佳音/畔色…) 不是外部供应商
    if internal_accounts.is_imported_account_owner(name) or internal_accounts.is_internal_counterparty(name):
        return True
    return False


def auto_build_from_purchases(db: Session, *, apply: bool = False,
                              min_count: int = 1) -> dict:
    """从 PartPurchase.supplier 逐供应商建档 + 按料推断类型。

    apply=False: 只预览(返回每家推断的类型/采购次数/金额/物料样例)。
    apply=True: 建档(跳过已存在名字; alipay_counterparty_keywords=[名字])。
    """
    known: set[str] = set()
    for s in db.execute(select(Supplier)).scalars().all():
        known.add(s.name)
        for kw in (s.alipay_counterparty_keywords or []):
            if kw:
                known.add(kw)

    by_sup: dict[str, list] = defaultdict(list)
    for sup, mname, amt in db.execute(
        select(PartPurchase.supplier, PartPurchase.material_name, PartPurchase.amount)
        .where(PartPurchase.supplier.isnot(None), PartPurchase.supplier != "")
    ).all():
        by_sup[sup].append((mname, amt))

    items: list[dict] = []
    created = 0
    for sup, rows in sorted(by_sup.items(), key=lambda kv: -sum(abs(float(r[1] or 0)) for r in kv[1])):
        if len(rows) < min_count or _is_noise(sup):
            continue
        already = sup in known or any(k and (k in sup or sup in k) for k in known)
        mats = [m for m, _ in rows]
        stype = infer_supplier_type(mats)
        total = sum((Decimal(str(abs(a or 0))) for _, a in rows), Decimal("0"))
        rec = {
            "name": sup, "supplier_type": stype, "purchase_count": len(rows),
            "total_amount": float(total), "materials_sample": [m for m in mats if m][:4],
            "already_exists": already,
        }
        if not already and apply:
            db.add(Supplier(name=sup, supplier_type=stype,
                            alipay_counterparty_keywords=[sup], is_active=True))
            known.add(sup)
            created += 1
            rec["created"] = True
        items.append(rec)
    if apply and created:
        db.commit()
    return {"applied": apply, "created": created,
            "candidates": len([i for i in items if not i["already_exists"]]),
            "items": items}
