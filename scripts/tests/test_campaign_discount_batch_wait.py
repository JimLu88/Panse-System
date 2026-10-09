import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,'D:/AI/畔色ERP系统/ERP程序/scripts')
spec=importlib.util.spec_from_file_location('transport_batch_candidate',Path(__file__).parents[1]/'campaign_continuous_transport.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.mark.parametrize('step,seconds',[('discount',1800),('discount_amend',1800),('product_export',1800),('signup',300),('discount_readback',1800)])
def test_wait_does_not_repeat_submission(tmp_path,step,seconds):
    calls=[]
    terminal={'job_id':'original','state':'finished','operation':step}
    class Edge:
        def submit(self,s,p):calls.append('submit');return dict(terminal,state='running')
        def status(self,j):calls.append('status');return terminal
        def wait(self,j,*,timeout,progress):
            assert j=='original' and timeout==seconds
            calls.append('wait');return terminal
    obj=object.__new__(m.CampaignTransport)
    obj.edge=Edge();obj.progress=None;obj.release_guard=SimpleNamespace(verify=lambda:None)
    payload={'offers':[{}]*57} if step=='discount_readback' else {}
    assert obj.job(step,payload,tmp_path)==terminal
    assert obj.job(step,payload,tmp_path)==terminal
    assert calls==['submit','wait','status']
