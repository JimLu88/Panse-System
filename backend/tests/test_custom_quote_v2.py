"""定制报价 v2 金标准测试 (custom_quote_v2_service)。

锁死推算逻辑: 策略C多档插值、材质delta(wood_cost反推+现成款)、增减部位、分类器、推五金、模板聚合、引擎包装。
全部 sqlite 内存 + 合成数据(非生产数据), 与外部 Excel 无关。
"""
from decimal import Decimal as D

from app.services import custom_quote_v2_service as v2


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, autoflush=False, future=True)()
    # 既有金标准断言聚焦锚点/尺寸/材质公式，固定 safety=1 避免把独立规则混入。
    # 安全系数是否作用于普通报价由下面专门的回归测试锁定。
    from app.services import custom_quote_config_service as ccfg
    ccfg.save_config(db, {"safety_rate": 1.0})
    db.commit()
    return db


def _seed_table(db):
    """餐桌 P1: 4 档干净价(便于插值断言)。"""
    from app.models.pricing import PricingSku
    from app.models.product import Product
    db.add(Product(code="P1", name="畔色蜂蜜餐桌", category="餐厅-餐桌", main_material="樱桃木"))
    for ln, price in [("1.2", 2800), ("1.4", 2900), ("1.6", 3000), ("1.8", 3100)]:
        db.add(PricingSku(product_code="P1", sku=f"蜂蜜餐桌-{ln}米",
                          sku_code=f"P1-{ln}", size_category="中型", daily_price=D(str(price))))
    db.commit()


# ───────── 工具函数 ─────────

def test_parse_length_m():
    assert v2.parse_length_m("蜂蜜餐桌-1.4米") == 1.4
    assert v2.parse_length_m("榉木无边床-1.35米-榉木铺板") == 1.35
    assert v2.parse_length_m("无尺寸") is None


def test_interp_exact_interp_extrap():
    pts = [(1.2, 2800), (1.4, 2900), (1.6, 3000), (1.8, 3100)]
    assert v2.interp(pts, 1.4) == (2900, "exact")
    y, m = v2.interp(pts, 1.5)
    assert y == 2950 and m == "interp"      # 1.4↔1.6 中点
    y, m = v2.interp(pts, 2.0)
    assert y == 3200 and m == "extrap"      # 末段斜率外推
    y, m = v2.interp(pts, 1.0)
    assert y == 2700 and m == "extrap"      # 首段反向外推
    assert v2.interp([(1.5, 999)], 1.7) == (999, "single")
    assert v2.interp([], 1.5) == (None, "no-data")


# ───────── 普通定制: 尺寸 delta (策略C) ─────────

def test_quote_light_size_interpolation():
    db = _db()
    _seed_table(db)
    r = v2.quote_light(db, base_product_code="P1", target_length_m=1.5)
    assert r["anchor"] == 2950.0
    assert r["final_price"] == 2950.0          # 无材质/增减 → 等于锚点
    assert "策略C" in r["anchor_method"]


def test_quote_light_safety_rate_changes_total_and_is_explained():
    """安全系数必须作用于普通报价整体，并逐笔写入报价明细。"""
    from app.services import custom_quote_config_service as ccfg

    db = _db()
    _seed_table(db)
    ccfg.save_config(db, {"safety_rate": 1.10})
    db.commit()
    r = v2.quote_light(db, base_product_code="P1", target_length_m=1.5)
    assert r["subtotal_before_safety"] == 2950.0
    assert r["safety_delta"] == 295.0
    assert r["final_price"] == 3245.0
    assert r["pricing_parameters"]["safety_rate"] == 1.10
    assert any(x["label"] == "安全系数 ×1.1" and x["amount"] == 295.0 for x in r["breakdown"])


def test_quote_light_unknown_product():
    db = _db()
    r = v2.quote_light(db, base_product_code="NOPE", target_length_m=1.5)
    assert r["final_price"] is None and "无此产品" in r["error"]


def _seed_slab_table(db):
    """岩板餐桌 S1: 带 size_info(长/宽/高) 的多档 —— 触发「面积一致定价」路径。
    价随长度增(2630→3100), 宽随长度增(75→85 后 plateau), 复刻生产 榉木岩板餐桌 形态。"""
    from app.models.pricing import PricingSku
    from app.models.product import Product
    db.add(Product(code="S1", name="榉木岩板餐桌", category="餐厅-餐桌", main_material="榉木"))
    for ln, depth_mm, price in [("1.4", 750, 2630), ("1.6", 800, 2710),
                                ("1.8", 850, 2800), ("2.0", 850, 3100)]:
        db.add(PricingSku(product_code="S1", sku=f"岩板餐桌-{ln}米", sku_code=f"S1-{ln}",
                          size_category="中型", big_promo=D(str(price)), daily_price=D(str(price)),
                          size_info=f"长度：{float(ln) * 1000:.0f}mm；深度：{depth_mm}mm；高度：750mm"))
    db.commit()


