from tests.test_order_line_factory_delivery_0812 import _order,_line
from app.models.import_file import ImportedFile
from app.services import order_sheet_archive_service as sheets


def test_pending_child_never_allocates_parent_or_deletes_archive(db_session,monkeypatch):
    order=_order(db_session,'parent');line=_line(db_session,'parent','child','床','PPS2633007032018');line.factory_delivery_required=True;db_session.commit()
    calls=[]
    monkeypatch.setattr(sheets,'_next_factory_no',lambda *a:(_ for _ in ()).throw(AssertionError('parent allocation')))
    monkeypatch.setattr(sheets,'generate_for_order',lambda *a:(_ for _ in ()).throw(AssertionError('parent render')))
    monkeypatch.setattr(sheets,'reconcile_order_line_delivery',lambda db,**kw:calls.append(kw) or {'pushed':1,'failed':[]})
    r=sheets.repush_to_factory(db_session,'parent')
    assert r['ok'] and r['delivery_scope']=='child' and order.factory_no is None
    assert calls==[{'limit':1,'only_sub_order_nos':{'child'}}]


def test_delivered_child_does_not_resend(db_session,monkeypatch):
    order=_order(db_session,'parent');line=_line(db_session,'parent','child','床','PPS2633007032018');line.factory_delivery_required=True;line.factory_no=840;line.factory_delivery_message_id='verified-test-message'
    rec=ImportedFile(kind='order_sheet_sent',original_filename='child.jpg',stored_path='/tmp/child.jpg',source='test',row_summary={'sub_order_no':'child','pushed':True})
    db_session.add(rec);db_session.commit()
    monkeypatch.setattr(sheets,'reconcile_order_line_delivery',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('duplicate send')))
    r=sheets.repush_to_factory(db_session,'parent')
    assert r['ok'] and r['pushed']==0 and r['skipped']=='child_orders_already_delivered'
    assert line.factory_no==840 and order.factory_no is None and db_session.get(ImportedFile,rec.id)
