import sys
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import campaign_seven_no_sales_prepare as m


@pytest.fixture(scope='module')
def built():
    return m.build()


def test_32_exact_and_unready(built):
    raw, r = built
    assert r['ordinary_skus']==32 and len(r['items'])==7
    assert len(r['excluded_custom_skus'])==7 and m.eight.CLOUD not in r['items']
    assert not r['upload_ready'] and not r['platform_write'] and not r['business_complete']
    assert not r['campaign_retry_allowed'] and len(r['blockers'])==3
    assert not r['old_discount']['closure_verified'] and r['old_discount']['offer_id'] is None


def test_frozen_target_and_g_not_p(built):
    audit, prices, _=m.eight.sources()
    frozen={(r['item'],r['sku']):r for r in audit['activity_rows']}
    different=0
    for r in built[1]['rows']:
        original=frozen[r['item'],r['sku']]
        assert Decimal(r['snapshot_base_g'])-Decimal(r['deduct'])==Decimal(original['target'])
        assert r['official_cut']=='0.00'
        assert r['frozen_target']==r['expected_if_g_unchanged_and_no_other_discounts']
        if Decimal(r['snapshot_base_g'])!=Decimal(r['failed_attempt_p']):
            different+=1
            assert Decimal(r['deduct'])!=Decimal(r['failed_attempt_p'])-Decimal(r['frozen_target'])
    assert different==32


def test_worksheet_readback_and_zip(built):
    with m.eight._archive(m.eight.SINGLE.read_bytes()) as a,m.eight._archive(built[0]) as b:
        assert a.namelist()==b.namelist()
        changed=[n for n in a.namelist() if a.read(n)!=b.read(n)]
        assert len(changed)==1 and changed[0].startswith('xl/worksheets/')
        _,_,cells,_=m.eight._read(b,changed[0])
        got={(r['A'],r['B']):r['C'] for n,r in cells.items() if n>1}
        assert len(got)==32
        for r in built[1]['rows']:
            assert Decimal(got[r['item'],r['sku']])==Decimal(r['deduct'])


@pytest.mark.parametrize('column,value,error',[
    ('Z','成功','not_exact_no_sales'),('Z','','not_exact_no_sales'),
    ('AA','价格超限','not_exact_no_sales'),('N','0.00','attempt_price'),
    ('X','15','attempt_discount'),('Y','1','attempt_discount'),
    ('E','999','scope_extra'),('A','720234422814','scope_extra')])
def test_reject_wrong_failure(monkeypatch,column,value,error):
    raw=m.REPORT.read_bytes()
    cells,merges=m.eight.workbook_rows(raw,'商品SKU导入列表')
    expected={(m.eight._effective(cells,merges,n,'A'),r['E']):r['N']
              for n,r in cells.items() if n>=4 and r.get('E')}
    changed=deepcopy(cells);changed[4][column]=value
    monkeypatch.setattr(m.eight,'workbook_rows',lambda *a:(changed,merges))
    with pytest.raises(ValueError,match=error):m.parse_failure(raw,expected)


def test_incomplete_report_rejected(monkeypatch):
    raw=m.REPORT.read_bytes();cells,merges=m.eight.workbook_rows(raw,'商品SKU导入列表')
    expected={(m.eight._effective(cells,merges,n,'A'),r['E']):r['N']
              for n,r in cells.items() if n>=4 and r.get('E')}
    del cells[42]
    monkeypatch.setattr(m.eight,'workbook_rows',lambda *a:(cells,merges))
    with pytest.raises(ValueError,match='incomplete'):m.parse_failure(raw,expected)


@pytest.mark.parametrize('column,value,error',[
    ('H','1','other_promotions'),('J','10%','other_promotions'),
    ('F','0','base_conflict'),('G','','invalid_money'),('E','NaN','invalid_money')])
def test_snapshot_conflicts_rejected(column,value,error):
    audit,prices,_=m.eight.sources()
    r=next(r for r in audit['activity_rows'] if r['item'] in m.ITEMS and not r['custom'])
    prices[r['item'],r['sku']][column]=value
    with pytest.raises(ValueError,match=error):m.calculate(audit,prices)


@pytest.mark.parametrize('target',['0','999999','NaN'])
def test_invalid_target_no_zero_fallback(target):
    audit,prices,_=m.eight.sources()
    r=next(r for r in audit['activity_rows'] if r['item'] in m.ITEMS and not r['custom'])
    r['target']=target
    with pytest.raises(ValueError):m.calculate(audit,prices)


def test_duplicate_generation_never_releases(tmp_path,monkeypatch):
    d=tmp_path/'ledger';d.mkdir();monkeypatch.setattr(m,'LEDGER',d)
    with pytest.raises(ValueError,match='no_replay'):m.generate(tmp_path/'new')


def test_existing_output_preserved(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'LEDGER',tmp_path/'ledger')
    with pytest.raises(ValueError,match='no_overwrite'):m.generate(tmp_path)


def test_preparation_does_not_mutate_old_proofs(built):
    m.eight.pinned(m.eight.LEDGER/'receipt.json',m.PRIOR_SHA)
    m.eight.pinned(m.REPORT,m.REPORT_SHA)
    m.eight.pinned(m.eight.ROOT/'official-eight-price-details.xlsx',m.eight.EXPORT_SHA)


def test_completed_scope_rejected(monkeypatch):
    audit,_,_=m.eight.sources()
    r=next(r for r in audit['activity_rows'] if r['item'] in m.ITEMS and not r['custom'])
    monkeypatch.setattr(m.eight,'load_current_protection',lambda:{'protected_pairs':{(r['item'],r['sku'])}})
    with pytest.raises(ValueError,match='completed_scope_overlap'):m.build()
