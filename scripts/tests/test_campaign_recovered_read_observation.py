from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from campaign_continuous_transport import CampaignTransport,persist,load

@pytest.mark.parametrize('step,oldstate,allowed',[('product_export','unknown',True),('product_export','finished',False),('signup','unknown',False)])
def test_recovered_read_adds_observation_without_overwriting_old(tmp_path,step,oldstate,allowed):
    old={'job_id':'same','operation':step,'state':oldstate,'result':{'old':True}}
    fresh={'job_id':'same','operation':step,'state':'finished','result':{'new':True}}
    original=tmp_path/(step+'-observation.json');persist(original,old)
    persist(tmp_path/(step+'-job.json'),{'job_id':'same','step':step})
    t=CampaignTransport.__new__(CampaignTransport);t.release_guard=NS(verify=lambda:None)
    t.edge=NS(status=Mock(return_value=fresh),submit=Mock());t.progress=None
    if allowed:
        assert t.job(step,{},tmp_path)==fresh
        assert len(list(tmp_path.glob(step+'-observation-*.json')))==1
    else:
        with pytest.raises(ValueError,match='immutable'):t.job(step,{},tmp_path)
    assert load(original)==old
    t.edge.submit.assert_not_called()
