import importlib.util
from pathlib import Path
import copy
import pytest

spec = importlib.util.spec_from_file_location('walnut_plan', Path(__file__).resolve().parents[2] / 'scripts/campaign_walnut_amount_plan.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample():
    common = {'商品Id': '1', 'skuId': '2', '活动时间': m.WINDOW_TEXT,
              '优惠层级': '单品级', '一口价': '8060', '标价': '6045',
              '1件预估价(公域)': '4020.41'}
    return dict(reference_item_id='1', reference_sku_id='2', sku_code='ERP1',
                source_cell='List!K98', target_buyer_price='3940', reference_daily_price='9999',
                official_lines=[dict(common, 工具名称=t, 活动id=i, 优惠金额=a)
                                for t, i, a in [('官方大促', 'a', '2015'),
                                                ('官方立减', 'b', '907'),
                                                ('单品立减', m.OFFER, '1117.59')]])


def test_target_direct_preserves_platform_signup_not_new_erp_daily():
    result = m.calculate(sample(), '1117.59')
    assert result['daily'] == '6045'
    assert result['new_deduct'] == '1198'
    assert result['target'] == '3940'


@pytest.mark.parametrize('change', [
    lambda r: r['official_lines'].pop(),
    lambda r: r['official_lines'][0].update(skuId='3'),
    lambda r: r['official_lines'][1].update(工具名称='未知券'),
    lambda r: r['official_lines'][2].update(活动id='other'),
    lambda r: r['official_lines'][2].update(活动时间='other'),
    lambda r: r['official_lines'][2].update(优惠金额='1118'),
    lambda r: r.update(target_buyer_price='NaN'),
    lambda r: r.update(target_buyer_price='6000'),
])
def test_bad_evidence_rejected(change):
    row = copy.deepcopy(sample())
    change(row)
    with pytest.raises(ValueError):
        m.calculate(row, '1117.59')


def test_old_amount_compare_and_swap():
    with pytest.raises(ValueError, match='current_old_amount_changed'):
        m.calculate(sample(), '1000')
