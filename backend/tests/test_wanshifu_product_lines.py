import pytest
import openpyxl
from app.models.finance import WanshifuOrder
from app.services import wanshifu_order_service as service

def workbook(conflict=None):
    wb=openpyxl.Workbook();ws=wb.active
    ws.append(['订单信息'])
    ws.append(['订单编号','商品序号','商品类别','商品型号','客户姓名','下单时间','订单总净额','订单服务费','订单状态'])
    ws.append([])
    ws.append(['W100',1,'床类','JYC02','测试','2026-09-26 10:00:00',320,240,'交易成功'])
    ws.append(['W100',2,'桌类',None,None,None,conflict,None,'交易成功'])
    return wb

def test_all_product_lines_and_one_total(db_session):
    report=service.import_workbook(db_session,workbook())
    assert not report.errors and report.parsed==2 and report.inserted==1
    row=db_session.query(WanshifuOrder).one()
    assert row.product_category=='床类 / 桌类'
    assert row.product_model=='JYC02 / （未填）'
    assert row.net_amount==320 and row.service_fee==240
    assert '序号=1' in row.remark and '序号=2' in row.remark
    assert service.import_workbook(db_session,workbook()).updated==0

def test_conflicting_order_money_no_partial_write(db_session):
    report=service.import_workbook(db_session,workbook(conflict=999))
    assert report.errors and report.inserted==0
    assert db_session.query(WanshifuOrder).count()==0

def test_repeated_total_not_summed(db_session):
    report=service.import_workbook(db_session,workbook(conflict=320))
    assert not report.errors
    assert db_session.query(WanshifuOrder).one().net_amount==320

@pytest.mark.parametrize('field,value',[('customer_name','另一个人'),('status','交易关闭'),('service_fee',300),('created_time','2026-09-27 10:00:00')])
def test_order_identity_conflicts_fail_closed(field,value):
    first={'wsf_order_no':'W1',field:'2026-09-26 10:00:00' if field=='created_time' else ('甲' if field=='customer_name' else ('交易成功' if field=='status' else 20))}
    _,errors=service._coalesce_order_records([first,{'wsf_order_no':'W1',field:value}])
    assert errors

def test_exact_duplicate_not_extra_product():
    rec={'wsf_order_no':'W1','product_sequence':1,'product_category':'床','net_amount':'320'}
    rows,errors=service._coalesce_order_records([rec,rec.copy()])
    assert not errors and len(rows[0]['_product_lines'])==1

def test_too_long_never_truncated():
    _,errors=service._coalesce_order_records([{'wsf_order_no':'W1','product_model':'长'*256}])
    assert errors

def test_importer_propagates_errors(db_session):
    from io import BytesIO
    from pathlib import Path
    from app.services.agent_ingest_service import _import_one
    out=BytesIO();workbook(conflict=999).save(out)
    _,status,summary=_import_one(db_session,'wanshifu',Path('sample.xlsx'),out.getvalue())
    assert status=='error' and summary['errors']
