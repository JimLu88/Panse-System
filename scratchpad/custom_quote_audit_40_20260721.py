# -*- coding: utf-8 -*-
"""定制报价 40 项只读审计（30 个现有产品改动 + 10 个全定制）。

读取生产库，调用当前报价服务，绝不写数据库。输出 JSON 和 Markdown 到 /tmp。
价格口径：
  系统卡1 = 现有产品 SKU 锚点报价（全定制则为品类模板报价）
  系统卡2 = 当前系统的产品 BOM / 自动板单报价
  核算价  = 对系统卡2返回的逐行板单重新调用重报价引擎，油漆按最终零售追加价另加
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import custom_board_template as tpl
from app.services import custom_quote_config_service as ccfg
from app.services import custom_quote_v2_service as v2


EXISTING = [
    ("PPS24210070901", "餐桌长度改成1.65米，桌面深度85厘米，其他照旧", 1.65, 85, 75, None, [], []),
    ("PPS25210080401", "餐桌做1.50米，整张改成奶油白油漆", 1.50, 80, 75, None, ["奶油白油漆上色"], []),
    ("PPS25210100410", "原餐桌做短一点1.40米，主木料换樱桃木", 1.40, 80, 75, "樱桃木", [], []),
    ("PPS25210110415", "餐桌桌面加宽到90厘米，桌高不变", 1.60, 90, 75, None, [], []),
    ("PPS25210090405", "伸缩餐桌固定做1.80米，不要伸缩结构", 1.80, 85, 75, None, [], ["伸缩结构"]),
    ("PPS23210030202", "餐桌改1.35米，桌角稍微做圆一点", 1.35, 75, 75, None, [], []),
    ("PPS24210040513", "餐桌换成黑胡桃木，尺寸1.60乘0.80米", 1.60, 80, 75, "黑胡桃", [], []),
    ("PPS24210050510", "岩板餐桌做1.90米，桌面加宽5厘米", 1.90, 90, 75, None, [], []),
    ("PFG25250011225", "餐边柜下柜高改成90厘米，宽1.50米", 1.50, 40, 90, None, [], []),
    ("PFG25250031226", "餐边柜做1.60米，整柜改胡桃色油漆", 1.60, 42, 85, None, ["胡桃色油漆上色"], []),
    ("PFG25250051220", "薄餐边柜深度从原款改到32厘米", 1.40, 32, 85, None, [], []),
    ("PFG25250061201", "窄餐边柜上柜加高25厘米，下面不动", 1.00, 38, 115, None, ["上柜"], []),
    ("PFG25250071230", "半高餐边柜改成1.80米宽，门板不要一扇", 1.80, 42, 100, None, [], ["门板"]),
    ("PFG26250020110", "柚木餐边柜换樱桃木，尺寸照旧", 1.50, 40, 85, "樱桃木", [], []),
    ("PFG26250040110", "多功能餐边柜右边加两个抽屉", 1.60, 45, 90, None, ["抽屉面板", "抽屉面板"], []),
    ("PPS24250080801", "洞石餐边柜背板换成黑色岩板", 1.50, 40, 90, None, ["黑色岩板背板"], ["原背板"]),
    ("PPS25250090403", "樱桃木底座餐边柜长度改1.70米", 1.70, 42, 88, None, [], []),
    ("PPS25250100405", "窄柜做60厘米宽，深度改30厘米", 0.60, 30, 95, None, [], []),
    ("PPS25250130420", "太空舱餐边柜去掉中间灯带", 1.50, 42, 90, None, [], ["灯带"]),
    ("PPS26380040225", "榉木床头柜宽度改50厘米", 0.50, 40, 48, None, [], []),
    ("PPS26380050301", "樱桃木床头边柜加高到55厘米", 0.45, 40, 55, None, [], []),
    ("PPS26380060320", "胡桃木床头柜改成双抽", 0.48, 42, 50, None, ["抽屉面板"], []),
    ("PPS26390070315", "窄床头柜做35厘米宽，换黄铜拉手", 0.35, 36, 50, None, ["黄铜拉手"], ["原拉手"]),
    ("PPS24350010410", "抽屉柜长度做1.20米，少一个抽屉", 1.20, 45, 85, None, [], ["抽屉面板"]),
    ("PPS26330100225", "樱桃木床改适配1.8乘2米床垫", 1.80, 200, 105, None, [], []),
    ("PPS26330110226", "静音床床头高度降到95厘米", 1.80, 200, 95, None, [], []),
    ("PPS26330150118", "胡桃木悬浮床加两边悬浮床头板", 1.80, 200, 105, None, ["床头板", "床头板"], []),
    ("PPS23410020201", "书桌改1.30米，桌深65厘米", 1.30, 65, 75, None, [], []),
    ("PPS23450010201", "书柜宽度改1.10米，上柜加一层层板", 1.10, 35, 200, None, ["上柜层板"], []),
    ("PPS24410040513", "升降桌桌面改1.50乘0.75米，换白橡木色", 1.50, 75, 75, "白橡木", [], []),
]

FULL_CUSTOM = [
    ("全定制餐桌：2.2×0.95×0.75米，黑胡桃木桌面，奶油白油漆桌腿", "餐桌", 2.20, 95, 75, "黑胡桃木-2.2cm", ["奶油白油漆上色"]),
    ("全定制餐边柜：1.8×0.45×0.9米，四门三抽，樱桃木，整柜清漆上色", "餐边柜", 1.80, 45, 90, "樱桃木-2.2cm", ["清漆上色"]),
    ("全定制岛台：1.6×0.8×0.95米，带岩板台面、电力轨道和两抽", "岛台", 1.60, 80, 95, "榉木-2.2cm", []),
    ("全定制书柜：2.4×0.35×2.2米，下柜门上开放格，带灯带", "书柜", 2.40, 35, 220, "樱桃木-2.2cm", []),
    ("全定制鞋柜：1.2×0.35×2.1米，中间留空，底部悬空", "鞋柜", 1.20, 35, 210, "榉木-2.2cm", []),
    ("全定制电视柜：2.4×0.42×0.45米，三抽两翻门，黑胡桃木", "电视柜", 2.40, 42, 45, "黑胡桃木-2.2cm", []),
    ("全定制书桌：1.8×0.7×0.75米，左侧三抽，右侧走线孔", "书桌", 1.80, 70, 75, "樱桃木-2.2cm", []),
    ("全定制床：适配2×2米床垫，悬浮床体，软包床头", "床", 2.00, 220, 105, "黑胡桃木-2.2cm", []),
    ("全定制床头柜：0.55×0.42×0.52米，双抽，黄铜拉手", "床头柜", 0.55, 42, 52, "樱桃木-2.2cm", []),
    ("全定制茶几：1.3×0.7×0.42米，一边抽屉一边开放格", "茶几", 1.30, 70, 42, "黑胡桃木-2.2cm", []),
]


def paint_parts(names):
    return [{"material": name, "qty": 1, "is_paint": True} for name in names if "漆" in name or "上色" in name]


def normal_parts(names):
    return [{"material": name, "qty": 1} for name in names if "漆" not in name and "上色" not in name]


def audit_boards(db, category, length, width, height, boards, paint_amount=0):
    if not boards:
        return None
    q = v2.quote_heavy(db, product_type=category.split("-")[-1], length_m=length,
                       boards=boards, overall_width_m=width / 100,
                       overall_height_m=height / 100)
    value = q.get("final_price")
    return round(float(value) + float(paint_amount or 0), 2) if value is not None else None


def main():
    db = SessionLocal()
    rows = []
    try:
        for i, (code, req, length, width, height, wood, adds, removes) in enumerate(EXISTING, 1):
            product = db.query(Product).filter(Product.code == code).first()
            add = normal_parts(adds) + paint_parts(adds)
            rm = [{"material": x, "qty": 1} for x in removes]
            rec = {"序号": i, "类型": "现有产品改动", "产品编码": code,
                   "产品": product.name if product else "未找到", "需求": req}
            if not product:
                rec.update({"系统卡1": None, "系统卡2": None, "核算价": None, "结论": "产品不存在"})
                rows.append(rec)
                continue
            both = v2.quote_both(
                db, base_product_code=code, category=product.category,
                target_length_m=length, target_width_cm=width, target_height_cm=height,
                target_material=wood, add_parts=add, remove_parts=rm,
                price_tier="daily", description=req)
            card1 = (both.get("spec") or {}).get("final_price")
            card2 = (both.get("custom") or {}).get("final_price")
            audit = audit_boards(db, product.category or "", length, width, height,
                                 both.get("custom_boards") or [],
                                 (both.get("custom") or {}).get("paint_surcharge", 0))
            issues = []
            if card1 is None:
                issues.append((both.get("spec") or {}).get("error") or "系统卡1无价格")
            if card2 is None:
                issues.append((both.get("custom") or {}).get("error") or "系统卡2无价格")
            if card2 is not None and audit is not None and abs(float(card2) - audit) > 0.01:
                issues.append("系统卡2与逐行复算不一致")
            if card1 and card2 and max(card1, card2) / min(card1, card2) > 1.35:
                issues.append("两张系统卡价差超过35%，需人工看结构差异")
            rec.update({"系统卡1": card1, "系统卡2": card2, "核算价": audit,
                        "卡2核算差": None if card2 is None or audit is None else round(float(card2) - audit, 2),
                        "结论": "通过" if not issues else "；".join(issues)})
            rows.append(rec)

        for j, (req, category, length, width, height, wood, paints) in enumerate(FULL_CUSTOM, 31):
            template = tpl.quote_from_template(db, category, length * 100,
                                               depth_cm=width, height_cm=height,
                                               main_material=wood)
            boards = template.get("generated_boards") or []
            card1 = template.get("final_price")
            card2_raw = v2.quote_heavy(db, product_type=category, length_m=length, boards=boards,
                                      overall_width_m=width / 100, overall_height_m=height / 100)
            card2 = card2_raw.get("final_price")
            cfg = ccfg.get_config(db)
            paint_total = sum(v2._paint_surcharge(
                cfg, category=category, length_m=length, depth_cm=width,
                height_cm=height, qty=1)[0] for _ in paints)
            if card1 is not None:
                card1 = round(float(card1) + paint_total, 2)
            if card2 is not None:
                card2 = round(float(card2) + paint_total, 2)
            audit = audit_boards(db, category, length, width, height, boards, paint_total)
            issues = []
            if card1 is None or card2 is None or audit is None:
                issues.append("全定制板单无法完整计价")
            if card2 is not None and audit is not None and abs(card2 - audit) > 0.01:
                issues.append("系统卡2与逐行复算不一致")
            rows.append({"序号": j, "类型": "全定制", "产品编码": None, "产品": category,
                         "需求": req, "系统卡1": card1, "系统卡2": card2, "核算价": audit,
                         "卡2核算差": None if card2 is None or audit is None else round(card2 - audit, 2),
                         "结论": "通过" if not issues else "；".join(issues)})
    finally:
        db.close()

    out_json = Path("/tmp/custom_quote_audit_40_20260721.json")
    out_md = Path("/tmp/custom_quote_audit_40_20260721.md")
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 定制报价 40 项三价审计（2026-07-21）", "",
             "|#|类型|产品|需求|系统卡1|系统卡2|核算价|卡2核算差|结论|",
             "|---:|---|---|---|---:|---:|---:|---:|---|"]
    for r in rows:
        money = lambda x: "—" if x is None else f"¥{float(x):,.2f}"
        lines.append(f"|{r['序号']}|{r['类型']}|{r['产品']}|{r['需求']}|{money(r['系统卡1'])}|"
                     f"{money(r['系统卡2'])}|{money(r['核算价'])}|{money(r['卡2核算差'])}|{r['结论']}|")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    passed = sum(r["结论"] == "通过" for r in rows)
    print(json.dumps({"count": len(rows), "existing": len(EXISTING), "full_custom": len(FULL_CUSTOM),
                      "passed": passed, "needs_review": len(rows) - passed,
                      "json": str(out_json), "markdown": str(out_md)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