def test_quote_light_area_pricing_monotone():
    """面积一致定价(修「越小越贵」): 固定宽85, 更短(1.45m)绝不比更长(1.5m)贵; 宽=标准≈老长度锚点; 加宽更贵。"""
    from app.models.pricing import PricingSku
    db = _db()
    _seed_slab_table(db)
    r145 = v2.quote_light(db, base_product_code="S1", target_length_m=1.45,
                          target_width_cm=85, price_tier="big")
    r150 = v2.quote_light(db, base_product_code="S1", target_length_m=1.5,
                          target_width_cm=85, price_tier="big")
    assert r145["final_price"] <= r150["final_price"]        # ★ 修复核心: 更短不再更贵
    assert "面积定价" in r145["anchor_method"]
    # 宽=标准(1.5m 标准宽=77.5) → 面积锚点 ≈ 老「按长度插值」(2670 附近)
    r_std = v2.quote_light(db, base_product_code="S1", target_length_m=1.5, price_tier="big")
    assert 2650 <= r_std["final_price"] <= 2690
    # 同长度加宽(90>85) → 更贵(宽单调)
    r_wide = v2.quote_light(db, base_product_code="S1", target_length_m=1.5,
                            target_width_cm=90, price_tier="big")
    assert r_wide["final_price"] >= r150["final_price"]
    # 全长扫一遍(宽固定85): 应单调不降
    prices = [v2.quote_light(db, base_product_code="S1", target_length_m=L,
                             target_width_cm=85, price_tier="big")["final_price"]
              for L in (1.3, 1.4, 1.5, 1.6, 1.8)]
    assert prices == sorted(prices)                          # 更长必不更便宜


# ───────── 普通定制: 材质 delta ─────────

def _seed_bed(db, with_walnut_sibling=False):
    from app.models.material import Material
    from app.models.pricing import PricingSku
    from app.models.product import Product
    db.add_all([
        Material(code="MW-0001", name="榉木-2.2cm厚度", price=D("240")),
        Material(code="MW-0002", name="黑胡桃木-2.2cm厚度", price=D("550")),
        Material(code="AC-0001", name="金属把手", price=D("30")),
    ])
    db.add(Product(code="B1", name="畔色榉木无边床", category="卧室-床", main_material="榉木"))
    db.add(PricingSku(product_code="B1", sku="榉木无边床-1.5米", sku_code="B1-1.5",
                      size_category="中型", daily_price=D("2000"), wood_cost=D("600"),
                      factory_cost=D("900"), accounting_cost=D("1300"), big_promo=D("1625")))
    db.add(PricingSku(product_code="B1", sku="榉木无边床-1.8米", sku_code="B1-1.8",
                      size_category="大型", daily_price=D("2300"), wood_cost=D("720"),
                      factory_cost=D("1050"), accounting_cost=D("1500"), big_promo=D("1875")))
    if with_walnut_sibling:
        db.add(Product(code="B2", name="畔色黑胡桃无边床", category="卧室-床", main_material="黑胡桃"))
        db.add(PricingSku(product_code="B2", sku="黑胡桃无边床-1.5米", sku_code="B2-1.5",
                          size_category="中型", daily_price=D("2800")))
    db.commit()


def test_material_delta_via_woodcost():
    """无现成款 → wood_cost÷原料价 反推面积 × 料价差 × 1.25。"""
    db = _db()
    _seed_bed(db)
    r = v2.quote_light(db, base_product_code="B1", target_length_m=1.5, target_material="黑胡桃")
    # 面积=600/240=2.5㎡; delta=(550-240)*2.5*1.25 = 968.75
    assert r["material_delta"] == 968.75
    assert r["final_price"] == 2000.0 + 968.75


def test_material_delta_sibling_is_reference_only():
    """有现成黑胡桃款 → 仍只减材质差额(反推), 现成款仅作"可切换"参考提示, 绝不换产品。
    用户拍板 2026-06-20: 换料只减两者木材差额, 不能切到另一个产品减掉整份价。"""
    db = _db()
    _seed_bed(db, with_walnut_sibling=True)
    r = v2.quote_light(db, base_product_code="B1", target_length_m=1.5, target_material="黑胡桃")
    assert r["material_delta"] == 968.75          # 只减材质差额(同 via_woodcost), 不是切现成款的800
    assert r["final_price"] == 2000.0 + 968.75
    assert any("参考现成同款" in (line["note"] or "") for line in r["breakdown"])


def test_box_part_pricing_and_autoheight():
    """顶柜木作盒子: 6块板面积×木单价; 总高>标准高 → 顶柜高=总高−标准高自动算(用户拍板 2026-06-20)。"""
    from app.services.custom_quote_v2_service import _is_box_part, _box_material_cost, _autofill_box_parts
    assert _is_box_part("顶柜") and _is_box_part("吊柜") and not _is_box_part("背板")
    # 盒 150×50×20: 面积=(3×150×50+2×50×20+150×20)/10000=2.75㎡; 成本=2.75×300=825
    cost, area, _f = _box_material_cost(150, 50, 20, 1, 300)
    assert area == 2.75 and cost == 825.0
    # 自动填: 总高230−标准210=顶柜高20; 长150(柜宽1.5m); 宽50(柜深); 木种榉木
    out = _autofill_box_parts([{"material": "顶柜"}], length_m=1.5, depth_cm=None, std_w=50,
                              total_h_cm=230, std_h=210, box_wood="榉木")
    assert out[0]["height_cm"] == 20.0 and out[0]["length_cm"] == 150.0
    assert out[0]["width_cm"] == 50.0 and out[0]["material_real"] == "榉木"
    # 总高≤标准高 → 不自动加高度(留空手填)
    out2 = _autofill_box_parts([{"material": "顶柜"}], length_m=1.5, depth_cm=None, std_w=50,
                               total_h_cm=200, std_h=210, box_wood="榉木")
    assert not out2[0].get("height_cm")


