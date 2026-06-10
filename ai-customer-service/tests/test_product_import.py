from __future__ import annotations

import tempfile
from pathlib import Path

import openpyxl
import pytest

from apps.core.crm.db import connect
from apps.core.crm.product_import import import_product_workbook


def test_import_product_workbook_minimal(tmp_brand_shop_db) -> None:
    db_path, bid, sid = tmp_brand_shop_db
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "产品List"
    ws.append(["产品编码", "畔色品名", "可定制范围", "SKU编码", "SKU"])
    ws.append(["P001", "测试品", "超出范围需主管", "S1", "规格A"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        xlsx_path = Path(tf.name)
    try:
        wb.save(xlsx_path)
        conn = connect(db_path)
        try:
            n_p, n_s = import_product_workbook(xlsx_path, conn, brand_id=bid, shop_id=sid)
            assert n_p >= 1
            assert n_s >= 1
            row = conn.execute(
                "SELECT customization_scope FROM products WHERE product_code='P001'"
            ).fetchone()
            assert row and "超出" in str(row[0])
        finally:
            conn.close()
    finally:
        xlsx_path.unlink(missing_ok=True)
