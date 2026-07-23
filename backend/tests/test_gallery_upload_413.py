import asyncio
from io import BytesIO

from starlette.datastructures import UploadFile

from app.api import gallery


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(BytesIO(content), filename=name, size=len(content))


def test_folder_import_reports_rejection_reasons(tmp_path, monkeypatch, db_session):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    monkeypatch.setattr(gallery, "_MAX_UPLOAD_BYTES", 4)
    files = [
        _upload("ok.jpg", b"abc"),
        _upload("large.jpg", b"abcde"),
        _upload("not-an-image.txt", b"abc"),
    ]

    result = asyncio.run(gallery.import_folder_bulk(
        files=files,
        folder="PPS24210070901 测试产品",
        product_code=None,
        group="(根目录)",
        db=db_session,
    ))

    assert result["added"] == 1
    assert result["invalid"] == 2
    assert result["too_large"] == 1
    assert result["unsupported"] == 1
    assert result["write_failed"] == 0
    target = tmp_path / "PPS24210070901 测试产品"
    assert (target / "ok.jpg").read_bytes() == b"abc"
    assert not (target / "large.jpg").exists()
    assert not (target / "not-an-image.jpg").exists()


def test_safe_filename_no_longer_turns_non_image_into_jpg(tmp_path, monkeypatch, db_session):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    result = asyncio.run(gallery.import_folder_bulk(
        files=[_upload("payload.exe", b"not an image")],
        folder="PPS24210070901 测试产品",
        product_code=None,
        group="(根目录)",
        db=db_session,
    ))
    assert result["added"] == 0
    assert result["unsupported"] == 1
    assert list(tmp_path.rglob("*.*")) == []