def test_parse_height_and_auto_top_cabinet():
    """高度提取(优先长度不抢高度) + 顶柜自动加部位(用户拍板 2026-06-20: 一句话自动提取高度+加顶柜)。"""
    from app.services.custom_quote_v2_service import parse_length_m, parse_height_cm, _auto_top_cabinet
    txt = "定制榉木洞石餐边柜；高度2.3米，长度1.5米，高出来的部分加到顶柜"
    assert parse_length_m(txt) == 1.5          # 优先"长度1.5米", 不抢"高度2.3米"
    assert parse_height_cm(txt) == 230.0       # 高度2.3米→230cm
    assert parse_height_cm("总高2300mm") == 230.0 and parse_height_cm("高度230cm") == 230.0
    assert any(p["material"] == "顶柜" for p in _auto_top_cabinet(txt, []))
    assert len(_auto_top_cabinet(txt, [{"material": "顶柜", "qty": 1}])) == 1   # 不重复加
    assert _auto_top_cabinet("加个抽屉", []) == []                              # 没提顶柜不加


# ───────── 盈亏平衡工厂价 (净不亏红线) ─────────

def test_break_even_light():
    """普通定制盈亏平衡: 售价 − (accounting−factory) = 红线; 安全垫 = 红线 − 预测 = 售价 − accounting。"""
    db = _db()
    _seed_bed(db)
    r = v2.quote_light(db, base_product_code="B1", target_length_m=1.5)
    assert r["factory_predicted"] == 900.0                 # 定价表 factory_cost@1.5
    assert r["break_even_factory"] == 1600.0               # 2000 − (1300−900)
    assert r["break_even_buffer"] == 700.0                 # 1600 − 900 = 售价2000 − accounting1300
    assert r["product_margin"] == 0.20                     # 大促毛利率 = 平均(1−1300/1625, 1−1500/1875)
    assert r["break_even_sell"] == 1600.0                  # 保本价 = 售价2000 × (1−0.20)(与红线同值纯巧合)


def test_product_margin():
    """本款大促毛利率(实时): 平均(1−会计成本/大促价); 缺则回落 gross_margin_rate; 再缺→None。"""
    from app.models.pricing import PricingSku
    db = _db()
    db.add(PricingSku(product_code="X1", sku="x-1.0米", sku_code="X1-1.0",
                      big_promo=D("1000"), accounting_cost=D("800")))   # 1−800/1000=0.20
    db.add(PricingSku(product_code="X1", sku="x-1.2米", sku_code="X1-1.2",
                      big_promo=D("1000"), accounting_cost=D("600")))   # 1−600/1000=0.40
    db.commit()
    xs = db.query(PricingSku).filter(PricingSku.product_code == "X1").all()
    assert v2.product_margin(xs) == 0.30                    # 平均(0.20, 0.40)
    # 无 big_promo → 回落存档 gross_margin_rate
    db.add(PricingSku(product_code="Y1", sku="y-1.0米", sku_code="Y1-1.0",
                      gross_margin_rate=D("0.18")))
    db.commit()
    ys = db.query(PricingSku).filter(PricingSku.product_code == "Y1").all()
    assert v2.product_margin(ys) == 0.18
    # 大促价/会计/存档 全无 → None
    db.add(PricingSku(product_code="Z1", sku="z-1.0米", sku_code="Z1-1.0", daily_price=D("500")))
    db.commit()
    zs = db.query(PricingSku).filter(PricingSku.product_code == "Z1").all()
    assert v2.product_margin(zs) is None


def test_break_even_heavy():
    """特殊定制盈亏平衡: 预测=factory_quote_compare; 红线<售价; 安全垫=红线−预测。"""
    db = _db()
    boards = [
        {"part": "板单合计", "material": "黑胡桃木-2.2cm", "length_cm": 1008.8, "width_cm": 100, "qty": 1},
        {"part": "抽屉面板", "material": "樱桃木-2.2cm", "length_cm": 40, "width_cm": 20, "qty": 2},
    ]
    r = v2.quote_heavy(db, product_type="餐边柜", length_m=2.1, boards=boards,
                       overall_width_m=2.1, overall_height_m=1.95)
    assert r["factory_predicted"] == r["factory_quote_compare"]
    assert r["break_even_factory"] < r["final_price"]      # 红线低于售价
    assert r["break_even_buffer"] == round(r["break_even_factory"] - r["factory_predicted"], 2)
    assert r["break_even_sell"] == round(r["final_price"] - r["break_even_buffer"], 2)  # B2 保本价


# ───────── 普通定制: 增减部位 delta ─────────

def test_add_remove_parts():
    """配件(按件)逐部位 cascade: 材料 → ×1.3人工 ×1.25厂利 ÷0.85畔色; 删除再×0.85保守。"""
    db = _db()
    _seed_bed(db)
    r = v2.quote_light(db, base_product_code="B1", target_length_m=1.5,
                       add_parts=[{"material": "金属把手", "qty": 2}])
    assert r["addremove_delta"] == 114.71         # 60×1.3×1.25÷0.85
    assert r["final_price"] == 2114.71
    assert r["parts_detail"][0]["change"] == "add" and r["parts_detail"][0]["material_cost"] == 60.0
    r2 = v2.quote_light(db, base_product_code="B1", target_length_m=1.5,
                        remove_parts=[{"material": "金属把手", "qty": 1}])
    assert r2["addremove_delta"] == -25.5          # 决策①: 删件只扣材料 30×0.85(不退人工/利润)
    # 每次算价都带竞品/基准对比块
    assert "comparison" in r and r["comparison"]["baseline"] is not None


