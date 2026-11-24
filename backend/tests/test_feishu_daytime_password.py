import hashlib
from datetime import datetime, timezone, timedelta
import pytest
from app.models.import_file import ImportedFile
from app.services import agent_ingest_service as ai, scheduler, web_agent_service as web

CN=timezone(timedelta(hours=8))

@pytest.mark.parametrize('manual', [True,False])
def test_daytime_password_checks_original_manual_batch(db_session,monkeypatch,manual):
    now=datetime.now(CN).replace(hour=15,minute=0,second=0,microsecond=0)
    batch='orders-'+now.strftime('%Y%m%d')+'-'+'a'*32
    roles={'orders.xlsx':'orders','sales.xlsx':'sales_detail','shipping.xlsx':'shipping'}
    payload={'started_at':now.replace(hour=13).isoformat(),'order_business_date':now.date().isoformat(),
             'order_batch_id':batch,'manual_recovery':manual,'manual_request_key':'request-1' if manual else None,
             'tasks':[{'task':'taobao_orders','status':'done','artifacts':list(roles),'artifact_roles':roles}]}
    for name,role in roles.items():
        db_session.add(ImportedFile(kind='taobao',original_filename=name,stored_path=name,
            file_hash=hashlib.sha256(name.encode()).hexdigest(),created_at=now.replace(hour=14).astimezone(timezone.utc),
            row_summary={'agent_status':'imported','automation_batch_id':batch,'agent_report_role':role}))
    ai._save_json(db_session,'web_agent_order_receipt_evidence',payload)
    # A subsequent finance run must not erase this order's user initiation.
    ai._save_json(db_session,ai.KEY_ORCH_STATE,{'started_at':now.isoformat(),'tasks':[{'task':'alipay_main','status':'done'}]})
    db_session.commit()
    monkeypatch.setattr(ai,'run_ingest',lambda *a,**k:pytest.fail('must not reimport'))
    monkeypatch.setattr(ai,'orchestrate',lambda *a,**k:pytest.fail('must not reexport'))
    r=ai.finalize_order_pull_after_shipping_password(db_session,now=now,resolved_artifacts=['shipping.xlsx'])
    if manual:
        assert r['completed'] and r['order_batch_id']==batch
        assert set(r['artifacts'])==set(roles)
    else:
        assert r['reason']=='missing_current_order_pull_evidence'


def test_manual_identity_is_persisted_before_external_order_work(db_session,monkeypatch):
    monkeypatch.setattr(web,'ensure_online',lambda *a,**k:{'online':False,'error':'offline'})
    monkeypatch.setattr(ai,'run_ingest',lambda *a,**k:{})
    monkeypatch.setattr(ai.yulibao_service,'refresh_estimated_balance',lambda *a,**k:{})
    r=ai.orchestrate(db_session,orders_only=True,force_orders=True,manual_request_key='trusted-request')
    saved=ai._load_json(db_session,ai.KEY_ORCH_STATE)
    assert r['manual_recovery'] and saved['manual_request_key']=='trusted-request'
    assert saved['order_batch_id']==r['order_batch_id']


def test_scheduler_forwards_explicit_manual_identity(db_session,monkeypatch):
    calls=[]
    monkeypatch.setattr(ai,'is_running',lambda:False)
    monkeypatch.setattr(ai,'orchestrate',lambda db,**kw:calls.append(kw) or {'agent_offline':'offline'})
    monkeypatch.setattr(scheduler,'_record_pipeline_result',lambda db,p,r,**kw:r)
    scheduler._job_web_agent_daily(db_session,manual_request_key='trusted-request')
    assert calls==[{'force_orders':True,'orders_only':True,'manual_request_key':'trusted-request'}]


def test_password_status_updates_only_same_batch(db_session):
    from app.services import feishu_order_push_service as p
    row={'key':'request','order_batch_id':'batch-1','state':'blocked'}
    ai._save_json(db_session,p.ACTIVE_KEY,row);ai._save_json(db_session,'request',row);db_session.commit()
    result={'delivery':{'images_pushed':1,'line_images_pushed':2}}
    p.record_password_closeout(db_session,'other-batch',result)
    assert p._load(db_session,'request')['state']=='blocked'
    p.record_password_closeout(db_session,'batch-1',result)
    assert p._load(db_session,'request')['images_pushed']==3
    assert p._load(db_session,'request')['state']=='completed'
