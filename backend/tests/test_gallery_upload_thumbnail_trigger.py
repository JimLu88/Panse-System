"""图库上传成功后立即生成缩略图；每日任务只负责漏网兜底。"""
from __future__ import annotations

import asyncio
from io import BytesIO

from starlette.datastructures import UploadFile

from app.api import gallery


def test_single_upload_warms_thumb_and_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    generated = []
    monkeypatch.setattr(gallery, "_compressed", lambda path, edge: generated.append((path, edge)))
    upload = UploadFile(BytesIO(b"image"), filename="new.jpg", size=5)

    result = asyncio.run(gallery.upload_image(
        file=upload,
        folder="PPS-322 测试产品",
        product_code=None,
        group="主图",
    ))

    saved = tmp_path / "PPS-322 测试产品" / "主图" / "new.jpg"
    assert result["ok"] is True
    assert saved.read_bytes() == b"image"
    assert generated == [(saved, gallery._THUMB_EDGE), (saved, gallery._PREVIEW_EDGE)]