def test_style_delta_geometry_wood_part():
    """木作部位走模板几何: 「背板」→ 餐边柜模板 1.5m×0.8m=1.2㎡ × 多层板220元/㎡ 的逐部位算价。"""
    from app.models.material import Material
    db = _db()
    db.add(Material(code="QM-0001", name="实木多层板1.8cm", price=D("220"), unit="平方米"))
    db.commit()
    cfg = {"style_labor_ratio": 0.30, "factory_profit_rate": 0.25,
           "panse_profit_rate": 0.15, "style_remove_credit": 0.85}
    total, lines, detail = v2.style_delta(
        db, category="餐边柜", length_m=1.5,
        add_parts=[{"material": "背板"}], remove_parts=[], cfg=cfg)
    d = detail[0]
    assert d["material"] == "实木多层板1.8cm"      # 模板把「背板」映射到多层板
    assert d["area_m2"] == 1.2                       # 1.5m × 0.8m(餐边柜H=80cm)
    assert d["material_cost"] == 264.0               # 1.2㎡ × 220
    assert total == round(264 * 1.3 * 1.25 / 0.85, 2)


def test_modify_parts():
    """改部位(换料): 材料差; 净增(新料更贵)×(1+工厂利润), 净减就材料差不加利润。"""
    from app.models.material import Material
    db = _db()
    db.add_all([
        Material(code="QM-0001", name="实木多层板1.8cm", price=D("220"), unit="平方米"),
        Material(code="QM-0002", name="亚克力洞洞板", price=D("400"), unit="平方米"),
        Material(code="QM-0003", name="廉价背板", price=D("100"), unit="平方米"),
    ])
    db.commit()
    cfg = {"style_labor_ratio": 0.30, "factory_profit_rate": 0.25,
           "panse_profit_rate": 0.15, "style_remove_credit": 0.85}
    # 背板模板=多层板 1.2㎡×220=264。改贵料(亚克力 1.2㎡×400=480): 差+216, 净增×1.25=270
    total, _l, detail = v2.style_delta(
        db, category="餐边柜", length_m=1.5, add_parts=[], remove_parts=[],
        modify_parts=[{"material": "背板", "material_real": "亚克力洞洞板"}], cfg=cfg)
    assert detail[0]["change"] == "modify" and detail[0]["from_material"] == "实木多层板1.8cm"
    assert total == round((480 - 264) * 1.25, 2)          # 净增=216×1.25=270
    # 改便宜料(1.2㎡×100=120): 差-144, 净减就材料差不加利润
    total2, _l2, _d2 = v2.style_delta(
        db, category="餐边柜", length_m=1.5, add_parts=[], remove_parts=[],
        modify_parts=[{"material": "背板", "material_real": "廉价背板"}], cfg=cfg)
    assert total2 == round(120 - 264, 2)                  # 净减=-144(不加工厂利润)


def test_compare_prices_baseline_and_competitor():
    """竞品对比: 空竞品表→仅本店标准款基准; 灌一条竞品→出现并算高低。"""
    db = _db()
    c = v2.compare_prices(db, category="餐边柜", wood="樱桃木", size_m=1.5,
                          our_price=3000, baseline_price=2800)
    assert c["competitor_available"] is False
    assert c["baseline"]["price"] == 2800.0
    assert c["baseline"]["diff_pct"] == 7.1 and c["baseline"]["is_lower"] is False
    from app.models.competitor import CompetitorPrice
    db.add(CompetitorPrice(store="别家", category="餐边柜", product="樱桃木餐边柜",
                           wood="樱桃木", sku_name="餐边柜-1.5米", daily_price=D("3500")))
    db.commit()
    c2 = v2.compare_prices(db, category="餐边柜", wood="樱桃木", size_m=1.5,
                           our_price=3000, baseline_price=2800)
    assert c2["competitor_available"] is True
    comp = c2["competitors"][0]
    assert comp["price"] == 3500.0 and comp["is_lower"] is True


# ───────── 分类器 ─────────

def test_classify_hit_is_light():
    db = _db()
    _seed_table(db)
    r = v2.classify(db, text="蜂蜜餐桌")
    assert r["customization_type"] == "普通定制"
    assert r["base_product_code"] == "P1"


def test_classify_miss_is_heavy():
    db = _db()
    _seed_table(db)
    r = v2.classify(db, text="全新异形旋转吧台")
    assert r["customization_type"] == "特殊定制"
    assert r["base_product_code"] is None


# ───────── A6 尺寸合理性校验 ─────────

def test_size_plausible():
    from app.services.custom_quote_config_service import DEFAULT_CONFIG as cfg, size_plausible
    assert size_plausible(cfg, "卧室-床头柜", 1.5) is False   # 1.5m 床头柜→不合理(上限0.5×1.6=0.8)
    assert size_plausible(cfg, "卧室-床头柜", 0.7) is True
    assert size_plausible(cfg, "餐厅-餐桌", 1.5) is True       # 餐桌上限 2.0×1.6
    assert size_plausible(cfg, "卧室-床头柜", None) is True    # 无长度→不拦
    assert size_plausible(cfg, "衣帽架", 5.0) is True          # 大阈0→不判


