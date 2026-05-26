"""全定制报价引擎单测 — 对照「全定制算价 v0.5」表格的已知合计。"""
from decimal import Decimal as D

from app.services.custom_quote_service import Line, compute_quote, material_diff_surcharge


def test_area_board_cost():
    # 床头板: 300 ￥/㎡ × 1.95m × 0.51m × 1 = 298.35
    ln = Line("床头板", "樱桃木-2.2cm", D("300"), D("1"), length_m=D("1.95"), width_m=D("0.51"))
    assert ln.cost() == D("298.35")


def test_unit_based_costs():
    assert Line("把手", "金属把手", D("30"), D("2"), unit="个").cost() == D("60.00")
    assert Line("灯带", "灯带", D("50"), D("1"), length_m=D("0.3"), unit="米").cost() == D("15.00")
    assert Line("轨道", "抽屉轨道", D("22"), D("2"), unit="付").cost() == D("44.00")


def test_bedside_cabinet_matches_spreadsheet():
    """床头柜小型: 表格期望最终报价 840。"""
    r = compute_quote(
        wood_lines=[],
        accessory_lines=[
            Line("把手", "金属把手", D("30"), D("1"), unit="个"),
            Line("灯带", "灯带", D("50"), D("1"), length_m=D("0.3"), unit="米"),
            Line("抽屉轨道", "抽屉轨道", D("22"), D("2"), unit="付", is_drawer_rail=True),
        ],
        labor_fee=D("300"), packing_fee=D("100"), freight=D("100"), install_fee=D("50"),
    )
    assert r.factory_in_cost == D("300.00")
    assert r.factory_profit == D("75.00")          # 300 × 0.25
    assert r.factory_wood_total == D("375.00")     # 厂内 × 1.25
    assert r.accessory_total == D("89.00")
    assert r.panse_cost == D("714.00")
    assert r.final_quote == D("840.00")            # 714 / 0.85
    assert r.drawer_rail_total == D("44.00")
    assert r.factory_quote_compare == D("419.00")  # 木作总成本 + 轨道


def test_full_pipeline_ratios():
    """显式验证 ×1.25 工厂利润 与 ÷0.85 畔色利润。"""
    r = compute_quote(
        wood_lines=[Line("板", "樱桃木", D("1000"), D("1"), length_m=D("1"), width_m=D("1"))],
        accessory_lines=[],
        labor_fee=D("0"),
        factory_profit_rate=D("0.25"), panse_profit_rate=D("0.15"),
    )
    assert r.factory_wood_total == D("1250.00")   # 1000 × 1.25
    assert r.panse_cost == D("1250.00")
    assert r.final_quote == D("1470.59")          # 1250 / 0.85


def test_material_diff_surcharge():
    # 樱桃木300 → 黑胡桃木500, 面积2㎡: (500-300)×2×1.25 = 500
    assert material_diff_surcharge(
        new_unit_price=D("500"), base_unit_price=D("300"), area_m2=D("2"),
    ) == D("500.00")


def test_projection_estimate():
    r = compute_quote(
        wood_lines=[], accessory_lines=[], labor_fee=D("0"),
        overall_width_m=D("1.0"), overall_height_m=D("1.9"),
        projection_rate=D("900"),
    )
    assert r.projection_area_m2 == D("1.90")       # 1.0 × 1.9
    assert r.projection_estimate == D("1710.00")   # 1.9 × 900


# ── 报价参数配置 (后台可调) ───────────────────────────────────────────────────

def _cfg_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def test_size_classification_by_rules():
    from app.services import custom_quote_config_service as c
    cfg = c.DEFAULT_CONFIG
    assert c.classify_size(cfg, "餐边柜", 2.1) == "大"
    assert c.classify_size(cfg, "餐边柜", 1.5) == "中"
    assert c.classify_size(cfg, "餐边柜", 1.0) == "小"
    assert c.classify_size(cfg, "电视柜", 1.8) == "中"   # 1.4-2.0 中
    assert c.classify_size(cfg, "电视柜", 2.0) == "大"


def test_labor_lookup_matches_table():
    from app.services import custom_quote_config_service as c
    cfg = c.DEFAULT_CONFIG
    assert c.lookup_labor(cfg, "餐边柜", 2.1) == 1480   # 大
    assert c.lookup_labor(cfg, "餐边柜", 1.0) == 840    # 小
    assert c.lookup_labor(cfg, "电视柜", 1.8) == 800    # 中
    assert c.lookup_labor(cfg, "餐桌", 1.8) == 400      # 中


