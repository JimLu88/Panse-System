import sys
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import campaign_remaining_eight_prepare as m


@pytest.fixture(scope='module')
def built():return m.build()


def test_full_scope_and_windows(built):
    files,r=built
    assert len(files)==4 and r['blocked']==[]
    assert [(g['activity_skus'],g['discount_skus']) for g in r['groups']]==[(39,32),(63,49)]
    assert {x['item'] for x in r['rows']}==set(m.ITEMS)
    assert r['groups'][1]['start']=='2026-10-01 00:00:00'
    assert r['old_claims_unchanged'] and not r['platform_write']


def test_equations_and_ordinary_basis(built):
    audit,_,_=m.sources()
    orig={(r['item'],r['sku']):r for r in audit['activity_rows']}
    for row in built[1]['rows']:
        p=m.money(row['campaign_price']);cut=m.money(row['official_cut']);d=m.money(row['single_deduction'])
        assert p-cut-d==m.money(row['expected_after_both_success'])
        assert cut==m.official_cut(p)
        if not row['custom']:
            o=orig[row['item'],row['sku']]
            assert p==m.money(o['activity_price'])
            assert abs(m.money(o['target'])-(p-cut-d))<=Decimal('2')


def test_current_price_is_not_future_activity_base(built):
    row=next(r for r in built[1]['rows'] if r['sku']=='5431027545254')
    assert row['current_one_price']=='5140.00'
    assert row['campaign_price']=='3855.00'


@pytest.mark.parametrize('sku,value',[
    ('5182259562550','977.77'),('5182259562551','1955.55'),
    ('5436206809703','4888.88'),('5605000579544','2832.22'),('6221853678797','1466.66')])
def test_exact_prior_custom_corrections(built,sku,value):
    r=next(r for r in built[1]['rows'] if r['sku']==sku)
    assert r['campaign_price']==value and r['single_deduction']=='0.00'


def test_no_extra_custom_reduction_without_basis(built):
    rows=[r for r in built[1]['rows'] if r['item']==m.CLOUD and r['custom']]
    assert len(rows)==14
    assert {r['campaign_price'] for r in rows}=={'500.00'}


def test_proof_does_not_release_old_controller():
    p=m.preupload_proof()
    assert p['pre_upload_verified'] and p['old_records_unchanged']
    assert not p['old_controller_resume_allowed']


def test_master_zip_preserved(built):
    for name,raw in built[0].items():
        master=m.MASTER if '报名' in name else m.SINGLE
        with m._archive(master.read_bytes()) as a,m._archive(raw) as b:
            assert a.namelist()==b.namelist()
            changed=[n for n in a.namelist() if a.read(n)!=b.read(n)]
            assert len(changed)==1 and changed[0].startswith('xl/worksheets/')


@pytest.mark.parametrize('price,cut',[('892.50','90'),('3855','386'),('1000','100'),('977.77','98')])
def test_official_rounding(price,cut):
    assert m.official_cut(Decimal(price))==Decimal(cut)


def test_block_new_unknown_even_without_list_overlap(monkeypatch):
    audit,prices,caps=m.sources();payload,lists,_=m.terminal()
    offers,attempts,_=m.authority_snapshot();protection=m.load_current_protection()
    projection=m.project(payload,lists,offers,attempts,protection['protected_pairs'])
    p=next(r for r in projection['rows'] if r['item']=='722912832184')
    p['history'].append(dict(unresolved_history_hold=True,window=dict(start=m.AS_OF,end=m.END),original_claims=['other']))
    with pytest.raises(ValueError,match='unresolved_submission_history'):
        m.calculate(audit,prices,caps,projection,m.preupload_proof())


def test_existing_ledger_prevents_second_generation(tmp_path,monkeypatch):
    ledger=tmp_path/'ledger';ledger.mkdir();monkeypatch.setattr(m,'LEDGER',ledger)
    with pytest.raises(ValueError,match='already_generated'):m.generate(tmp_path/'second')
