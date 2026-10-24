import pytest
from app.services.bed_cost_curve import estimate_wood


def points():
    # Actual same-variant ERP example: width 125/140/155/185cm, depth 210cm.
    return [dict(sku=str(w), area=w*210/10000, wood=c)
            for w,c in [(125,2010),(140,2040),(155,2070),(185,2210)]]


def test_known_smaller_bed_uses_recorded_cost_not_area_proportion():
    r=estimate_wood(points(),source_area=3.885,source_cost=2210,target_area=2.625)
    assert r['wood']==2010
    assert r['mode']=='recorded_area'
    assert r['wood'] != pytest.approx(1493.24)


def test_unseen_internal_point_retains_fixed_cost():
    rows=[p for p in points() if p['sku']!='140']
    r=estimate_wood(rows,source_area=3.885,source_cost=2210,target_area=2.94)
    assert r['wood']==pytest.approx(2040)
    assert r['mode']=='interpolated'


def test_selected_cost_unchanged_even_when_other_records_conflict():
    r=estimate_wood(points()+[dict(sku='bad',area=2.625,wood=999)],
                    source_area=3.885,source_cost=2210,target_area=3.885)
    assert r['wood']==2210


def test_duplicate_cost_disagreement_not_averaged():
    with pytest.raises(ValueError,match='不同木作成本'):
        estimate_wood(points()+[dict(sku='bad',area=2.625,wood=999)],
                      source_area=3.885,source_cost=2210,target_area=3)


def test_cost_inversion_not_silently_repaired():
    with pytest.raises(ValueError,match='倒挂'):
        estimate_wood([dict(sku='a',area=1,wood=1000),dict(sku='b',area=2,wood=500)],
                      source_area=1,source_cost=1000,target_area=1.5)


def test_out_of_range_reports_extrapolation_and_never_negative():
    r=estimate_wood(points(),source_area=3.885,source_cost=2210,target_area=4)
    assert r['mode']=='extrapolated' and r['estimated']
    with pytest.raises(ValueError):
        estimate_wood([dict(sku='a',area=1,wood=10),dict(sku='b',area=2,wood=1000)],
                      source_area=1,source_cost=10,target_area=.5)


def test_insufficient_samples_explicitly_unvalidated():
    r=estimate_wood(points()[:1],source_area=2.625,source_cost=2010,target_area=3)
    assert r['mode']=='insufficient_samples' and r['estimated']


@pytest.mark.parametrize('bad',[0,-1,float('nan'),float('inf')])
def test_invalid_measurements_rejected(bad):
    with pytest.raises(ValueError):
        estimate_wood(points(),source_area=3.885,source_cost=2210,target_area=bad)
