"""客户岩板采购明细：别名、按客户保留、重传幂等（不访问外部服务）。"""
from datetime import date
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from sqlalchemy import select

from app.models.order import PartPurchase
from app.services import accessory_capture_service as capture
from app.services.purchase_table_import import import_purchases_table_core


@pytest.fixture(autouse=True)
def no_capture(monkeypatch):
    monkeypatch.setattr(capture, "run_capture", lambda *a, **kw: {
        "material_match": {"matched": 0}, "order_link": {"linked": 0},
        "supplier_relabel": {"relabeled": 0},
    })


def workbook_bytes(rows, headers=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers or ["日期", "姓名", "材质及说明", "尺寸（mm）", "加工", "价格"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def purchases(db):
    return list(db.scalars(select(PartPurchase).order_by(PartPurchase.id)))


def test_aliases_serial_date_and_replay(db_session):
    rows = [[46176, name, "9mm洞石", "1200×480", "磨边\n打孔", 230] for name in ("客户甲", "客户乙")]
    raw = workbook_bytes(rows)
    r = import_purchases_table_core(db_session, raw, "采购.xlsx")
    assert (r["inserted"], r["skipped_invalid"], r["unmapped_columns"]) == (2, 0, [])
    saved = purchases(db_session)
    assert sum(p.total_amount for p in saved) == Decimal("460")
    assert all(p.purchase_date == date(2026, 6, 3) for p in saved)
    assert all(p.supplier is None and p.related_order_no is None for p in saved)
    assert saved[0].spec == "1200×480"
    assert saved[0].remark == "姓名：客户甲\n加工：磨边\n打孔"
    r2 = import_purchases_table_core(db_session, raw, "重命名.xlsx")
    assert (r2["inserted"], r2["skipped_duplicate"]) == (0, 2)
    assert len(purchases(db_session)) == 2


def test_identical_occurrences_and_incremental_upload(db_session):
    row = [46176, "客户甲", "岩板", "800×480", "磨边", 170]
    raw = workbook_bytes([row, row])
    assert import_purchases_table_core(db_session, raw, "a.xlsx")["inserted"] == 2
    assert import_purchases_table_core(db_session, raw, "a.xlsx")["skipped_duplicate"] == 2
    assert import_purchases_table_core(db_session, workbook_bytes([row]*3), "a.xlsx")["inserted"] == 1
    assert len(purchases(db_session)) == 3


def test_specs_processing_and_sheet_boundaries(db_session):
    rows = [[46176, "客户甲", "岩板", "800×480", "磨边", 170],
            [46176, "客户甲", "岩板", "900×480", "磨边", 170],
            [46176, "客户甲", "岩板", "800×480", "打孔", 170]]
    wb = openpyxl.load_workbook(BytesIO(workbook_bytes(rows)))
    wb.copy_worksheet(wb.active)
    buf = BytesIO()
    wb.save(buf)
    raw = buf.getvalue()
    assert import_purchases_table_core(db_session, raw, "a.xlsx")["inserted"] == 6
    assert import_purchases_table_core(db_session, raw, "a.xlsx")["skipped_duplicate"] == 6


def test_legacy_nonempty_update_is_preserved(db_session):
    first = "日期,供应商,配件名称,规格,数量,金额\n2026-06-03,某厂,岩板,旧规格,1,170\n".encode()
    second = first.decode().replace("旧规格,1", "新规格,2").encode()
    assert import_purchases_table_core(db_session, first, "a.csv")["inserted"] == 1
    assert import_purchases_table_core(db_session, second, "a.csv")["updated"] == 1
    assert purchases(db_session)[0].spec == "新规格"
    assert purchases(db_session)[0].qty == Decimal("2")


def test_customer_rows_do_not_overwrite_legacy_record(db_session):
    old = PartPurchase(purchase_no="OLD", purchase_date=date(2026,6,3), material_name="岩板",
                       amount=170, total_amount=170, qty=1, remark="旧账保留")
    db_session.add(old)
    db_session.commit()
    raw = workbook_bytes([[46176, "客户甲", "岩板", "800×480", "磨边", 170]])
    assert import_purchases_table_core(db_session, raw, "a.xlsx")["inserted"] == 1
    assert old.remark == "旧账保留" and old.spec is None
