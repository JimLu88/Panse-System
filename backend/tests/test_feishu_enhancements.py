"""飞书机器人增强: 支付宝流水截图入库 / 送货单追问供应商并入库 / 选类型卡含支付宝。"""
from datetime import date
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.supplier import DeliveryNote, Supplier
from app.services import delivery_note_service, feishu_bot_service as fb, ocr_service


def test_picker_card_includes_alipay_and_supplier_types():
    card = fb._picker_card("m1")
    labels = [a["text"]["content"] for a in card["elements"][1]["actions"]]
    assert "支付宝流水" in labels
    assert "供应商送货单" in labels
    assert "alipay_flow" in fb.IMAGE_TYPES


def test_supplier_picker_card_lists_suppliers_and_cancel():
    card = fb._supplier_picker_card("m1", [(1, "甲工厂"), (2, "乙板材")])
    actions = card["elements"][1]["actions"]
    assert len(actions) == 3  # 2 供应商 + 取消
    assert actions[0]["value"] == {"op": "pick_supplier", "message_id": "m1", "supplier_id": 1}
    # 无供应商 → 提示先建供应商
    empty = fb._supplier_picker_card("m1", [])
    assert "请先建供应商" in empty["header"]["title"]["content"]


def test_dispatch_alipay_flow_screenshot_imports(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(fb.vision_ocr_service, "parse_alipay_flow_screenshot", lambda db, img, **kw: {
        "flows": [
            {"transaction_no": "AF1", "amount": "-1200.50", "transaction_type": "采购付款",
             "transaction_time": "2026-05-02 10:00:00", "counterparty": "博冠家具"},
            {"transaction_no": "AF2", "amount": "300", "transaction_type": "退款"},
        ],
        "ocr_warnings": ["截图可能被截断"],
    })
    result = fb._dispatch_import(db, "alipay_flow", b"img")
    db.flush()
    assert result["ok"] is True
    flows = db.query(AlipayFlow).filter_by(account=fb._ALIPAY_SCREENSHOT_ACCOUNT).all()
    assert len(flows) == 2
    assert {f.transaction_no for f in flows} == {"AF1", "AF2"}
    # 提示里包含"建议用 CSV"的长账单告警
    assert "CSV" in result["summary"]


def test_dispatch_supplier_note_requires_supplier(db_session):
    db = db_session
    r = fb._dispatch_import(db, "supplier_note", b"img")   # 无 supplier_id
    assert r["ok"] is False
    assert "供应商" in r["summary"]


def test_dispatch_supplier_note_persists_to_chosen_supplier(db_session, monkeypatch):
    db = db_session
    sup = Supplier(name="博冠家具", supplier_type="wood")
    db.add(sup)
    db.flush()

    # 避免真实落盘与真实 OCR
    monkeypatch.setattr(delivery_note_service.delivery_storage, "save_upload",
                        lambda sid, *, content, original_name, on_date=None: {
                            "year": 2026, "month": 5, "file_path": "/tmp/x.jpg",
                            "original_name": original_name, "mime_type": "image/jpeg",
                            "size_bytes": len(content)})
    parsed = ocr_service.ParsedDeliveryNote(
        note_no="SN-001", delivery_date=date(2026, 5, 2), total_amount=Decimal("4750"),
        lines=[ocr_service.ParsedDeliveryLine(
            line_no=1, item_name="樱桃木窄柜", spec="100", unit="件",
            qty=Decimal("1"), unit_price=Decimal("3300"), amount=Decimal("3300"),
            raw_text="樱桃木窄柜100", warnings=[])],
        warnings=[], model="test", raw_response="{}", confidence=Decimal("95"))
    monkeypatch.setattr(ocr_service, "ocr_delivery_note", lambda *a, **k: parsed)

    r = fb._dispatch_import(db, "supplier_note", b"img", supplier_id=sup.id)
    db.flush()
    assert r["ok"] is True
    assert "博冠家具" in r["summary"]
    note = db.query(DeliveryNote).filter_by(supplier_id=sup.id).one()
    assert note.note_no == "SN-001"
    assert note.total_amount == Decimal("4750")
