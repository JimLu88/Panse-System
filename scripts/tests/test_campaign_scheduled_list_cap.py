import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_official_failure_report as parser


def parse(monkeypatch, message, names=None):
    names = names or [('6221853678797', '尺寸微定制;整装')]
    rows = {1: dict(zip('ABCDEF', parser.HEADERS.values()))}
    for index, (sku, name) in enumerate(names, 2):
        rows[index] = dict(zip('ABCDEF', ['797139954559', sku, name,
                                              '1466.66', '失败', message if index == 2 else '']))
    monkeypatch.setattr(parser, 'read_rows', lambda *args: rows)
    raw = b'bound-official-report-fixture'
    return parser.parse_feedback(raw, expected_sha=hashlib.sha256(raw).hexdigest(),
                                 batch='933010293', expected_items=['797139954559'])


def message(comparison='小于', cap='500'):
    return ('您的资质：您的sku：尺寸微定制;整装 在管控期标价为您设置一口价为'
            f'[{cap}元]生效日期为[2026-09-27 21:25:49~2026-10-12 21:25:49]; '
            f'要求商品报名的活动价{comparison}一口价\n'
            '您的资质：活动要求活动价小于等于商品一口价skuId:6221853678797 '
            'sku名称:尺寸微定制;整装的活动价格为1466.66。不满足')


@pytest.mark.parametrize('comparison,cap', [('小于', '499.99'), ('小于等于', '500')])
def test_scheduled_exact_name_and_strictest_comparison(monkeypatch, comparison, cap):
    errors = parse(monkeypatch, message(comparison))['errors']
    assert len(errors) == 1
    assert errors[0]['sku'] == '6221853678797'
    assert errors[0]['kind'] == 'list_price'
    assert errors[0]['official_cap'] == cap
    assert errors[0]['official_raw_cap'] == '500'
    assert errors[0]['submitted_price'] == '1466.66'


def test_truncated_scheduled_clause_stays_unknown(monkeypatch):
    errors = parse(monkeypatch, message().split('生效日期')[0])['errors']
    assert errors[0]['kind'] == 'unknown'


def test_duplicate_name_never_guesses_sku(monkeypatch):
    errors = parse(monkeypatch, message(), [('1', '尺寸微定制;整装'),
                                           ('2', '整装;尺寸微定制')])['errors']
    assert errors[0]['parse_issue'] == 'reported_sku_name_missing_or_ambiguous'


def test_old_plain_list_clause_unchanged(monkeypatch):
    errors = parse(monkeypatch, '您的sku：尺寸微定制;整装 在管控期标价为500元')['errors']
    assert errors[0]['official_cap'] == '500'
    assert errors[0]['kind'] == 'list_price'


def test_extra_unparsed_price_clause_is_not_erased(monkeypatch):
    errors = parse(monkeypatch, message() + '\n您的sku：另一个 在管控期标价为')['errors']
    assert any(e['kind'] == 'unknown' for e in errors)


@pytest.mark.parametrize('original,expected', [('2000', 'repair'), ('3000', 'rotation_approval')])
def test_custom_fixed_floor_not_ordinary_two_yuan(monkeypatch, original, expected):
    from campaign_feedback_normalization import normalize_errors
    from campaign_continuous_policy import classify
    parsed = parse(monkeypatch, message())
    pair = ('797139954559', '6221853678797')
    errors = normalize_errors(parsed,
        submitted_rows=[dict(item=pair[0], sku=pair[1], erp_code='CUSTOM', activity_price='1466.66')],
        erp_rows=[dict(code='CUSTOM', custom=True, daily='2000')],
        fixed_bases={pair: dict(original=original, source_sha256='verified-test-source')},
        actual_discounts=[], rate='0.1', target_mode='medium')
    assert errors[0]['feasible_signup_price'] == '499.99'
    assert classify(errors[0])['action'] == expected


def test_reclassification_keeps_newly_resolved_sku_only():
    from campaign_price_report_recovery import retain_failed_scope
    report = dict(batch='933010293', errors=[dict(item='1', sku='')])
    resolved = dict(item='1', sku='11', batch='933010293', terminal='failed',
                    official_evidence={'sha256': 'bound-source'})
    inputs = [resolved, dict(resolved, item='2'), dict(resolved, sku='12'),
              dict(resolved, batch='wrong'), dict(resolved, terminal='success'),
              dict(resolved, official_evidence=None)]
    assert retain_failed_scope(inputs, report, [dict(item='1', sku='11')]) == [resolved]
