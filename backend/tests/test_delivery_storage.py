"""delivery_storage: 文件按 supplier/year/month 归档 + 安全读取."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services import delivery_storage


def test_save_upload_creates_year_month_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    info = delivery_storage.save_upload(
        7, content=b"\x89PNG\r\n\x1a\n", original_name="单据.png",
        on_date=date(2026, 5, 14),
    )
    assert info["year"] == 2026
    assert info["month"] == 5
    assert info["mime_type"] == "image/png"
    assert info["size_bytes"] == 8
    fp = Path(info["file_path"])
    assert fp.exists()
    assert fp.parent.name == "05"
    assert fp.parent.parent.name == "2026"
    assert fp.parent.parent.parent.name == "7"


def test_save_upload_unsafe_extension_becomes_bin(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    info = delivery_storage.save_upload(
        1, content=b"x", original_name="evil.exe", on_date=date(2026, 5, 1),
    )
    assert info["file_path"].endswith(".bin")
    assert info["mime_type"] == "application/octet-stream"


def test_read_returns_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    info = delivery_storage.save_upload(
        1, content=b"hello", original_name="x.jpg", on_date=date(2026, 5, 1),
    )
    assert delivery_storage.read(info["file_path"]) == b"hello"


def test_read_rejects_path_outside_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    outside = tmp_path.parent / "outside.txt"
    outside.write_bytes(b"secret")
    try:
        with pytest.raises(PermissionError):
            delivery_storage.read(str(outside))
    finally:
        outside.unlink(missing_ok=True)


def test_read_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        delivery_storage.read(str(tmp_path / "no-such.jpg"))


def test_remove_safe_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    info = delivery_storage.save_upload(
        2, content=b"x", original_name="x.jpg", on_date=date(2026, 5, 1),
    )
    delivery_storage.remove(info["file_path"])
    assert not Path(info["file_path"]).exists()


def test_remove_outside_root_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
    target = tmp_path.parent / "important.txt"
    target.write_bytes(b"keep")
    try:
        delivery_storage.remove(str(target))
        assert target.exists()
    finally:
        target.unlink(missing_ok=True)
