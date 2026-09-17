"""作废图必须显著标记，文本与图片一起送达，未知不重发。"""
from types import SimpleNamespace

import pytest

from app.services import order_sheet_archive_service as sheets
from app.services import settings_service
from app.models.import_file import ImportedFile
from tests.test_order_line_factory_delivery_0812 import _order, _line


def test_void_overlay_uses_legacy_renderer_supported_coordinates(monkeypatch):
    captured = {}
    monkeypatch.setattr(sheets, "render_html", lambda _: '<html><body>sheet</body></html>')
    def render(html, *, width):
        captured.update(html=html, width=width)
        return b'jpeg'
    monkeypatch.setattr(sheets, '_html_to_png', render)
    assert sheets.render_void_png(None) == b'jpeg'
    assert captured['width'] == 1684
    assert 'inset:' not in captured['html']
    assert 'top:0;left:0;' in captured['html']
    assert 'font-size:230px' in captured['html']
    assert '>作废</div>' in captured['html']
    assert '禁止发货' in captured['html']


@pytest.mark.parametrize('mode', ['success', 'timeout', 'missing_receipt'])
def test_void_notice_atomic_card_and_no_replay(db_session, monkeypatch, tmp_path, mode):
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY', raising=False)
    monkeypatch.setenv('DELIVERY_STORAGE_ROOT', str(tmp_path))
    settings_service.set_value(db_session, 'feishu_push_chat_id', 'factory-test')
    order = _order(db_session, 'VOID-MAIN')
    line = _line(db_session, order.order_no, 'VOID-CHILD', '床', 'PPS2633007032018', refunded=True)
    line.factory_no = 418
    line.factory_delivery_required = True
    db_session.add(ImportedFile(kind='order_sheet_sent', original_filename='sent.jpg',
        stored_path='/test/sent.jpg', row_summary={'sub_order_no':line.sub_order_no, 'pushed':True}))
    db_session.commit()
    monkeypatch.setattr(sheets.factory_sheet, 'build_for_order_line', lambda *a: SimpleNamespace())
    monkeypatch.setattr(sheets, 'render_void_png', lambda _: b'VOID IMAGE')
    monkeypatch.setattr('app.services.feishu_client.upload_image', lambda *a: 'img-test')
    cards = []
    def send(db, chat, card):
        pending = db.query(ImportedFile).filter_by(kind='order_sheet_void').one()
        assert pending.row_summary['delivery_state'] == 'unknown'
        cards.append(card)
        if mode == 'timeout':
            raise TimeoutError('uncertain send')
        return {'message_id': 'test-receipt'} if mode == 'success' else {}
    monkeypatch.setattr('app.services.feishu_client.send_card', send)
    result = sheets.reconcile_refunded_order_lines(db_session)
    assert result['voided'] == (1 if mode == 'success' else 0)
    assert result['failed'] == (0 if mode == 'success' else 1)
    assert len(cards) == 1
    assert '畔色418单' in cards[0]['header']['title']['content']
    assert '已作废' in cards[0]['elements'][0]['text']['content']
    assert '不要发货' in cards[0]['elements'][0]['text']['content']
    assert cards[0]['elements'][1]['img_key'] == 'img-test'
    again = sheets.reconcile_refunded_order_lines(db_session)
    assert len(cards) == 1
    assert again['failed'] == (0 if mode == 'success' else 1)
    rec = db_session.query(ImportedFile).filter_by(kind='order_sheet_void').one()
    assert rec.row_summary['pushed'] == (mode == 'success')
    assert line.factory_no == 418