def test_config_persist_and_merge():
    from app.services import custom_quote_config_service as c
    db = _cfg_db()
    c.save_config(db, {"factory_profit_rate": 0.30})
    cfg = c.get_config(db)
    assert cfg["factory_profit_rate"] == 0.30
    assert cfg["panse_profit_rate"] == 0.15        # 未动的保留默认
    assert cfg["labor"]["餐边柜"] == [840, 1070, 1480]


# ── 板单→报价 (接配置) ────────────────────────────────────────────────────────

def test_quote_from_spec_uses_config():
    from app.services.custom_quote_service import quote_from_spec, BoardSpec
    db = _cfg_db()
    # 餐边柜 2.1m: 一块"等效板"模拟真实材料5044(黑胡桃500→10.088㎡) + 抽屉轨道×4
    boards = [
        BoardSpec("板单合计", "黑胡桃木-2.2cm", length_cm=1008.8, width_cm=100, qty=1),
        BoardSpec("抽屉轨道", "抽屉轨道", 0, 0, qty=4, unit="付", is_drawer_rail=True),
    ]
    r = quote_from_spec(db, product_type="餐边柜", length_m=2.1, boards=boards,
                        overall_width_m=2.1, overall_height_m=1.95)
    assert r.labor_fee == 1480.0                       # 餐边柜大型, 来自配置
    assert abs(float(r.wood_cost) - 5044) < 5          # 黑胡桃 10.088㎡×500
    # 工厂对比 = (5044+1480)×1.25 + 88轨道 = 8243, 工厂8500 → 进500
    assert abs(float(r.factory_quote_compare) - 8243) < 5
    assert 8500 - float(r.factory_quote_compare) < 500


# ── 联动: 物料表为唯一数据源 (优先于配置) ────────────────────────────────────

def test_lookups_prefer_material_table():
    from app.services import custom_quote_config_service as c
    from app.models.material import Material
    db = _cfg_db()
    cfg = c.DEFAULT_CONFIG
    # 物料表里改了价/人工 → 应覆盖配置默认 (联动)
    db.add_all([
        Material(code="W1", name="黑胡桃木-2.2cm", price=600),       # 配置默认500
        Material(code="L1", name="餐边柜-人工费-大型", price=1600),    # 配置默认1480
        Material(code="P1", name="打包费用-大型家具", price=500),      # 配置默认400
    ])
    db.commit()
    assert c.lookup_price(cfg, "黑胡桃木-2.2cm", db=db) == 600
    assert c.lookup_labor(cfg, "餐边柜", 2.1, db=db) == 1600
    assert c.lookup_packing(cfg, "餐边柜", 2.1, db=db) == 500
    # 没有 db / 表里没有 → 回落配置
    assert c.lookup_price(cfg, "黑胡桃木-2.2cm") == 500
    assert c.lookup_labor(cfg, "餐边柜", 2.1) == 1480


def test_safety_rate_biases_up():
    from app.services.custom_quote_service import compute_quote, Line
    from decimal import Decimal as D
    base = compute_quote(wood_lines=[Line("板","樱桃木",D("1000"),D("1"),length_m=D("1"),width_m=D("1"))],
                         accessory_lines=[], labor_fee=D("0"), safety_rate=D("1.0"))
    safe = compute_quote(wood_lines=[Line("板","樱桃木",D("1000"),D("1"),length_m=D("1"),width_m=D("1"))],
                         accessory_lines=[], labor_fee=D("0"), safety_rate=D("1.05"))
    assert safe.final_quote > base.final_quote                    # 上浮
    assert safe.factory_quote_conservative > safe.factory_quote_compare


# ── 竞品 Top-10 匹配 ──────────────────────────────────────────────────────────

def test_competitor_top_ranking():
    from app.models.competitor import CompetitorPrice
    from app.services.product_match_service import _similarity
    db = _cfg_db()
    db.add_all([
        CompetitorPrice(store="A", product="樱桃木餐边窄柜", sku_name="窄柜-洞石背板-1.0米", daily_price=5135),
        CompetitorPrice(store="B", product="黑胡桃餐边柜", sku_name="曜黑柜-整柜-1.5米", daily_price=14191),
        CompetitorPrice(store="C", product="岩板餐桌", sku_name="餐桌-1.4米", daily_price=3000),
    ])
    db.commit()
    rows = db.query(CompetitorPrice).all()
    scored = sorted(((_similarity("樱桃木窄柜 1.0米", " ".join(filter(None,[r.product,r.sku_name,r.wood]))), r) for r in rows),
                    key=lambda x: x[0], reverse=True)
    assert scored[0][1].store == "A"          # 樱桃木窄柜 命中最高
    assert scored[0][0] > scored[-1][0]
