import json
from datetime import date
from hashlib import sha256
import pytest
from app.models.order import Order,OrderDetail
from app.models.settings import SystemSetting
from app.models.import_file import ImportedFile
from app.services import factory_production_evidence as facts
from app.services import factory_sheet,order_sheet_archive_service as sheets,order_line_delivery_service as delivery


def setup(db):
    o=Order(platform='淘宝',order_no='MAIN',order_date=date(2026,9,20),status='paid',qty=2,
            sku='尺寸定制',sku_code='CUSTOM',product_code='TABLE',remark='150*75尺寸')
    line=OrderDetail(order_no='MAIN',sub_order_no='CHILD',sync_key='line:CHILD',source='import',
                     sku_code='CUSTOM',product_code='TABLE',sku_name='尺寸定制',qty=2,
                     factory_no=513,factory_delivery_state='failed')
    db.add_all([o,line]);db.commit();return o,line


def confirm(db,line,qty=2):
    return facts.confirm_quantity(db,line_id=line.id,expected_sku='CUSTOM',expected_purchase_qty=2,
                                  physical_qty=qty,actor='user',evidence_ref='explicit-test-confirmation')


@pytest.mark.parametrize('fault',['ok','hash','product','path','dimensions','missing'])
def test_dimensions_require_exact_reviewed_file(tmp_path,monkeypatch,fault):
    from app.services import gallery_lookup
    raw=b'exact inspected sku drawing';(tmp_path/'SKU.jpg').write_bytes(raw)
    record={'product_code':'CAB','path':'SKU.jpg','sha256':sha256(raw).hexdigest(),'dimensions_mm':[720,380,360]}
    if fault=='hash':record['sha256']='wrong'
    if fault=='product':record['product_code']='OTHER'
    if fault=='path':record['path']='../outside.jpg'
    if fault=='dimensions':record['dimensions_mm']=[720,0,360]
    if fault=='missing':record['path']='missing.jpg'
    registry=tmp_path/'facts.json';registry.write_text(json.dumps({'SKU':record}))
    monkeypatch.setattr(facts,'DIMENSION_REGISTRY',registry);monkeypatch.setattr(gallery_lookup,'_root',lambda:tmp_path)
    assert facts.verified_dimensions('CAB','SKU') == ('长度：720mm；深度：380mm；高度：360mm' if fault=='ok' else None)
    assert facts.verified_dimensions('CAB','ANOTHER') is None


@pytest.mark.parametrize('qty',[1,2])
def test_confirmation_only_changes_production_not_purchase(db_session,monkeypatch,qty):
    o,line=setup(db_session);rec=confirm(db_session,line,qty)
    assert confirm(db_session,line,qty)==rec
    sheet=factory_sheet.build_for_order_line(db_session,o.id,line.id)
    assert sheet.qty==qty and sheet.purchase_qty==2
    assert line.qty==2 and o.qty==2
    assert not any(w.code=='production_quantity_unverified' for w in sheet.warnings)
    monkeypatch.setattr(sheets,'_gallery_data_uri',lambda *a:None)
    assert f'已确认成品 {qty} 件（原拍下数量 2）' in sheets.render_html(sheet)
    monkeypatch.setattr(sheets,'_html_to_png',lambda *a,**k:b'approved image')
    assert sheets.render_png(sheet)==b'approved image'


@pytest.mark.parametrize('field,value',[('qty',3),('sku_code','OTHER')])
def test_changed_line_invalidates_confirmation(db_session,field,value):
    o,line=setup(db_session);confirm(db_session,line)
    setattr(line,field,value);db_session.flush()
    assert facts.quantity_confirmation(db_session,o,line) is None


def test_notes_change_invalidates_confirmation(db_session):
    o,line=setup(db_session);confirm(db_session,line)
    o.remark='改为其他尺寸';assert facts.quantity_confirmation(db_session,o,line) is None


def test_unconfirmed_custom_still_blocks(db_session,monkeypatch):
    o,line=setup(db_session)
    sheet=factory_sheet.build_for_order_line(db_session,o.id,line.id)
    monkeypatch.setattr(sheets,'_html_to_png',lambda *a,**k:pytest.fail('must not render'))
    with pytest.raises(ValueError,match='实物数量'):sheets.render_png(sheet)


@pytest.mark.parametrize('state',['sending_image','uncertain','sent'])
def test_confirmation_cannot_release_unknown_or_sent(db_session,state):
    o,line=setup(db_session);line.factory_delivery_state=state;db_session.commit()
    with pytest.raises(ValueError):confirm(db_session,line)


def test_different_confirmation_never_overwrites(db_session):
    o,line=setup(db_session);confirm(db_session,line,1)
    with pytest.raises(ValueError):confirm(db_session,line,2)


def test_snapshot_distinguishes_physical_and_purchase_qty(db_session,monkeypatch):
    o,line=setup(db_session);confirm(db_session,line,1)
    sheet=factory_sheet.build_for_order_line(db_session,o.id,line.id)
    captured={}
    def archive(db,**kwargs):
        captured.update(kwargs)
        return type('Result',(),{'file':None})()
    monkeypatch.setattr(sheets.import_storage,'archive',archive)
    sheets.archive_sent_line_snapshot(db_session,o,line,b'image',rendered_sheet=sheet)
    evidence=ImportedFile(id=1,kind='order_sheet_sent',stored_path='unused',file_hash=sha256(b'image').hexdigest(),row_summary=captured['row_summary'])
    assert delivery.audit_sent_line_content([line],{'CHILD':evidence})['mismatches']==[]
    evidence.row_summary['rendered_line']['quantity_confirmation']['physical_qty']=99
    assert delivery.audit_sent_line_content([line],{'CHILD':evidence})['mismatches']


def test_reviewed_dimensions_resolve_guard_not_other_sku(db_session,monkeypatch):
    from app.models.product import Product
    from app.models.pricing import PricingSku
    db_session.add(Product(code='CAB',name='柜',size_detail='wrong upper cabinet'))
    db_session.add_all([PricingSku(product_code='CAB',sku_code='A'),PricingSku(product_code='CAB',sku_code='B')])
    o,line=setup(db_session);line.product_code='CAB';line.sku_code='A';line.sku_name='横竖隔板';line.qty=1
    db_session.commit()
    monkeypatch.setattr(facts,'verified_dimensions',lambda product,sku:'长度：720mm；深度：380mm；高度：360mm' if (product,sku)==('CAB','A') else None)
    sheet=factory_sheet.build_for_order_line(db_session,o.id,line.id)
    assert sheet.size_info.startswith('长度：720')
    assert not any(w.code=='variant_size_unverified' for w in sheet.warnings)
    line.sku_code='B';db_session.commit()
    assert any(w.code=='variant_size_unverified' for w in factory_sheet.build_for_order_line(db_session,o.id,line.id).warnings)
