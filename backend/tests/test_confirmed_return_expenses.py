from datetime import date
from decimal import Decimal as D
from sqlalchemy import select
from app.models.order import Order
from app.models.marketing import AfterSales
from app.services import order_financials as f
from app.api.reports import per_order_reconcile

def make_return(db):
    o=Order(order_no='return-expense-test',platform='淘宝',status='aftersales',order_date=date(2026,2,11),paid_amount=D('2674.41'),refund_amount=D('2674.41'),shop_received_amount=D('2604.88'),tax=D('0'),actual_cost=D('1300'),actual_packing=D('260'),actual_logistics=D('315'),actual_install=D('66'))
    db.add(o)
    db.add(AfterSales(platform_order_no=o.order_no,second_inbound_confirmed='已确认',second_visit_fee=D('198'),return_pack_freight=D('309.10'),wanshifu_deduction=D('198')))
    db.commit()
    return o

def test_return_report_and_summary(db_session):
    db=db_session; o=make_return(db)
    b=f.cost_breakdown(o,f.load_coefficients(db),db=db)
    assert b['physical']==D('260')
    assert b['freight']==D('315') and b['install_upstairs']==D('66')
    assert b['aftersales']==D('705.10') and b['total']==D('1346.10')
    row=next(r for r in per_order_reconcile(year=2026,month=2,db=db,product_search=None)['rows'] if r['order_no']==o.order_no)
    assert row['revenue']==0 and row['net_profit']==-1346.10
    summary=f.accounting_summary(db,date(2026,2,1),date(2026,2,28))
    assert summary['order_cost']==D('1346.10') and summary['aftersales']==D('705.10')
    assert summary['count']==0 and summary['returned_count']==1

def test_unconfirmed_return_not_written_off(db_session):
    db=db_session; o=make_return(db)
    a=db.execute(select(AfterSales)).scalar_one()
    a.second_inbound_confirmed=None
    db.commit()
    assert not f.is_returned_refund(o,db)

def test_partial_refund_not_returned_branch(db_session):
    db=db_session; o=make_return(db)
    o.refund_amount=D('100'); db.commit()
    assert not f.is_returned_refund(o,db)