def test_apply_size_sanity_demotes_implausible():
    from app.models.pricing import PricingSku
    from app.models.product import Product
    from app.services.custom_quote_config_service import DEFAULT_CONFIG
    db = _db()
    db.add(Product(code="N1", name="畔色床头柜", category="卧室-床头柜", main_material="樱桃木"))
    db.add(PricingSku(product_code="N1", sku="床头柜-0.5米", sku_code="N1-0.5",
                      size_category="小型", daily_price=D("800")))
    db.commit()
    res = {"customization_type": "普通定制", "base_product_code": "N1",
           "base_product_name": "畔色床头柜", "target_length_m": 1.5, "confidence": 0.9}
    out = v2.apply_size_sanity(db, DEFAULT_CONFIG, res)
    assert out["base_product_code"] is None and out["size_warning"] is True
    assert out["confidence"] <= 0.3
    out2 = v2.apply_size_sanity(db, DEFAULT_CONFIG, {**res, "target_length_m": 0.5})
    assert out2["base_product_code"] == "N1" and not out2.get("size_warning")


# ───────── 自动推五金 ─────────

def test_infer_hardware():
    boards = [{"part": "抽屉面板", "qty": 2}, {"part": "层板", "qty": 3}, {"part": "柜门", "qty": 2}]
    hw = {h["material"]: h for h in v2.infer_hardware(boards)}
    assert hw["抽屉轨道"]["qty"] == 2 and hw["抽屉轨道"]["is_drawer_rail"]
    assert hw["层板托"]["qty"] == 12             # 3层板 × 4
    assert hw["铰链"]["qty"] == 4                # 2门 × 2
    assert hw["反弹器"]["qty"] == 4              # 抽2+门2, 无把手


def test_infer_hardware_handle_suppresses_rebounder():
    boards = [{"part": "抽屉面板", "qty": 1}, {"part": "金属把手", "qty": 1}]
    mats = {h["material"] for h in v2.infer_hardware(boards)}
    assert "反弹器" not in mats                  # 有把手 → 不加反弹器


# ───────── 部位模板聚合 ─────────

def test_suggest_part_template():
    from app.models.bom import BomLine
    from app.models.product import Product
    db = _db()
    db.add_all([
        Product(code="C1", name="床A", category="卧室-床"),
        Product(code="C2", name="床B", category="卧室-床"),
    ])
    for pc in ("C1", "C2"):
        db.add_all([
            BomLine(product_code=pc, material_code="MW-0001", material_name="床头板", size_type="组合"),
            BomLine(product_code=pc, material_code="MW-0001", material_name="床腿", size_type="组合"),
        ])
    db.commit()
    tmpl = {t["part"]: t for t in v2.suggest_part_template(db, "卧室-床")}
    assert "床头板" in tmpl and tmpl["床头板"]["freq"] == 2


# ───────── 特殊定制: 引擎包装 + 自动五金 ─────────

def test_quote_heavy_engine_and_hardware():
    db = _db()
    boards = [
        {"part": "板单合计", "material": "黑胡桃木-2.2cm", "length_cm": 1008.8, "width_cm": 100, "qty": 1},
        {"part": "抽屉面板", "material": "樱桃木-2.2cm", "length_cm": 40, "width_cm": 20, "qty": 2},
    ]
    r = v2.quote_heavy(db, product_type="餐边柜", length_m=2.1, boards=boards,
                       overall_width_m=2.1, overall_height_m=1.95)
    assert r["labor_fee"] == 1480.0                          # 餐边柜大型(配置)
    assert r["final_price"] > 0
    mats = {h["material"] for h in r["inferred_hardware"]}
    assert "抽屉轨道" in mats                                # 有抽屉面板 → 自动加轨道


# ───────── 缓存 + AI 增强分类(收尾批) ─────────

class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeProvider:
    """假 AI 提供方: 固定返回一段文本(模拟模型 JSON 输出)。"""
    def __init__(self, text):
        self._t = text

    def chat(self, system, user, max_tokens=600):
        return _FakeResp(self._t)

    def chat_with_images(self, system, user, images, max_tokens=600):
        return _FakeResp(self._t)


def test_cache_hits_once():
    v2.cache_clear()
    n = {"c": 0}

    def build():
        n["c"] += 1
        return n["c"]

    a = v2._cached("k", build)
    b = v2._cached("k", build)
    assert a == b == 1 and n["c"] == 1          # 第二次走缓存, builder 只跑一次
    v2.cache_clear()


def test_classify_deterministic_parses_dims_material():
    db = _db()
    _seed_table(db)
    v2.cache_clear()
    r = v2.classify(db, text="蜂蜜餐桌 改 1.5米 黑胡桃")
    assert r["customization_type"] == "普通定制"
    assert r["target_length_m"] == 1.5
    assert r["target_material"] == "黑胡桃"
    assert r["ai_used"] is False


def test_classify_ai_parses_structured():
    db = _db()
    _seed_table(db)
    v2.cache_clear()
    js = ('{"customization_type":"普通定制","matched_product_name":"畔色蜂蜜餐桌",'
          '"target_length_m":1.5,"target_material":"黑胡桃","add_parts":[],'
          '"remove_parts":[],"confidence":0.9,"reasoning":"改尺寸+材质"}')
    r = v2.classify_ai(db, text="蜂蜜餐桌改1.5米黑胡桃", images=None,
                       provider=_FakeProvider(js), model="x")
    assert r is not None
    assert r["customization_type"] == "普通定制"
    assert r["base_product_code"] == "P1"        # 产品名→code 映射
    assert r["target_length_m"] == 1.5 and r["target_material"] == "黑胡桃"
    assert r["ai_used"] is True
    v2.cache_clear()


