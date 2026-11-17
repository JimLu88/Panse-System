import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.engine import order_report_identity as identity

STAMP=datetime(2026,11,17,0,47,48)


def text(status):
    return ('报表申请时间： 2026-11-17 00:47:48\n成交时间：2026-08-19 00:47:48 到 2026-11-17 00:47:48\n'
            f'订单状态：{status}\t物流服务：全部\t售后服务：全部\n评价状态：全部\t宝贝名称：\t买家昵称：\n下载发货报表 发送密码')


def rows():
    return [{'export_id':rid,'applied_at':STAMP,'scope':identity.row_scope(text(status))}
            for rid,status in [('4975','全部'),('4974','买家已付款')]]


def test_pair_selects_real_physical_scope_not_largest_id():
    result=identity.select(rows(),triggered_at=STAMP,baseline=['4000'],report='发货报表')
    assert result['export_id']=='4974' and result['scope']['宝贝名称']==''


@pytest.mark.parametrize('fault',['time','range','status','missing','duplicate','other_report','expected_virtual'])
def test_conflicting_pair_fails_closed(fault):
    data=rows();extra={}
    if fault=='time':data[0]['applied_at']+=timedelta(seconds=1)
    if fault=='range':data[0]['scope']['成交时间']='another range'
    if fault=='status':data[0]['scope']['订单状态']='买家已付款'
    if fault=='missing':data[0]['scope'].pop('宝贝名称')
    if fault=='duplicate':data[0]['export_id']='4974'
    if fault=='expected_virtual':extra['expected_id']='4975'
    with pytest.raises(ValueError):
        identity.select(data,triggered_at=STAMP,baseline=['4000'],report='订单报表' if fault=='other_report' else '发货报表',**extra)


@pytest.mark.parametrize('registered', [True,False,'error'])
def test_normal_download_registers_before_password_and_never_reexports(monkeypatch,tmp_path,registered):
    from app.engine import orchestrator as o, shipping_target as target
    events=[]
    class Link:
        @property
        def first(self):return self
        async def get_attribute(self,key):return 'https://example.test/download?export_id=4974'
        async def wait_for(self,**kw):pass
    class Row:
        async def inner_text(self):return text('买家已付款')
        def locator(self,*args):return Link()
    class Rows:
        async def count(self):return 1
        def nth(self,n):return Row()
    class Response:
        status=200
        headers={'content-disposition':'attachment; filename=ExportOrderList4974.xlsx'}
        async def body(self):return b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'+b'x'*120
    class Page:
        async def goto(self,*a,**kw):pass
        async def wait_for_timeout(self,*a):pass
        def locator(self,*a):return Rows()
    async def get(*a,**kw):events.append('download');return Response()
    page=Page();page.context=SimpleNamespace(request=SimpleNamespace(get=get))
    async def login(*a,**kw):return False
    async def password(*a):events.append('password');return {'sent':True}
    def register(path,**kw):
        assert path.exists() and kw['export_id']=='4974'
        events.append('register')
        if registered=='error':raise TimeoutError('registration unknown')
        return {'ok':True,'password_request_allowed':registered}
    monkeypatch.setattr(o,'_handle_taobao_login_wall',login)
    monkeypatch.setattr(o,'_request_shipping_password_delivery',password)
    monkeypatch.setattr(target,'register',register)
    call=o._download_fresh_report(page,report='发货报表',triggered_at=STAMP,download_dir=tmp_path,
                                  wait_s=1,variables={'_report_baseline_ids':['4000']})
    if registered=='error':
        with pytest.raises(o.FreshReportUnavailable,match='file_retained'):asyncio.run(call)
    else: assert len(asyncio.run(call))==1
    assert events==(['download','register','password'] if registered is True else ['download','register'])
    assert (tmp_path/'ExportOrderList4974.xlsx').exists()
