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
