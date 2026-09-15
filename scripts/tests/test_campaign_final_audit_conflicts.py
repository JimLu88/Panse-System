import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_final_audit import execution_export_conflicts


@pytest.mark.parametrize('failed,success,exported,conflict',[
    (True,False,'registered',True),(False,True,'not_verified_registered',True),
    (True,False,'not_verified_registered',False),(False,True,'registered',False),
    (False,False,'registered',False)])
def test_current_failure_and_legacy_success_are_not_overwritten(failed,success,exported,conflict):
    result={'segments':[{'segment_id':'s','exceptions':{'i':[{'action':'manual','reason':'fixed_original_evidence_missing'}]} if failed else {},
        'success':{'i':'old-proof'} if success else {}}]}
    audit={'segments':[{'segment_id':'s','products':[{'item':'i','status':exported}]}]}
    found=execution_export_conflicts(result,audit)
    assert bool(found)==conflict
    if found:assert found[0]['item']=='i' and found[0]['segment_id']=='s'


def test_other_campaign_success_does_not_erase_failure():
    result={'segments':[{'segment_id':'a','exceptions':{'i':[{'action':'manual'}]}}]}
    audit={'segments':[{'segment_id':'b','products':[{'item':'i','status':'registered'}]}]}
    assert execution_export_conflicts(result,audit)==[]