def test_classify_ai_none_on_garbage():
    db = _db()
    _seed_table(db)
    v2.cache_clear()
    assert v2.classify_ai(db, text="x", images=None,
                          provider=_FakeProvider("不是JSON"), model="") is None
    assert v2.classify_ai(db, text="x", images=None, provider=None, model="") is None
    v2.cache_clear()


def test_dims_triplet_remove_parts_category_guess(db_session):
    """2026-07-12 报价空白复盘: 「1.5*0.6*0.95」无单位三元组/「不要底板」/品类猜测 确定性解析。"""
    from app.services import custom_quote_v2_service as v2
    assert v2.parse_dims_triplet("纯定制品，1.5*0.6*0.95") == (1.5, 60.0, 95.0)
    assert v2.parse_dims_triplet("1500×600×950") == (1.5, 60.0, 95.0)
    assert v2.parse_dims_triplet("没有尺寸") == (None, None, None)
    assert v2.parse_remove_parts("不要底板，去掉背板") == [
        {"material": "底板", "qty": 1}, {"material": "背板", "qty": 1}]
    assert v2.guess_category("做个吧台中岛") == "岛台"
    assert v2.guess_category("纯定制品") is None
    v2.cache_clear()
    r = v2.classify(db_session, text="纯定制品，1.5*0.6*0.95，不要底板，纯樱桃木，大促价格")
    assert r["customization_type"] == "特殊定制"
    assert r["target_length_m"] == 1.5 and r["target_width_cm"] == 60.0 and r["target_height_cm"] == 95.0
    assert r["target_material"] == "樱桃木"
    assert {"material": "底板", "qty": 1} in r["remove_parts"]
    assert "尺寸" in r["reasoning"] or "长1.5米" in r["reasoning"]
    v2.cache_clear()


# ───────── 顶柜自动拆分 (2026-07-18: 餐边柜下柜固定, 变动的是顶柜) ─────────

def test_parse_lower_cabinet_height():
    assert v2.parse_lower_cabinet_height_cm("下柜高度92cm") == 92.0
    assert v2.parse_lower_cabinet_height_cm("下柜高度0.92米") == 92.0
    assert v2.parse_lower_cabinet_height_cm("底柜高920mm") == 92.0
    assert v2.parse_lower_cabinet_height_cm("总高194cm 没有下柜标注") is None


def test_detect_top_cabinet():
    h, hint = v2.detect_top_cabinet("洞石餐边柜 全景柜下柜", 194, lower_h_cm=92, category="餐边柜")
    assert h == 102.0 and "顶柜 102cm" in hint and "下柜" in hint
    # 无下柜标注时退回标准柜身高
    h2, hint2 = v2.detect_top_cabinet("餐边柜", 194, std_h_cm=90, category="餐厅-餐边柜")
    assert h2 == 104.0 and "标准柜身" in hint2
    # 非柜类 / 差额太小 → 不拆
    assert v2.detect_top_cabinet("樱桃木餐桌", 194, lower_h_cm=92, category="餐桌") == (None, "")
    assert v2.detect_top_cabinet("餐边柜", 95, lower_h_cm=92, category="餐边柜") == (None, "")


def test_ensure_top_cabinet_dedup():
    assert v2._ensure_top_cabinet([], 102) == [{"material": "顶柜", "qty": 1, "height_cm": 102}]
    p = v2._ensure_top_cabinet([{"material": "顶柜", "qty": 1}], 102)
    assert len(p) == 1 and p[0]["height_cm"] == 102


def test_classify_autosplits_top_cabinet():
    """用户实测单: 洞石餐边柜 60×27.5×194 下柜92 → 顶柜102 + 三元组优先长0.6(非AI幻觉2.75)。"""
    db = _db()
    r = v2.classify(db, text="洞石餐边柜, 定制60×27.5×194cm, 下柜高度92cm, 全景柜下柜")
    assert r["target_length_m"] == 0.6            # 三元组优先, 不是把 27.5 读成 2.75m
    assert r["target_height_cm"] == 194.0         # 总高取三元组, 不被"下柜高度92"顶替
    assert r["top_cabinet_height_cm"] == 102.0
    top = next((p for p in r["add_parts"] if "顶柜" in (p.get("material") or "")), None)
    assert top is not None and top.get("height_cm") == 102.0
    assert r.get("top_cabinet_hint")
    v2.cache_clear()


def test_quote_light_below_range_proportional():
    """远小于最小档的定制按正比估, 不被平坦价曲线线性外推顶高(治 0.6m 报 14k 实际 ~5k)。"""
    db = _db()
    _seed_table(db)   # P1 1.2~1.8m / daily 2800~3100
    q = v2.quote_light(db, base_product_code="P1", target_length_m=0.6, price_tier="daily")
    # 最小档 1.2m/¥2800 → 0.6m 正比 = 2800×0.6/1.2 = 1400 (线性外推会给 2500 偏高)
    assert q["anchor"] == 1400.0
    assert "正比估" in q["anchor_method"]
    v2.cache_clear()


# ─────────────── 2026-07-21 定制报价压力测试修复 ───────────────

