from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from apps.core.crm.db import connect, init_db
from apps.core.crm.events import ensure_brand_row, ensure_shop_row


@pytest.fixture
def tmp_brand_shop_db() -> Generator[tuple[Path, str, str], None, None]:
    """临时 SQLite：已 init_db + brand/shop 行。yield (db_path, brand_id, shop_id)。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = Path(dbf.name)
    try:
        conn = connect(db_path)
        init_db(conn)
        ensure_brand_row(conn, brand_id="b_test")
        ensure_shop_row(
            conn,
            brand_id="b_test",
            shop_id="b_test:s_test",
            shop_code="s_test",
            display_name="s_test",
        )
        conn.close()
        yield db_path, "b_test", "b_test:s_test"
    finally:
        db_path.unlink(missing_ok=True)
