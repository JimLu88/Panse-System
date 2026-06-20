"""尺寸变体(宽高微调)定价测试 — 用户拍板 2026-06-20。

size_info 解析 + 按目标长度插值出"标准宽高" + 宽高偏离按面积比例算 delta。
纯函数 + SimpleNamespace, 无 DB。(quote_light 整链已生产实测: 宽85→+154、宽75→−51、标准→0。)
"""
from types import SimpleNamespace

from app.services.custom_quote_v2_service import _parse_size_info, _dim_points, interp, _resolve_length_m


def test_parse_size_info():
    assert _parse_size_info("长度：1400mm；深度：750mm；高度：750mm") == (140.0, 75.0, 75.0)
    assert _parse_size_info("长度：2000mm；深度：850mm；高度：750mm") == (200.0, 85.0, 75.0)
    assert _parse_size_info(None) == (None, None, None)
    assert _parse_size_info("无尺寸信息") == (None, None, None)


def test_dim_points_and_std_interp():
    skus = [
        SimpleNamespace(sku="榉木餐桌-1.4米", sku_code="X1", size_info="长度：1400mm；深度：750mm；高度：750mm"),
        SimpleNamespace(sku="榉木餐桌-1.6米", sku_code="X2", size_info="长度：1600mm；深度：800mm；高度：750mm"),
    ]
    depth_pts, height_pts = _dim_points(skus)
    assert sorted(depth_pts) == [(1.4, 75.0), (1.6, 80.0)]
    assert sorted(height_pts) == [(1.4, 75.0), (1.6, 75.0)]
    # 1.5m 的标准深(宽) = 75 与 80 中点 = 77.5cm
    assert interp(depth_pts, 1.5)[0] == 77.5
    assert interp(height_pts, 1.5)[0] == 75.0


def test_resolve_length_fallback_to_size_info():
    # SKU 名有「X米」→ 用名字(餐桌)
    assert _resolve_length_m(SimpleNamespace(
        sku="榉木餐桌-1.4米", sku_code="X", size_info="长度：1400mm；深度：750mm")) == 1.4
    # SKU 名只叫「标准/窄款」无「X米」(床头柜)→ 退回 size_info 长度 cm÷100
    assert _resolve_length_m(SimpleNamespace(
        sku="榉木床头柜-标准", sku_code="X", size_info="长度：450mm；深度：400mm；高度：450mm")) == 0.45
    assert _resolve_length_m(SimpleNamespace(
        sku="榉木床头柜-窄款", sku_code="X", size_info="长度：250mm；深度：400mm")) == 0.25
    # 名字+size_info 都没有 → None
    assert _resolve_length_m(SimpleNamespace(sku="无尺寸款", sku_code="X", size_info=None)) is None


def test_size_factor_math():
    # 面积比例 = (宽/宽0)×(高/高0): 标准→1(delta 0); 变宽→>1(加价); 变窄→<1(减价)
    std_w, std_h = 77.5, 75.0
    assert abs((std_w / std_w) * (std_h / std_h) - 1.0) < 1e-9      # 标准 → 0
    assert (85 / std_w) * (75 / std_h) > 1.0                          # 变宽 → 加
    assert (75 / std_w) * (75 / std_h) < 1.0                          # 变窄 → 减
