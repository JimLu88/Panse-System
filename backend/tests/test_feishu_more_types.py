"""飞书新增类型: 采购单/工厂对账 截图入库 + Excel 文件消息(订单/工厂对账)。"""
import json
from decimal import Decimal
from io import BytesIO

import openpyxl

from app.models.finance import FactoryReconciliation
from app.models.factory_recon_item import FactoryReconItem
from app.models.import_file import ImportedFile
from app.models.order import PartPurchase
from app.services import feishu_bot_service as fb, screenshot_ingest_service as sis, feishu_client


# ---- 截图: 采购单 / 工厂对账 独立成类 ----
def test_purchase_screenshot_inserts_part_purchase(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(fb.vision_ocr_service, "parse_purchase_invoice", lambda db, img, **k: {
        "purchase": {"supplier_name": "博冠五金", "purchase_no": "PO-1",
                     "lines": [{"material_name": "合页", "qty": 10, "unit_price": 2.5},
                               {"material_name": "螺丝", "qty": 100, "unit_price": 0.1}]}})
    r = fb._dispatch_import(db, "purchase", b"img")
    db.flush()
    assert r["ok"] is True
    pps = db.query(PartPurchase).all()
    assert len(pps) == 2
    assert pps[0].supplier == "博冠五金"


def test_purchase_no_lines_is_rejected(db_session, monkeypatch):
    db = db_session
    # OCR 没解析出明细行 → 多半不是采购单, 不硬塞
    monkeypatch.setattr(fb.vision_ocr_service, "parse_purchase_invoice",
                        lambda db, img, **k: {"purchase": {"supplier_name": None, "lines": []}})
    r = fb._dispatch_import(db, "purchase", b"img")
    assert r["ok"] is False
    assert "不是采购单" in r["summary"]


def test_purchase_higher_confidence_threshold():
    assert fb._threshold("purchase") > fb._threshold("order_table")


def test_factory_recon_screenshot_inserts_reconciliation(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(fb.vision_ocr_service, "parse_factory_reconciliation", lambda db, img, **k: {
        "rows": [{"factory_name": "博冠家具", "bill_amount": 1000, "paid_amount": 600}]})
    r = fb._dispatch_import(db, "factory_recon", b"img")
    db.flush()
    assert r["ok"] is True
    rec = db.query(FactoryReconciliation).filter_by(factory_name="博冠家具").one()
    assert rec.diff_amount == Decimal("400")   # 1000 - 600


def test_purchase_parsed_skips_duplicate_purchase_no(db_session):
    db = db_session
    parsed = {"purchase": {"purchase_no": "PO-DUP", "lines": [{"material_name": "板", "qty": 1}]}}
    sis.commit_purchase_parsed(db, parsed)
    db.flush()
    r2 = sis.commit_purchase_parsed(db, parsed)   # 同 purchase_no 再来一次
    assert r2["inserted"] == 0 and r2["skipped"] == 1


def test_factory_recon_parsed_dedup_on_resend(db_session):
    db = db_session
    parsed = {"rows": [{"factory_name": "博冠家具", "period_end": "2026-01-31",
                        "bill_amount": 1000, "paid_amount": 600}]}
    r1 = sis.commit_factory_recon_parsed(db, parsed)
    db.flush()
    assert r1["inserted"] == 1
    r2 = sis.commit_factory_recon_parsed(db, parsed)   # 重复发图 → 不再翻倍
    db.flush()
    assert r2["inserted"] == 0 and r2["skipped"] == 1
    assert db.query(FactoryReconciliation).filter_by(factory_name="博冠家具").count() == 1


# ---- Excel 文件消息 ----
def test_text_message_replies_help_guide(db_session, monkeypatch):
    db = db_session
    replies = []
    monkeypatch.setattr(feishu_client, "reply_card", lambda db, mid, card: replies.append((mid, card)))
    event = {"message": {"message_type": "text", "message_id": "t1",
                         "content": json.dumps({"text": "@机器人 你好"})}}
    out = fb.on_message_event(db, event)
    assert out["kind"] == "help"
    assert replies and replies[0][0] == "t1"
    assert "使用指南" in replies[0][1]["header"]["title"]["content"]
    body = replies[0][1]["elements"][0]["text"]["content"]
    assert "图片" in body and "表格" in body   # 指南列了发图/发表格两类


def test_classify_table_by_filename():
    from app.services import table_ingest_service as tis
    assert tis.classify_table("2026工厂对账单.xlsx", b"") == "factory_recon"
    assert tis.classify_table("千牛订单导出.xlsx", b"") == "order"
    assert tis.classify_table("万师傅5月账单.xlsx", b"") == "wanshifu"
    assert tis.classify_table("物流月结.xlsx", b"") == "logistics"
    assert tis.classify_table("乱七八糟.xlsx", b"") is None


def test_file_message_archives_and_picks(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(feishu_client, "download_message_resource", lambda *a, **k: b"XLSXBYTES")
    monkeypatch.setattr(feishu_client, "reply_card", lambda *a, **k: None)
    event = {"message": {"message_type": "file", "message_id": "f1",
                         "content": json.dumps({"file_key": "k", "file_name": "6月工厂对账单.xlsx"})}}
    out = fb.on_message_event(db, event)
    db.flush()
    assert out["file_kind"] == "factory_recon"
    # 原文件已兜底归档(kind=factory_recon, source=feishu)
    assert db.query(ImportedFile).filter_by(kind="factory_recon", source="feishu").count() == 1
    pend = fb._load_pending(db).get("f1")
    assert pend["is_file"] is True and pend["archived_path"]


def test_file_message_download_failure_asks_resend(db_session, monkeypatch):
    db = db_session
    replies = []
    def _boom(*a, **k):
        raise RuntimeError("feishu down")
    monkeypatch.setattr(feishu_client, "download_message_resource", _boom)
    monkeypatch.setattr(feishu_client, "reply_card", lambda db, mid, card: replies.append(card))
    event = {"message": {"message_type": "file", "message_id": "f3",
                         "content": json.dumps({"file_key": "k", "file_name": "工厂对账.xlsx"})}}
    out = fb.on_message_event(db, event)
    # 取不到原件 → 不给确认卡, 直接让重发
    assert out["error"] == "download_failed"
    assert "文件获取失败" in replies[0]["header"]["title"]["content"]
    assert fb._load_pending(db).get("f3") is None   # 未暂存


def test_file_message_non_table_rejected(db_session, monkeypatch):
    db = db_session
    replies = []
    monkeypatch.setattr(feishu_client, "reply_card", lambda db, mid, card: replies.append(card))
    event = {"message": {"message_type": "file", "message_id": "f2",
                         "content": json.dumps({"file_key": "k", "file_name": "猫.pdf"})}}
    out = fb.on_message_event(db, event)
    assert out == {"ignored": True, "file_name": "猫.pdf"}
    assert "暂不支持" in replies[0]["header"]["title"]["content"]


def _factory_recon_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "26年1月"
    ws.append(["畔色 月度生产明细表"])
    ws.append(["单号", "订单号", "追加订单号1", "备注", "详情", "数量", "价格",
               "客户信息", "下单时间", "发货时间"])
    ws.append([1, "ORDX1", "", "", "胡桃木案台", 1, 1450, "上海 李四", 46080, 46109])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def test_dispatch_file_factory_recon_xlsx_imports(db_session):
    db = db_session
    r = fb._dispatch_file(db, "factory_recon", _factory_recon_xlsx(), "工厂对账.xlsx")
    db.flush()
    assert r["ok"] is True
    assert db.query(FactoryReconItem).filter_by(order_no="ORDX1").count() == 1
