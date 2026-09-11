from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_pin_signup_master import received_master
from campaign_fixed_signup_template import resolve_master
from campaign_continuous_transport import persist
from campaign_official_template import MergeRanges,_effective


def test_user_download_is_separate_bound_receipt(tmp_path):
    source=tmp_path/'official.xlsx';source.write_bytes(b'fixture-only')
    request={'identity':{'campaign_id':'legacy'},'items':['1']}
    page=dict(ok=True,operation='program_inspect_generated_template',source_job_id='a'*64,
              download_clicked=False,platform_write=False,official_filename=source.name,
              official_page_url='https://myseller.taobao.com/',observed_at='2026-09-12',**request)
    with patch('campaign_pin_signup_master.template_rows',return_value=[{'item':'1','sku':'2'}]):
        result=received_master(source,page,request,'a'*64)
        assert result['receipt_kind']=='user_received_existing_download'
        assert result['sku_rows']==1 and not result['business_write']
        for field,value in [('official_filename','other.xlsx'),('source_job_id','b'*64),('items',['3']),('platform_write',True)]:
            with pytest.raises(ValueError,match='binding_mismatch'):
                received_master(source,dict(page,**{field:value}),request,'a'*64)
    manifest=persist(tmp_path/'manifest.json',result)
    with patch('campaign_fixed_signup_template.template_rows',return_value=[{'item':'1','sku':'2'}]):
        assert resolve_master(manifest,identity=request['identity'],roots=[tmp_path])['state']=='downloaded'


def test_merge_index_preserves_old_lookup_semantics():
    rows={4:{'A':'one','D':'failed'},5:{'A':''},8:{'A':'two'}}
    ranges=['A4:A7','D4:D7','A8:A12']
    indexed=MergeRanges(ranges)
    for n in range(1,15):
        for col in ['A','D','X']:
            assert _effective(rows,indexed,n,col)==_effective(rows,ranges,n,col)
    bad=MergeRanges(['A4:A7','A5:A8'])
    with pytest.raises(ValueError,match='overlapping_merge'):_effective(rows,bad,6,'A')
