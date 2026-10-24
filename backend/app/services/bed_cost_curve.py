"""Deterministic within-variant cost calibration, not a fabricated cutting list.

ERP observations are never updated. No zero-intercept regression, cummax repair,
cross-product fallback, or assumed accuracy percentage is permitted here.
"""
from bisect import bisect_left
import math


def estimate_wood(points, *, source_area, source_cost, target_area):
    for value in (source_area, source_cost, target_area):
        if not math.isfinite(value) or value <= 0:
            raise ValueError('床木作核算缺少有效面积或成本')
    if abs(target_area - source_area) < 1e-8:
        return dict(wood=source_cost, mode='unchanged', points=[], estimated=False,
                    note='原尺寸木作成本保持原SKU记录')
    groups = {}
    for p in points:
        if not all(math.isfinite(p[k]) and p[k] > 0 for k in ('area', 'wood')):
            continue
        groups.setdefault(round(p['area'], 8), []).append(p)
    ordered = []
    for area, rows in sorted(groups.items()):
        costs = {round(r['wood'], 2) for r in rows}
        if len(costs) > 1:
            raise ValueError('同款同配置相同面积存在不同木作成本，须内部核实规格，不能平均消除冲突')
        ordered.append({'area': area, 'wood': rows[0]['wood'], 'skus': sorted(r['sku'] for r in rows)})
    if any(b['wood'] < a['wood'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('同款同配置木作成本随尺寸倒挂，须内部核实，不能自动抹平成单调价格')
    if len(ordered) < 2:
        return dict(wood=source_cost * target_area / source_area,
                    mode='insufficient_samples', points=ordered, estimated=True,
                    note='同配置不足两个有效尺寸成本，暂用面积比例；缺少校准依据，不属于已验证精度')
    areas = [p['area'] for p in ordered]
    i = bisect_left(areas, target_area)
    if i < len(areas) and abs(areas[i] - target_area) < 1e-8:
        return dict(wood=ordered[i]['wood'], mode='recorded_area', points=[ordered[i]],
                    estimated=False, note='木作采用同款同配置对应面积的ERP实录成本；形状仍须按外尺寸确认')
    outside = i == 0 or i == len(areas)
    pair = ordered[:2] if i == 0 else ordered[-2:] if i == len(areas) else ordered[i-1:i+1]
    a, b = pair
    slope = (b['wood'] - a['wood']) / (b['area'] - a['area'])
    cost = a['wood'] + slope * (target_area - a['area'])
    if cost <= 0:
        raise ValueError('定制尺寸超出成本曲线可用范围，请补充内部板件用量；不能返回零或负成本')
    return dict(wood=cost, mode='extrapolated' if outside else 'interpolated', points=pair,
                estimated=True, marginal_cost_per_m2=slope,
                note=('超出已知尺寸范围，按最近两档边际成本外推，精度未实测' if outside else
                      '按相邻两档ERP木作成本差额插值，保留固定成本，不按整套木作同比缩放'))


def calibrated_wood(skus, basis, selected):
    from app.services.custom_quote_v2_service import _parse_size_info
    points = []
    _, _, selected_height = _parse_size_info(selected.size_info)
    for sku in skus:
        length, width, height = _parse_size_info(sku.size_info)
        if not length or not width or height != selected_height or not sku.wood_cost:
            continue
        points.append({'sku': sku.sku_code, 'area': length * width / 10000,
                       'wood': float(sku.wood_cost), 'length_cm': length, 'width_cm': width})
    # A user-provided reference override does not rewrite peer geometry.
    if basis['dimension_conflict']:
        points = []
    result = estimate_wood(points, source_area=basis['reference_area_m2'],
                           source_cost=float(selected.wood_cost),
                           target_area=basis['target_length_cm'] * basis['target_width_cm'] / 10000)
    result['sample_count'] = len(points)
    target_shape = (basis['target_length_cm'], basis['target_width_cm'])
    source_shape = (basis['reference_length_cm'], basis['reference_width_cm'])
    if result['mode'] == 'unchanged' and target_shape != source_shape:
        result.update(mode='equal_area_shape_change', estimated=True,
                      note='长宽形状改变但面积相同；当前只能沿用木作成本，结构差额缺少观测依据')
    if result['mode'] == 'recorded_area' and not any(
            (p['length_cm'], p['width_cm']) == target_shape for p in points):
        result.update(mode='area_equivalent', estimated=True,
                      note='对应面积有成本实录，但长宽组合不同；属于面积等效估算，不是同规格实录')
    result['limitations'] = [
        '尺寸成本已按同配置校准；不同长宽组合的结构差异仍属于内部估算',
        '铺板及外购配件沿用原SKU整套成本；更换铺板或配件须单独核算差额',
        '去板件沿用已确认的材料扣减规则，不假设加工人工同比退还',
    ]
    return result


def validate_removals(db, sku, removed, added):
    from app.models.bom import BomLine
    rows = db.query(BomLine).filter(BomLine.product_code == sku.product_code,
                                    BomLine.sku_code == sku.sku_code).all()
    names = [str(p.get('material') or '') for p in removed or []]
    if len(names) != len(set(names)):
        raise ValueError('同一配件不能重复扣减')
    if set(names) & {str(p.get('material') or '') for p in added or []}:
        raise ValueError('同一物料同时增减，请先合并净变化，避免重复核算')
    for p in removed or []:
        name, qty = str(p.get('material') or ''), float(p.get('qty', 1))
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError('扣减数量须为有效正数')
        if name == '床头板':
            if qty != 1 or not p.get('material_real') or not all(
                    math.isfinite(float(p.get(k) or 0)) and float(p.get(k) or 0) > 0
                    for k in ('length_cm', 'width_cm')):
                raise ValueError('去床头须明确一件板的实际宽高及木料')
            if any(k in (sku.sku or '') for k in ('无床头', '软包', '编织')) or any(
                    any(k in (r.material_name or '') for k in ('软包', '编织')) for r in rows):
                raise ValueError('软包、编织或无床头款不能套用实木平板扣减')
            continue
        own = [r for r in rows if r.material_name == name]
        if not own or any(k in name for k in ('木作', '铺板')):
            raise ValueError(f'扣减物料不属于原SKU可单独扣除的配件：{name}')
        if qty > sum(float(r.qty_per_product) for r in own):
            raise ValueError(f'扣减数量超过原SKU实际数量：{name}')