def test_quote_light_filters_placeholder_and_below_cost_skus():
    """占位 SKU 和明显低于成本的脏价不能成为整件家具报价锚点。"""
    from app.models.pricing import PricingSku
    from app.models.product import Product

    db = _db()
    db.add(Product(code="Q1", name="测试餐边柜", category="餐厅-餐边柜"))
    db.add_all([
        PricingSku(product_code="Q1", sku="正常款1.5米", sku_code="Q101",
                   size_info="长度：1500mm；深度：400mm；高度：800mm",
                   daily_price=D("3000"), accounting_cost=D("1800")),
        PricingSku(product_code="Q1", sku="尺寸定制专拍", sku_code="Q199",
                   daily_price=D("1"), accounting_cost=D("1"), is_custom_placeholder=True),
        PricingSku(product_code="Q1", sku="异常低价款", sku_code="Q102",
                   daily_price=D("750"), accounting_cost=D("1800")),
    ])
    db.commit()
    q = v2.quote_light(db, base_product_code="Q1", target_length_m=1.5)
    assert q["anchor"] == 3000.0
    assert q["final_price"] == 3000.0


def test_quote_light_stops_when_only_placeholder_skus_exist():
    from app.models.pricing import PricingSku
    from app.models.product import Product

    db = _db()
    db.add(Product(code="Q2", name="测试专拍产品", category="餐厅-餐桌"))
    db.add(PricingSku(product_code="Q2", sku="定制差价专拍", sku_code="Q299",
                      daily_price=D("1"), is_custom_placeholder=True))
    db.commit()
    q = v2.quote_light(db, base_product_code="Q2", target_length_m=1.5)
    assert q["final_price"] is None
    assert "没有可用的真实SKU锚点" in q["error"]


def test_paint_parser_requires_an_action_not_just_a_color():
    assert len(v2.parse_paint_parts("蜂蜜餐桌改成白色油漆上色")) == 1
    assert v2.parse_paint_parts("白色岩板餐桌改成1.6米") == []


def test_paint_standard_table_adds_250_to_both_cards():
    from app.services import custom_quote_config_service as ccfg

    db = _db()
    _seed_table(db)
    ccfg.save_config(db, {"safety_rate": 1.10})
    db.commit()
    plain = v2.quote_both(
        db, base_product_code="P1", target_length_m=1.5,
        target_width_cm=80, target_height_cm=75, category="餐桌")
    painted = v2.quote_both(
        db, base_product_code="P1", target_length_m=1.5,
        target_width_cm=80, target_height_cm=75, category="餐桌",
        add_parts=[{"material": "白色油漆上色", "qty": 1, "is_paint": True}])
    assert painted["spec"]["final_price"] - plain["spec"]["final_price"] == 250.0
    assert painted["spec"]["paint_surcharge"] == 250.0
    assert round(painted["custom"]["final_price"] - plain["custom"]["final_price"], 2) == 250.0
    assert painted["custom"]["paint_surcharge"] == 250.0


def test_quote_both_template_fallback_returns_editable_boards():
    db = _db()
    _seed_table(db)  # 产品有价格、无 BOM，必须走通用品类模板回退
    both = v2.quote_both(
        db, base_product_code="P1", target_length_m=1.5,
        target_width_cm=80, target_height_cm=75, category="餐桌")
    assert both["custom"]["from_bom"] is False
    assert both["custom_boards"]
    audit = v2.quote_heavy(
        db, product_type="餐桌", length_m=1.5, boards=both["custom_boards"],
        overall_width_m=0.8, overall_height_m=0.75)
    assert audit["final_price"] == both["custom"]["final_price"]


def test_quote_both_surfaces_large_method_gap():
    db = _db()
    _seed_table(db)
    both = v2.quote_both(
        db, base_product_code="P1", target_length_m=1.5,
        target_width_cm=80, target_height_cm=75, category="餐桌")
    # 测试数据的市场锚点约2950、模板板单约1965，必须把差异直接告诉客服。
    assert both["custom"]["price_gap_rate"] > 0.35
    assert "需人工核对款式复杂度后选用" in both["custom"]["price_gap_warning"]


def test_paint_standard_sideboard_adds_350():
    from app.models.pricing import PricingSku
    from app.models.product import Product

    db = _db()
    db.add(Product(code="Q3", name="标准餐边柜", category="餐厅-餐边柜"))
    db.add(PricingSku(product_code="Q3", sku="1.5米标准款", sku_code="Q301",
                      size_info="长度：1500mm；深度：400mm；高度：800mm",
                      daily_price=D("5000"), accounting_cost=D("3000")))
    db.commit()
    q = v2.quote_light(
        db, base_product_code="Q3", target_length_m=1.5,
        target_width_cm=40, target_height_cm=80,
        add_parts=[{"material": "胡桃色油漆上色", "qty": 1, "is_paint": True}])
    assert q["addremove_delta"] == 350.0
    assert q["final_price"] == 5350.0


def test_bom_board_defaults_use_small_product_real_dimensions():
    """没有手填尺寸时，床头柜不能再套用旧的 210cm 高柜默认值。"""
    from app.models.bom import BomLine
    from app.models.material import Material
    from app.models.pricing import PricingSku
    from app.models.product import Product

    db = _db()
    db.add(Material(code="AC-Q1", name="测试拉手", unit="个", price=D("20")))
    db.add(Product(code="Q4", name="小床头柜", category="卧室-床头柜"))
    db.add(PricingSku(product_code="Q4", sku="45厘米款", sku_code="Q401",
                      size_info="长度：450mm；深度：400mm；高度：450mm",
                      daily_price=D("1500"), accounting_cost=D("900")))
    db.add(BomLine(product_code="Q4", sku_code="Q401", material_code="AC-Q1",
                   material_name="测试拉手", qty_per_product=D("1"), size_type="个数"))
    db.commit()
    boards = v2.boards_from_product_bom(db, product_code="Q4", category="床头柜")
    assert boards
    assert max(float(b.get("length_cm") or 0) for b in boards) <= 50.0


