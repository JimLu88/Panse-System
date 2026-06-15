"""定制报价 金标准回归测试 — 锁住计价口径, 以后改参数/重构不跑偏。"""
from decimal import Decimal


def test_compute_quote_pipeline():
    """已知板单 → 期望最终报价(锁住 成本→工厂利润25%→畔色成本→×安全→÷0.85 管线)。

    1块板 樱桃木 单价300 长1.0m×宽0.5m → 板成本150;
    厂内150 → 工厂利润37.5 → 工厂木作总187.5 → 畔色成本187.5(无配件/打包/运费/安装) →
    ×1.0(安全) → ÷0.85 = 220.59。
    """
    from app.services.custom_quote_service import Line, compute_quote

    board = Line(part="顶板", material="樱桃木", unit_price=Decimal("300"),
                 qty=Decimal("1"), length_m=Decimal("1.0"), width_m=Decimal("0.5"),
                 unit="平方米")
    r = compute_quote(
        wood_lines=[board], accessory_lines=[], labor_fee=Decimal("0"),
        factory_profit_rate=Decimal("0.25"), panse_profit_rate=Decimal("0.15"),
        safety_rate=Decimal("1.0"),
    )
    assert r.wood_cost == Decimal("150.00")
    assert r.factory_in_cost == Decimal("150.00")
    assert r.factory_profit == Decimal("37.50")
    assert r.factory_wood_total == Decimal("187.50")
    assert r.panse_cost == Decimal("187.50")
    assert r.final_quote == Decimal("220.59")
    assert r.factory_quote_compare == Decimal("187.50")


def test_compute_quote_with_accessory_and_fees():
    """带配件+打包/运费/安装: 板150→厂内150→×1.25=187.5; +配件100; +打包50+运费30+安装20;
    畔色成本387.5; ×1.0 ÷0.85 = 455.88。"""
    from app.services.custom_quote_service import Line, compute_quote

    board = Line(part="顶板", material="樱桃木", unit_price=Decimal("300"),
                 qty=Decimal("1"), length_m=Decimal("1.0"), width_m=Decimal("0.5"), unit="平方米")
    acc = Line(part="五金", material="五金件", unit_price=Decimal("100"), qty=Decimal("1"), unit="个")
    r = compute_quote(
        wood_lines=[board], accessory_lines=[acc],
        labor_fee=Decimal("0"), packing_fee=Decimal("50"), freight=Decimal("30"),
        install_fee=Decimal("20"), factory_profit_rate=Decimal("0.25"),
        panse_profit_rate=Decimal("0.15"), safety_rate=Decimal("1.0"),
    )
    assert r.accessory_total == Decimal("100.00")
    assert r.panse_cost == Decimal("387.50")
    assert r.final_quote == Decimal("455.88")


def test_lookup_price_thickness_fallback():
    """配置兜底: 同材种不同厚度查不到精确键时, 用同材种任一价当近似(不再返0)。"""
    from app.services.custom_quote_config_service import DEFAULT_CONFIG, lookup_price

    cfg = dict(DEFAULT_CONFIG)
    assert lookup_price(cfg, "樱桃木-2.2cm") == 300.0           # 精确
    assert lookup_price(cfg, "黑胡桃木-2.8cm") == 500.0          # 同材种(2.2cm=500)近似, 旧版会返0
    assert lookup_price(cfg, "不存在材种-9cm") == 0.0           # 真没有 → 0


def test_classify_size():
    from app.services.custom_quote_config_service import DEFAULT_CONFIG, classify_size

    cfg = dict(DEFAULT_CONFIG)
    assert classify_size(cfg, "餐桌", 2.1) == "大"   # 餐桌阈值[2.0,1.4]
    assert classify_size(cfg, "餐桌", 1.5) == "中"
    assert classify_size(cfg, "餐桌", 1.0) == "小"


def test_detect_target_wood():
    from app.services.customization_ai_service import _detect_target_wood

    assert _detect_target_wood(["把材质从榉木改成樱桃木"]) == "樱桃木"
    assert _detect_target_wood(["升级黑胡桃"]) == "黑胡桃"
    assert _detect_target_wood(["只改尺寸"]) is None
    assert _detect_target_wood([]) is None
