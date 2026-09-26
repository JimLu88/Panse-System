def test_import_preserves_old_reference_and_manual_note(db_session):
    from app.models.finance import WanshifuOrder
    from app.services import wanshifu_order_service as service
    import openpyxl
    wb=openpyxl.Workbook();ws=wb.active
    ws.append(['订单信息'])
    ws.append(['订单编号','商品序号','商品类别','商品型号','客户姓名','下单时间','订单总净额','订单服务费','订单状态'])
    ws.append([])
    ws.append(['W100',1,'床类','JYC02','测试','2026-09-26 10:00:00',320,240,'交易成功'])
    ws.append(['W100',2,'桌类',None,None,None,None,None,'交易成功'])
    row=WanshifuOrder(wsf_order_no='W100',remark='淘宝单号:1234567890123456789; 人工备注保留')
    db_session.add(row);db_session.flush()
    assert not service.import_workbook(db_session,wb).errors
    assert '人工备注保留' in row.remark and '1234567890123456789' in row.remark
    original=row.remark
    assert service.import_workbook(db_session,wb).updated==0
    assert row.remark==original