def _seed_custom_sideboard(db):
    from app.models.bom import BomLine
    from app.models.material import Material
    from app.models.pricing import PricingSku
    from app.models.product import Product

    db.add_all([
        Material(code="MW-C1", name="樱桃木-2.2cm", unit="平方米", price=D("300")),
        Material(code="MW-C2", name="榉木-2.2cm", unit="平方米", price=D("280")),
        Material(code="AC-C1", name="5mm灰色玻璃", unit="平方米", price=D("250")),
        Material(code="AC-C2", name="超白玻璃", unit="平方米", price=D("300")),
    ])
    db.add(Product(code="C1", name="樱桃木测试餐边柜", category="餐厅-餐边柜", main_material="樱桃木"))
    for code, sku in [("C101", "1.5米灰玻款"), ("C102", "1.8米超白款")]:
        length = "1500" if code == "C101" else "1800"
        db.add(PricingSku(
            product_code="C1", sku=sku, sku_code=code,
            size_info=f"长度：{length}mm；深度：480mm；高度：1980mm",
            daily_price=D("8000"), wood_cost=D("3000"), accounting_cost=D("5000"),
        ))
    db.add(BomLine(product_code="C1", sku="1.5米灰玻款", sku_code="C101",
                   material_code="AC-C1", material_name="5mm灰色玻璃",
                   qty_per_product=D("2"), size_type="平面", remark="玻璃门"))
    db.add(BomLine(product_code="C1", sku="1.8米超白款", sku_code="C102",
                   material_code="AC-C2", material_name="超白玻璃",
                   qty_per_product=D("2"), size_type="平面", remark="玻璃门"))
    db.commit()


def test_bom_picker_locks_exact_sku_and_structure_enters_card2():
    db = _db()
    _seed_custom_sideboard(db)
    picked = v2.boards_from_product_bom(
        db, product_code="C1", category="餐边柜", base_sku_code="C102",
        target_length_m=1.8, target_width_cm=48, target_height_cm=198)
    assert any(b["material"] == "超白玻璃" for b in picked)
    assert not any(b["material"] == "5mm灰色玻璃" for b in picked)

    both = v2.quote_both(
        db, base_product_code="C1", base_sku_code="C101", category="餐边柜",
        target_length_m=1.5, target_width_cm=48, target_height_cm=198,
        target_material="榉木", add_parts=[{"material": "抽屉面板", "qty": 3}],
        modify_parts=[{"name": "玻璃门", "material": "5mm灰色玻璃",
                       "material_real": "榉木-2.2cm", "qty": 2}],
        description="1.5米餐边柜改榉木，玻璃门改实木门，下柜三抽",
    )
    boards = both["custom_boards"]
    assert both["custom"]["final_price"] is not None
    assert any("抽屉" in b["part"] for b in boards)
    assert any(b["material"] == "榉木-2.2cm" for b in boards)
    assert not any("玻璃" in b["material"] for b in boards)


def test_unknown_requested_material_stops_both_quote_cards():
    db = _db()
    _seed_custom_sideboard(db)
    both = v2.quote_both(
        db, base_product_code="C1", base_sku_code="C101", category="餐边柜",
        target_length_m=1.5, target_width_cm=48, target_height_cm=198,
        add_parts=[{"material": "感应灯带", "qty": 1}],
        description="增加一条感应灯带",
    )
    assert both["spec"]["final_price"] is None
    assert "感应灯带" in both["spec"]["missing_materials"]
    assert both["custom"]["final_price"] is None
    assert "感应灯带" in both["custom"]["missing_materials"]


def test_unknown_explicit_replacement_cannot_borrow_old_material_price():
    db = _db()
    _seed_custom_sideboard(db)
    both = v2.quote_both(
        db, base_product_code="C1", base_sku_code="C101", category="餐边柜",
        target_length_m=1.5, target_width_cm=48, target_height_cm=198,
        modify_parts=[{"name": "门板", "material": "樱桃木-2.2cm",
                       "material_real": "碳纤维蜂窝门板", "qty": 3}],
        description="门板改碳纤维蜂窝门板",
    )
    assert both["spec"]["final_price"] is None
    assert "碳纤维蜂窝门板" in both["spec"]["missing_materials"]
    assert both["custom"]["final_price"] is None


def test_lower_only_remove_upper_without_standard_height_does_not_fake_missing_material():
    from app.models.pricing import PricingSku
    from app.models.product import Product

    db = _db()
    db.add(Product(code="LOW1", name="樱桃木无尺寸餐边柜",
                   category="餐厅-餐边柜", main_material="樱桃木"))
    db.add(PricingSku(product_code="LOW1", sku="标准款", sku_code="LOW101",
                      daily_price=D("6000"), accounting_cost=D("4000")))
    db.commit()
    quote = v2.quote_light(
        db, base_product_code="LOW1", target_length_m=1.5,
        target_width_cm=48, target_height_cm=85,
        remove_parts=[{"material": "上柜", "qty": 1}],
    )
    assert quote["final_price"] is not None
    assert "上柜" not in quote["missing_materials"]
