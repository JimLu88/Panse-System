"""Excel importer: preview / AI 推断 / commit (delivery_note + factory_order)."""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from openpyxl import Workbook

from app.models.order import FactoryOrder
from app.models.supplier import DeliveryNote, DeliveryNoteLine, Supplier
from app.services import excel_importer, settings_service
from app.services.ai_provider import AiResponse


def _xlsx(sheet_name: str, header: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _multi_sheet_xlsx(sheets: dict[str, tuple[list[str], list[list]]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, (header, rows) in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------- preview --------------------------- #


def test_preview_single_sheet():
    data = _xlsx("Sheet1", ["供应商", "单号", "日期", "品名", "数量", "金额"], [
        ["木作工厂", "N1", "2026-05-14", "电视柜", 1, 580],
        ["木作工厂", "N1", "2026-05-14", "斗柜", 1, 320],
        ["岩板厂", "N2", "2026-05-15", "台面板", 2, 1160],
    ])
    previews = excel_importer.preview_excel(data)
    assert len(previews) == 1
    p = previews[0]
    assert p.sheet_name == "Sheet1"
    assert p.row_count == 3
    assert p.column_names == ["供应商", "单号", "日期", "品名", "数量", "金额"]
    assert len(p.sample_rows) == 3
    assert p.sample_rows[0][0] == "木作工厂"


def test_preview_multi_sheet():
    data = _multi_sheet_xlsx({
        "5月": (["供应商", "数量"], [["A", 1]]),
        "6月": (["供应商", "数量"], [["B", 2], ["C", 3]]),
        "空sheet": (["col1"], []),
    })
    previews = excel_importer.preview_excel(data)
    assert len(previews) == 3
    assert {p.sheet_name for p in previews} == {"5月", "6月", "空sheet"}
    by_name = {p.sheet_name: p for p in previews}
    assert by_name["5月"].row_count == 1
    assert by_name["6月"].row_count == 2
    assert by_name["空sheet"].row_count == 0


def test_preview_invalid_excel_raises():
    with pytest.raises(excel_importer.ImporterError):
        excel_importer.preview_excel(b"not an xlsx")


def test_preview_sample_rows_truncated_to_5():
    rows = [[f"r{i}", i] for i in range(20)]
    data = _xlsx("S", ["a", "b"], rows)
    p = excel_importer.preview_excel(data)[0]
    assert p.row_count == 20
    assert len(p.sample_rows) == 5


def test_preview_handles_date_value():
    data = _xlsx("S", ["日期"], [[date(2026, 5, 14)]])
    p = excel_importer.preview_excel(data)[0]
    assert "2026-05-14" in str(p.sample_rows[0][0])


def _xlsx_with_title_banner(sheet_name, title, header, rows):
    """第一行是合并单元格大标题, 第二行才是真表头 (复现业务总表结构)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append([title] + [None] * (len(header) - 1))
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(header))
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_preview_skips_merged_title_banner():
    """合并标题行不应被当成表头 (否则真列名错位, 每行报缺主键被跳过)."""
    data = _xlsx_with_title_banner(
        "1-产品总表", "1-产品总表",
        ["产品编码", "类目", "产品名称"],
        [["PPS001", "床", "榉木床"], ["PPS002", "柜", "斗柜"]],
    )
    p = excel_importer.preview_excel(data)[0]
    assert p.column_names == ["产品编码", "类目", "产品名称"]
    assert p.row_count == 2
    assert p.sample_rows[0][0] == "PPS001"
    assert any("识别为表头" in n for n in p.notes)


def test_commit_skips_merged_title_banner(db_session):
    """带合并标题的产品总表能正确入库, 不再误判'缺产品编码'."""
    from app.models.product import Product
    data = _xlsx_with_title_banner(
        "1-产品总表", "1-产品总表",
        ["产品编码", "类目", "产品名称"],
        [["PPS001", "床", "榉木床"], ["PPS002", "柜", "斗柜"]],
    )
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="1-产品总表",
        entity_type="product",
        mapping={"code": "产品编码", "category": "类目", "name": "产品名称"},
    )
    db_session.commit()
    assert report.inserted_parents == 2
    assert report.skipped_rows == 0
    codes = {p.code for p in db_session.query(Product).all()}
    assert {"PPS001", "PPS002"} <= codes


def test_account_balance_missing_date_defaults_to_current(db_session):
    """账户余额行有账户名但缺统计日期 → 用当前年月兜底入账, 不丢行。"""
    from app.models.finance import AccountBalance
    data = _xlsx("10", ["账户名称", "账户号", "统计日期", "期初余额", "期末余额"], [
        ["银行卡-个体户私人", None, None, 30000, 30000],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="10",
        entity_type="account_balance",
        mapping={"account_name": "账户名称", "account_no": "账户号",
                 "period_date": "统计日期", "opening_balance": "期初余额",
                 "closing_balance": "期末余额"},
    )
    db_session.commit()
    assert report.inserted_parents == 1
    assert any("缺统计日期" in w for w in report.warnings)
    bal = db_session.query(AccountBalance).filter_by(account_name="银行卡-个体户私人").one()
    assert bal.period_year and bal.period_month  # 已填充, 非空


# ----------------------------- AI 推断 --------------------------- #


def test_infer_mapping_no_ai_config_skips_silently(db_session):
    p = excel_importer.SheetPreview(
        sheet_name="S", row_count=1,
        column_names=["供应商", "数量"], sample_rows=[["A", 1]],
    )
    result = excel_importer.infer_mapping(db_session, preview=p)
    assert "AI 未配置" in (result.notes[0] if result.notes else "")
    assert result.suggested_mapping == {}


def test_infer_mapping_with_mocked_ai(db_session):
    settings_service.set_value(db_session, "ai_diagnose_provider", "anthropic")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "k")
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-x")

    p = excel_importer.SheetPreview(
        sheet_name="5月对账",
        row_count=2,
        column_names=["供应商", "单号", "送货日期", "品名", "数量", "金额"],
        sample_rows=[
            ["木作工厂", "N1", "2026-05-14", "电视柜", 1, 580],
            ["木作工厂", "N1", "2026-05-14", "斗柜", 1, 320],
        ],
    )
    fake = MagicMock()
    fake.chat.return_value = AiResponse(
        text='''{
            "entity_type": "delivery_note",
            "mapping": {
                "supplier_name": "供应商",
                "note_no": "单号",
                "delivery_date": "送货日期",
                "item_name": "品名",
                "qty": "数量",
                "amount": "金额"
            },
            "skipped_columns": [],
            "warnings": []
        }''',
        model="claude-x",
    )
    with patch.object(excel_importer, "build_provider", return_value=fake):
        result = excel_importer.infer_mapping(db_session, preview=p)

    assert result.suggested_entity == "delivery_note"
    assert result.suggested_mapping["supplier_name"] == "供应商"
    assert result.suggested_mapping["delivery_date"] == "送货日期"
    assert result.suggested_mapping["qty"] == "数量"


def test_infer_mapping_handles_ai_invalid_json(db_session):
    settings_service.set_value(db_session, "ai_diagnose_provider", "anthropic")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "k")
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-x")

    p = excel_importer.SheetPreview(
        sheet_name="S", row_count=1, column_names=["x"], sample_rows=[["v"]],
    )
    fake = MagicMock()
    fake.chat.return_value = AiResponse(text="完全不是 JSON", model="claude-x")
    with patch.object(excel_importer, "build_provider", return_value=fake):
        result = excel_importer.infer_mapping(db_session, preview=p)
    assert any("无法解析" in n for n in result.notes)
    assert result.suggested_mapping == {}


def test_infer_mapping_filters_invalid_columns(db_session):
    """AI 给的 mapping 引用了 Excel 里不存在的列 → 应被过滤掉."""
    settings_service.set_value(db_session, "ai_diagnose_provider", "anthropic")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "k")
    settings_service.set_value(db_session, "ai_diagnose_model", "claude-x")

    p = excel_importer.SheetPreview(
        sheet_name="S", row_count=1,
        column_names=["供应商", "数量"], sample_rows=[["A", 1]],
    )
    fake = MagicMock()
    fake.chat.return_value = AiResponse(text='''{
        "entity_type": "delivery_note",
        "mapping": {
            "supplier_name": "供应商",
            "delivery_date": "不存在的日期列",
            "qty": "数量"
        }
    }''', model="claude-x")
    with patch.object(excel_importer, "build_provider", return_value=fake):
        result = excel_importer.infer_mapping(db_session, preview=p)
    assert "supplier_name" in result.suggested_mapping
    assert "qty" in result.suggested_mapping
    assert "delivery_date" not in result.suggested_mapping


# ----------------------------- commit delivery_note -------------- #


def test_commit_delivery_note_basic(db_session):
    data = _xlsx("S", ["供应商", "单号", "日期", "品名", "数量", "单价", "金额"], [
        ["木作工厂", "N1", "2026-05-14", "电视柜", 1, 580, 580],
        ["木作工厂", "N1", "2026-05-14", "斗柜", 1, 320, 320],
        ["木作工厂", "N2", "2026-05-15", "床头柜", 2, 300, 600],
    ])
    mapping = {
        "supplier_name": "供应商",
        "note_no": "单号",
        "delivery_date": "日期",
        "item_name": "品名",
        "qty": "数量",
        "unit_price": "单价",
        "amount": "金额",
    }
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note", mapping=mapping,
        auto_match_orders=False,
    )
    db_session.commit()

    assert report.total_rows == 3
    assert report.inserted_parents == 2     # N1, N2
    assert report.inserted_children == 3
    assert report.skipped_rows == 0
    assert "木作工厂" in report.auto_created_suppliers

    from sqlalchemy import select
    notes = db_session.execute(select(DeliveryNote).order_by(DeliveryNote.note_no)).scalars().all()
    assert len(notes) == 2
    n1 = next(n for n in notes if n.note_no == "N1")
    assert n1.total_amount == Decimal("900")  # 580 + 320 (Excel 没单独给 total, 用行求和)
    lines = db_session.execute(
        select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == n1.id)
    ).scalars().all()
    assert len(lines) == 2
    assert {ln.item_name for ln in lines} == {"电视柜", "斗柜"}


def test_commit_delivery_note_dry_run_not_persisted(db_session):
    data = _xlsx("S", ["供应商", "单号", "日期", "品名", "数量", "金额"], [
        ["X", "N1", "2026-05-14", "x", 1, 100],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "note_no": "单号",
                 "delivery_date": "日期", "item_name": "品名",
                 "qty": "数量", "amount": "金额"},
        auto_match_orders=False, dry_run=True,
    )
    assert report.inserted_parents == 1
    # dry run 之后回滚, 不应留下 supplier
    from sqlalchemy import select
    assert db_session.execute(select(Supplier).where(Supplier.name == "X")).first() is None


def test_commit_missing_required_field_raises(db_session):
    data = _xlsx("S", ["数量"], [[1]])
    with pytest.raises(excel_importer.ImporterError) as ei:
        excel_importer.commit_sheet(
            db_session, file_bytes=data, sheet_name="S",
            entity_type="delivery_note", mapping={"qty": "数量"},
            auto_match_orders=False,
        )
    assert "必填字段未映射" in str(ei.value)


def test_commit_skips_bad_rows_keeps_good(db_session):
    data = _xlsx("S", ["供应商", "日期", "品名", "数量"], [
        ["A", "2026-05-14", "x", 1],
        ["B", "不是日期", "y", 2],   # 日期错 → 跳过
        ["", "2026-05-14", "z", 3],  # 供应商空 → 跳过
        ["C", "2026-05-14", "w", "abc"],  # 数量错 → 跳过
        ["D", "2026-05-14", "v", 4],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_match_orders=False,
    )
    db_session.commit()
    assert report.inserted_parents == 2   # 只 A, D 进了
    assert report.skipped_rows == 3
    assert len(report.errors) == 3


def test_commit_idempotent_skips_existing_note_no(db_session):
    data = _xlsx("S", ["供应商", "单号", "日期", "品名", "数量"], [
        ["X", "DUP", "2026-05-14", "p", 1],
    ])
    mapping = {"supplier_name": "供应商", "note_no": "单号",
               "delivery_date": "日期", "item_name": "品名", "qty": "数量"}
    excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note", mapping=mapping,
        auto_match_orders=False,
    )
    db_session.commit()
    # 再跑一次 → 跳过
    report2 = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note", mapping=mapping,
        auto_match_orders=False,
    )
    db_session.commit()
    assert report2.inserted_parents == 0
    assert report2.skipped_rows == 1
    assert any("已存在" in w for w in report2.warnings)


def test_commit_auto_create_supplier_disabled_errors(db_session):
    data = _xlsx("S", ["供应商", "日期", "品名", "数量"], [
        ["新供应商", "2026-05-14", "x", 1],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_create_suppliers=False, auto_match_orders=False,
    )
    db_session.commit()
    assert report.inserted_parents == 0
    assert report.skipped_rows == 1
    assert any("不存在" in e for e in report.errors)


def test_commit_existing_supplier_not_recreated(db_session):
    pre = Supplier(name="木作工厂", supplier_type="woodwork")
    db_session.add(pre); db_session.commit()

    data = _xlsx("S", ["供应商", "日期", "品名", "数量"], [
        ["木作工厂", "2026-05-14", "x", 1],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_match_orders=False,
    )
    db_session.commit()
    assert "木作工厂" not in report.auto_created_suppliers
    from sqlalchemy import select
    suppliers = db_session.execute(select(Supplier)).scalars().all()
    assert len(suppliers) == 1
    assert suppliers[0].supplier_type == "woodwork"  # 没被覆盖


def test_commit_auto_match_orders(db_session):
    from app.models.order import FactoryOrder as FO
    db_session.add(FO(
        factory_order_no="FO-001", platform_order_no="TB-001",
        factory_name="木作", product_code="P1",
        sku="电视柜 1800×850 黑色", qty=1, order_date=date(2026, 5, 10),
        expected_delivery=date(2026, 5, 14),
    ))
    db_session.commit()

    data = _xlsx("S", ["供应商", "日期", "品名", "规格", "数量"], [
        ["木作", "2026-05-14", "电视柜", "1800×850", 1],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "spec": "规格", "qty": "数量"},
        auto_match_orders=True,
    )
    db_session.commit()
    assert report.matched_lines >= 1


def test_commit_amount_auto_computed_from_price_qty(db_session):
    data = _xlsx("S", ["供应商", "日期", "品名", "数量", "单价"], [
        ["X", "2026-05-14", "x", 3, 100],
    ])
    excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量", "unit_price": "单价"},
        auto_match_orders=False,
    )
    db_session.commit()
    from sqlalchemy import select
    ln = db_session.execute(select(DeliveryNoteLine)).scalar_one()
    assert ln.amount == Decimal("300.00")


# ----------------------------- commit factory_order -------------- #


def test_commit_factory_order_basic(db_session):
    data = _xlsx("S", ["厂单号", "平台单号", "工厂", "下单日期", "SKU", "数量", "金额"], [
        ["FO-101", "TB-101", "X工厂", "2026-05-10", "电视柜黑色 1800", 1, 580],
        ["FO-102", "TB-102", "X工厂", "2026-05-11", "斗柜", 2, 640],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="factory_order",
        mapping={
            "factory_order_no": "厂单号",
            "platform_order_no": "平台单号",
            "factory_name": "工厂",
            "order_date": "下单日期",
            "sku": "SKU",
            "qty": "数量",
            "factory_bill_amount": "金额",
        },
    )
    db_session.commit()
    assert report.inserted_parents == 2
    from sqlalchemy import select
    fos = db_session.execute(select(FactoryOrder).order_by(FactoryOrder.factory_order_no)).scalars().all()
    assert len(fos) == 2
    assert fos[0].factory_order_no == "FO-101"
    assert fos[0].factory_bill_amount == Decimal("580")


def test_commit_factory_order_duplicate_skipped(db_session):
    pre = FactoryOrder(factory_order_no="FO-X", qty=1)
    db_session.add(pre); db_session.commit()

    data = _xlsx("S", ["厂单号", "数量"], [["FO-X", 1]])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="factory_order",
        mapping={"factory_order_no": "厂单号", "qty": "数量"},
    )
    db_session.commit()
    assert report.inserted_parents == 0
    assert report.skipped_rows == 1


def test_commit_unknown_entity_type_raises(db_session):
    with pytest.raises(ValueError):
        excel_importer.commit_sheet(
            db_session, file_bytes=b"", sheet_name="S",
            entity_type="not_an_entity", mapping={},
        )


def test_commit_chinese_date_format(db_session):
    data = _xlsx("S", ["供应商", "日期", "品名", "数量"], [
        ["X", "2026年5月14日", "x", 1],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_match_orders=False,
    )
    db_session.commit()
    assert report.inserted_parents == 1
    from sqlalchemy import select
    n = db_session.execute(select(DeliveryNote)).scalar_one()
    assert n.delivery_date == date(2026, 5, 14)


# ----------------------------- commit alipay_flow ---------------- #


def test_commit_alipay_flow_basic(db_session):
    from app.models.finance import AlipayFlow
    data = _xlsx("流水", ["账户", "流水号", "时间", "对方", "金额", "备注"], [
        ["企业号", "TX001", "2026-05-14 10:00:00", "X木业有限公司", -580, "5月料款"],
        ["企业号", "TX002", "2026-05-14 11:00:00", "万师傅安装", -120, "安装费"],
        ["企业号", "TX003", "2026-05-14 12:00:00", "客户A", 2000, "TB-001 收款"],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="流水",
        entity_type="alipay_flow",
        mapping={
            "account": "账户", "transaction_no": "流水号",
            "transaction_time": "时间", "counterparty": "对方",
            "amount": "金额", "remark": "备注",
        },
    )
    db_session.commit()
    assert report.inserted_parents == 3
    assert report.skipped_rows == 0
    from sqlalchemy import select
    flows = db_session.execute(select(AlipayFlow).order_by(AlipayFlow.transaction_no)).scalars().all()
    assert len(flows) == 3
    assert flows[0].amount == Decimal("-580")
    # smart_matching 应该把 X木业 标成 factory_payment, 万师傅 标成 logistics
    tags = {f.transaction_no: f.reconciliation_type for f in flows}
    assert tags["TX001"] == "factory_payment"
    assert tags["TX002"] == "logistics"


def test_commit_alipay_flow_dedup_same_account_tx(db_session):
    from app.models.finance import AlipayFlow
    pre = AlipayFlow(account="企业号", transaction_no="TX-EXIST",
                     amount=Decimal("-100"), reconciliation_status="open")
    db_session.add(pre); db_session.commit()
    data = _xlsx("S", ["账户", "流水号", "金额"], [
        ["企业号", "TX-EXIST", -100],   # 重复
        ["企业号", "TX-NEW", -200],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="alipay_flow",
        mapping={"account": "账户", "transaction_no": "流水号", "amount": "金额"},
    )
    db_session.commit()
    assert report.inserted_parents == 1
    assert report.skipped_rows == 1


def test_commit_alipay_flow_missing_required_skipped(db_session):
    data = _xlsx("S", ["账户", "流水号", "金额"], [
        ["", "TX1", -100],     # 账户空
        ["企业号", "", -100],  # 流水号空
        ["企业号", "TX2", -100],
    ])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="alipay_flow",
        mapping={"account": "账户", "transaction_no": "流水号", "amount": "金额"},
    )
    db_session.commit()
    assert report.inserted_parents == 1
    assert report.skipped_rows == 2
