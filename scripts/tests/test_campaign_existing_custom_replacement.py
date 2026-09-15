import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_existing_custom_replacement as replacement


@pytest.mark.parametrize('bad',[None,'new','item','authorization','scope'])
def test_exact_current_authorization_only(bad):
    value=deepcopy(replacement.REQUEST)
    scope={'authorization':replacement.AUTH,'items':[replacement.ITEM]}
    if bad=='new':value['new_sku']='another'
    if bad=='item':value['item']='cabinet'
    if bad=='authorization':value['authorization']='old'
    if bad=='scope':scope['items'].append('cabinet')
    if bad:
        with pytest.raises(ValueError):replacement.validate(value,scope)
    else:replacement.validate(value,scope)


@pytest.mark.parametrize('bad',[None,'old_on','new_off','missing','unrequested','duplicate','incomplete'])
def test_full_published_scope_not_stock_or_missing_baseline(bad):
    wanted=replacement.ORDINARY|replacement.CUSTOM|{replacement.OLD}
    entries=[];rows=[]
    for sku in sorted(wanted):
        enabled=sku!=replacement.OLD
        if (bad=='old_on' and sku==replacement.OLD) or (bad=='new_off' and sku==replacement.NEW):enabled=not enabled
        entries.append({'facts':dict(item=replacement.ITEM,sku=sku,attributes='颜色:'+sku,sku_code='PPS'+sku,price='2000',stock='0')})
        rows.append(dict(index=len(rows),cells=[{'text':sku},{'text':'PPS'+sku},
            {'text':'元','fields':[{'value':'2000'}]},{'text':'件','fields':[{'value':'0'}]}],
            enabled=enabled,switches=[{'aria':str(enabled).lower(),'class_name':'next-switch-on' if enabled else 'next-switch-off'}]))
    scope=dict(complete=bad!='incomplete',sku_facts=entries)
    record=dict(item=replacement.ITEM,state='read',platform_write=False,requested_skus=sorted(wanted),rows=rows)
    if bad=='missing':entries.pop()
    if bad=='unrequested':record['requested_skus'].remove(replacement.NEW)
    if bad=='duplicate':rows.append(deepcopy(rows[0]))
    if bad:
        with pytest.raises(ValueError):replacement.select_active(scope,record)
    else:
        assert replacement.select_active(scope,record)==replacement.CUSTOM|replacement.ORDINARY
        assert len(rows)==13 and all(e['facts']['stock']=='0' for e in entries)
