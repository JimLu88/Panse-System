import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_entry_authority import file_sha
from campaign_continuous_policy import RULE_SHA
from campaign_continuous_repairs import verified_saved_custom_items


@pytest.mark.parametrize('bad',[None,'report','price','failure','overlay','kind','scope'])
def test_mixed_repair_only_accepts_original_saved_local_half(tmp_path,bad):
    def save(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')
        return str(path.resolve())
    folder=tmp_path/'actions'/'action';folder.mkdir(parents=True)
    custom=[dict(item='1',sku='11',activity_price='396.99')]
    decisions={'1':[dict(sku='11',repair={'kind':'custom_price','price':'396.99'})],
               '2':[dict(sku='22',repair={'kind':'ordinary_discount'})]}
    payload={'decisions':decisions,'items':['1','2'],'failed_batch':'123'}
    report_path=tmp_path/'reports'/'123.json'
    save(report_path,{'errors':[],'source_terminal':'official-terminal'})
    body={'campaign':'campaign','report_path':str(report_path),
          'sources':[{'path':str(report_path),'sha256':file_sha(report_path)}]}
    ap=folder/'custom-authorization.json';fp=folder/'failed-custom-rows.json'
    auth={'campaign':'campaign','continuous_rule_sha':RULE_SHA,'authorized_custom_prices':custom,
          'source_report':str(report_path),'source_report_sha256':file_sha(report_path)}
    failure={'campaign':'campaign','terminal':True,'batch_id':'123',
             'rows':[dict(item='1',sku='11',status='failed')],'source_terminal':'official-terminal'}
    save(ap,auth);save(fp,failure)
    entries=[dict(custom[0],authorization_path=str(ap),authorization_sha256=file_sha(ap),
                  failure_path=str(fp),failure_sha256=file_sha(fp))]
    overlay=tmp_path/'repairs'/'custom-action.json';save(overlay,{'rows':entries})
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE continuous_discount_repairs(id TEXT,body TEXT)')
    db.execute('INSERT INTO continuous_discount_repairs VALUES(?,?)',('claim',json.dumps(body)))
    if bad=='report':save(report_path,{'errors':['changed']})
    if bad=='price':auth['authorized_custom_prices'][0]['activity_price']='1.00';save(ap,auth)
    if bad=='failure':failure['terminal']=False;save(fp,failure)
    if bad=='overlay':save(overlay,{'rows':[]})
    if bad=='kind':decisions['1'][0]['repair']['kind']='rotate'
    if bad=='scope':payload['items'].append('3')
    before=db.total_changes
    with patch('campaign_continuous_repairs.classify_items',return_value=(decisions,{})):
        if bad:
            with pytest.raises(ValueError):verified_saved_custom_items(SimpleNamespace(db=db),'claim',payload,folder)
        else:assert verified_saved_custom_items(SimpleNamespace(db=db),'claim',payload,folder)==['1']
    assert db.total_changes==before
    db.close()


def test_ordinary_only_requires_no_custom_authorization():
    payload={'decisions':{'1':[{'repair':{'kind':'ordinary_discount'}}]}}
    assert verified_saved_custom_items(None,'claim',payload,'unused')==[]
