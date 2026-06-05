"""删除前回收站 (recycle_bin) 测试: 序列化正确 + 空集返回 None + 列表。"""
import json
import os
from decimal import Decimal

from app.services import recycle_bin


def test_archive_serializes_and_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("RECYCLE_BIN_DIR", str(tmp_path))
    from app.models.order import Order
    o = Order(order_no="X1", platform="淘宝", paid_amount=Decimal("100.50"))
    path = recycle_bin.archive({"orders": [o]}, batch_ref="import_job:5", reason="test")
    assert path and os.path.isfile(path)
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["batch_ref"] == "import_job:5"
    row = data["data"]["orders"][0]
    assert row["order_no"] == "X1"
    assert row["paid_amount"] == "100.50"   # Decimal -> str (JSON 安全)


def test_archive_empty_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("RECYCLE_BIN_DIR", str(tmp_path))
    assert recycle_bin.archive({"orders": []}, batch_ref="x", reason="y") is None


def test_list_archives(tmp_path, monkeypatch):
    monkeypatch.setenv("RECYCLE_BIN_DIR", str(tmp_path))
    from app.models.order import Order
    recycle_bin.archive({"orders": [Order(order_no="A")]}, batch_ref="b1", reason="r")
    items = recycle_bin.list_archives()
    assert len(items) == 1 and items[0]["file"].endswith(".json")
