"""上传文件校验: 按文件头 magic bytes 判类型, 不信任客户端的 content-type / 扩展名。

防御: 伪造扩展名把可执行/SVG/HTML 喂给解析器或 OCR (SVG 可带脚本)。
所有上传端点已是 admin/operator 鉴权, 这里是纵深防御。
"""
from __future__ import annotations

from fastapi import HTTPException

# xlsx/xlsm/docx 均为 zip 容器; 旧 .xls 为 OLE 复合文档
_XLSX_SIGS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\xd0\xcf\x11\xe0")
_RASTER_IMG_SIGS = (
    b"\x89PNG",             # PNG (4 字节魔数足以区分非图片/SVG/HTML)
    b"\xff\xd8\xff",         # JPEG
    b"GIF87a", b"GIF89a",   # GIF
    b"BM",                   # BMP
)


def require_xlsx(data: bytes) -> None:
    if not data.startswith(_XLSX_SIGS):
        raise HTTPException(400, "文件不是有效的 Excel (.xlsx/.xls); 请勿伪造扩展名/类型")


def require_raster_image(data: bytes) -> None:
    head = data[:16]
    is_webp = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    is_isobmff = head[4:8] == b"ftyp"   # HEIC/HEIF/AVIF (iPhone 照片) 等 ISO-BMFF
    if not (head.startswith(_RASTER_IMG_SIGS) or is_webp or is_isobmff):
        raise HTTPException(400, "文件不是有效图片 (PNG/JPEG/GIF/BMP/WEBP/HEIC); 已拒绝 (防 SVG/HTML 伪造)")
