"""上传文件 magic-byte 校验测试 (防伪造扩展名 / SVG / HTML)。"""
import pytest
from fastapi import HTTPException

from app.upload_guard import require_xlsx, require_raster_image


def test_require_xlsx_accepts_zip_header():
    require_xlsx(b"PK\x03\x04rest-of-xlsx")          # xlsx = zip
    require_xlsx(b"\xd0\xcf\x11\xe0old-xls")          # 旧 xls = OLE


def test_require_xlsx_rejects_fakes():
    for bad in (b"<?xml version=", b"<svg>", b"plain text", b"%PDF-1.4", b""):
        with pytest.raises(HTTPException):
            require_xlsx(bad)


def test_require_raster_image_accepts_real_formats():
    require_raster_image(b"\x89PNG\r\n\x1a\n...")     # PNG
    require_raster_image(b"\xff\xd8\xff\xe0jpeg")     # JPEG
    require_raster_image(b"GIF89a...")                # GIF
    require_raster_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ")  # WEBP
    require_raster_image(b"\x00\x00\x00\x18ftypheic")  # HEIC (iPhone)


def test_require_raster_image_rejects_svg_and_html():
    for bad in (b"<svg xmlns=", b"<?xml version=", b"<!DOCTYPE html>", b"<html>", b"text"):
        with pytest.raises(HTTPException):
            require_raster_image(bad)
