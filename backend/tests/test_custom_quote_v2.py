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
    return sessionmaker(bind=eng, autoflush=False, future=True)()


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


def test_quote_light_unknown_product():
    db = _db()
    r = v2.quote_light(db, base_product_code="NOPE", target_length_m=1.5)
    assert r["final_price"] is None and "无此产品" in r["error"]


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
                      factory_cost=D("900"), accounting_cost=D("1300")))
    db.add(PricingSku(product_code="B1", sku="榉木无边床-1.8米", sku_code="B1-1.8",
                      size_category="大型", daily_price=D("2300"), wood_cost=D("720"),
                      factory_cost=D("1050"), accounting_cost=D("1500")))
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


def test_material_delta_prefers_sibling():
    """有现成黑胡桃款 → 用其真实价差 (比反推优先)。"""
    db = _db()
    _seed_bed(db, with_walnut_sibling=True)
    r = v2.quote_light(db, base_product_code="B1", target_length_m=1.5, target_material="黑胡桃")
    assert r["material_delta"] == 800.0          # 2800(现成款真实价) − 2000(锚点)
    assert r["final_price"] == 2800.0
    assert "现成款" in r["breakdown"][-1]["label"]


# ───────── 盈亏平衡工厂价 (净不亏红线) ─────────

def test_break_even_light():
    """普通定制盈亏平衡: 售价 − (accounting−factory) = 红线; 安全垫 = 红线 − 预测 = 售价 − accounting。"""
    db = _db()
    _seed_bed(db)
    r = v2.quote_light(db, base_product_code="B1", target_length_m=1.5)
    assert r["factory_predicted"] == 900.0                 # 定价表 factory_cost@1.5
    assert r["break_even_factory"] == 1600.0               # 2000 − (1300−900)
    assert r["break_even_buffer"] == 700.0                 # 1600 − 900 = 售价2000 − accounting1300


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
    assert r2["addremove_delta"] == -48.75         # 30×1.3×1.25÷0.85×0.85(删除保守, 只高不低)
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
