# -*- coding: utf-8 -*-
"""产品图库 (用户需求 2026-06-11)。

挂载: 主机 `D:\\畔色 产品图库` → 容器 GALLERY_ROOT(/app/gallery, 只读)。
      群晖部署时只改 docker-compose 卷的主机路径, 代码零改动。
结构: 「PPS编码 产品名…」文件夹 / 「主图|SKU 图|详情页」子目录 / 图片文件。
匹配: 文件夹名以产品编码开头 → 自动对应产品总表。
性能: 列表用 ?thumb=1 缩略图 (最长边 480px 的 WebP, 磁盘缓存),
      点开大图走 ?max_edge=1600 压缩版 (同样缓存) — 外网带宽友好;
      原图仅在需要时显式请求。缓存放 storage/gallery_thumbs (随 storage 卷持久)。
安全: 相对路径 resolve 后必须仍在 GALLERY_ROOT 内, 否则 403 (防 ../ 穿越)。
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product

router = APIRouter(prefix="/api/gallery", tags=["gallery"])

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_CODE_RE = re.compile(r"^(P[A-Z]{0,3}\d{8,})")   # 文件夹名开头的产品编码


def _root() -> Path:
    return Path(os.environ.get("GALLERY_ROOT", "/app/gallery"))


def _thumb_cache_dir() -> Path:
    d = Path("/app/storage/gallery_thumbs")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_resolve(rel: str) -> Path:
    """相对路径 → 根内绝对路径; 越界(../ 等)一律 403。"""
    root = _root().resolve()
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(403, "路径越界")
    return p


def _folder_code(folder_name: str) -> Optional[str]:
    m = _CODE_RE.match(folder_name.strip())
    return m.group(1) if m else None


@router.get("/folders")
def list_folders(db: Session = Depends(get_db)):
    """图库根目录的产品文件夹列表 (带产品总表匹配 + 图片数)。"""
    root = _root()
    if not root.exists():
        return {"available": False, "folders": [],
                "hint": "图库目录未挂载 (检查 docker-compose 卷 D:/畔色 产品图库)"}
    products = {p.code: p.name for p in db.execute(select(Product)).scalars().all()}
    folders = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        code = _folder_code(d.name)
        n_imgs = sum(1 for f in d.rglob("*") if f.suffix.lower() in _IMAGE_EXT)
        folders.append({
            "folder": d.name,
            "product_code": code,
            "product_name": products.get(code) if code else None,
            "matched": bool(code and code in products),
            "image_count": n_imgs,
        })
    return {"available": True, "folders": folders}


@router.get("/by-product/{product_code}")
def folders_for_product(product_code: str):
    """某产品对应的图库文件夹 (文件夹名以编码开头; 含品牌变体前缀宽匹配)。"""
    root = _root()
    if not root.exists():
        return {"folders": []}
    # 数字主体宽匹配: PPS/PFG/P 变体都算同一产品
    digits = re.sub(r"^[A-Z]+", "", product_code)
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        code = _folder_code(d.name)
        if not code:
            continue
        if code == product_code or (digits and re.sub(r"^[A-Z]+", "", code) == digits):
            out.append(d.name)
    return {"folders": out}


@router.get("/tree")
def folder_tree(folder: str = Query(..., description="根目录下的产品文件夹名")):
    """文件夹内容树: 子目录(主图/SKU图/详情页…) → 图片相对路径列表。"""
    base = _safe_resolve(folder)
    if not base.is_dir():
        raise HTTPException(404, "文件夹不存在")
    groups: list[dict] = []
    # 直接放在文件夹根的图片归到 "(根目录)"
    root_imgs = [f for f in sorted(base.iterdir())
                 if f.is_file() and f.suffix.lower() in _IMAGE_EXT]
    if root_imgs:
        groups.append({"group": "(根目录)",
                       "images": [f"{folder}/{f.name}" for f in root_imgs]})
    for sub in sorted(base.iterdir()):
        if not sub.is_dir():
            continue
        imgs = [f for f in sorted(sub.rglob("*"))
                if f.is_file() and f.suffix.lower() in _IMAGE_EXT]
        if not imgs:
            continue   # 空子目录不显示
        groups.append({"group": sub.name,
                       "images": [str(Path(folder) / f.relative_to(base)) for f in imgs]})
    return {"folder": folder, "groups": groups}


@router.post("/refresh-images")
def refresh_images(db: Session = Depends(get_db)):
    """刷新产品配图 (用户需求 2026-06-12): 把图库里的图刷进产品/定价表的图片列。

    只填空缺 (image_url 为空的产品/SKU), 已有的淘宝图不覆盖:
      - 产品 image_url ← 图库主图 (主图/1-1 第一张)
      - 定价 SKU image_url ← SKU 图 (标准名直配或尺寸款式匹配)
    图库浏览/下单图本来就实时读文件夹, 这个按钮解决的是"表格图片列还空着"。
    """
    from app.models.pricing import PricingSku
    from app.services import gallery_lookup

    root = _root()
    if not root.exists():
        raise HTTPException(503, "图库目录未挂载")
    filled_products = filled_skus = 0
    from urllib.parse import quote
    for p in db.execute(select(Product)).scalars().all():
        if p.image_url:
            continue
        rel = gallery_lookup.main_image_rel(p.code)
        if rel:
            p.image_url = f"/api/gallery/file?path={quote(rel)}&thumb=1"
            filled_products += 1
    for s in db.execute(select(PricingSku)).scalars().all():
        if s.image_url:
            continue
        rel = gallery_lookup.sku_image_rel(s.product_code, s.sku_code, s.sku)
        if rel:
            s.image_url = f"/api/gallery/file?path={quote(rel)}&max_edge=1600"
            filled_skus += 1
    db.commit()
    return {"filled_products": filled_products, "filled_skus": filled_skus}


@router.get("/coverage")
def gallery_coverage(db: Session = Depends(get_db)):
    """图库体检: 每个产品的 SKU 配图覆盖率 (实时算, **不写异常**)。

    用户拍板 (2026-06-12): 老产品缺图可能永远补不上, 不进异常中心 —
    这里是随点随查的体检报告, 看完即走, 不留账。
    """
    from app.models.pricing import PricingSku
    from app.services import gallery_lookup

    root = _root()
    if not root.exists():
        raise HTTPException(503, "图库目录未挂载")
    products = {p.code: p.name for p in db.execute(select(Product)).scalars().all()}
    skus_by_product: dict[str, list] = {}
    for s in db.execute(select(PricingSku)).scalars().all():
        skus_by_product.setdefault(s.product_code, []).append(s)

    rows, no_folder = [], []
    total_skus = total_with = 0
    for code in sorted(skus_by_product):
        skus = skus_by_product[code]
        folder = gallery_lookup.product_folder(code)
        if folder is None:
            no_folder.append({"code": code, "name": products.get(code),
                              "sku_count": len(skus)})
            continue
        sku_dir = next((folder / n for n in ("SKU", "SKU 图", "SKU图", "sku")
                        if (folder / n).is_dir()), None)
        stems = ([f.stem for f in sku_dir.iterdir()
                  if f.is_file() and f.suffix.lower() in _IMAGE_EXT]
                 if sku_dir else [])
        missing = []
        for s in skus:
            ok = any(st.startswith(s.sku_code) for st in stems)
            if not ok:
                # 标准名没中 → 再试 token 匹配 (尺寸+款式)
                ok = gallery_lookup.sku_image_rel(code, s.sku_code, s.sku) is not None
            if ok:
                total_with += 1
            else:
                missing.append(s.sku or s.sku_code)
        total_skus += len(skus)
        # 没对应到任何 SKU 编码的文件 (旧命名/颜色重复图)
        unmatched_files = sum(
            1 for st in stems
            if not any(st.startswith(s.sku_code) for s in skus))
        rows.append({
            "code": code, "name": products.get(code), "folder": folder.name,
            "total": len(skus), "with_image": len(skus) - len(missing),
            "missing": missing, "unmatched_files": unmatched_files,
        })
    rows.sort(key=lambda r: (r["with_image"] == r["total"], r["code"]))
    return {"products": rows, "no_folder": no_folder,
            "totals": {"sku_total": total_skus, "with_image": total_with,
                       "missing": total_skus - total_with,
                       "no_folder_products": len(no_folder)}}


@router.post("/scan")
def scan_gallery(
    create: bool = Query(False, description="true=把新文件夹建成产品档案"),
    db: Session = Depends(get_db),
):
    """扫描图库: 找出图库里有、产品总表里没有的产品文件夹。

    用户拍板 (2026-06-11): 图库浏览本来就是实时读文件夹 (丢图进去立刻能看),
    但「产品总表新增记录」需要这个按钮 — 先 dry-run 列出来, 确认后 create=1 建档。
    """
    root = _root()
    if not root.exists():
        raise HTTPException(503, "图库目录未挂载")
    existing = {p.code for p in db.execute(select(Product)).scalars().all()}
    news = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        code = _folder_code(d.name)
        if not code or code in existing:
            continue
        name = d.name[len(code):].strip() or code
        n_imgs = sum(1 for f in d.rglob("*") if f.suffix.lower() in _IMAGE_EXT)
        news.append({"folder": d.name, "code": code, "name": name, "image_count": n_imgs})
    created = 0
    if create and news:
        from app.services import field_change_service
        for n in news:
            db.add(Product(code=n["code"], name=n["name"], remark="图库扫描自动建档"))
            field_change_service.record(
                db, table="products", pk=n["code"], field="created",
                old=None, new=n["name"], actor="图库扫描", source="web",
                row_label=f"{n['code']} {n['name']}", field_label="新建产品",
            )
            created += 1
        db.commit()
    return {"new_folders": news, "created": created}


def _compressed(src: Path, max_edge: int) -> Path:
    """生成/复用压缩 WebP (按 源路径+mtime+尺寸 哈希缓存, 源图更新自动失效)。"""
    key = hashlib.md5(f"{src}|{src.stat().st_mtime_ns}|{max_edge}".encode()).hexdigest()
    out = _thumb_cache_dir() / f"{key}.webp"
    if out.exists():
        return out
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        im.save(out, "WEBP", quality=80)
    return out


@router.get("/file")
def serve_file(
    path: str = Query(..., description="相对图库根的图片路径"),
    thumb: bool = Query(False, description="缩略图 (480px WebP)"),
    max_edge: int = Query(0, description="压缩到最长边 N px (0=原图)"),
):
    """图片本体。列表用 thumb=1; 预览大图用 max_edge=1600; 原图不带参数。"""
    p = _safe_resolve(path)
    if not p.is_file() or p.suffix.lower() not in _IMAGE_EXT:
        raise HTTPException(404, "图片不存在")
    edge = 480 if thumb else (max_edge if 0 < max_edge <= 4000 else 0)
    if edge:
        try:
            cached = _compressed(p, edge)
            return FileResponse(cached, media_type="image/webp",
                                headers={"Cache-Control": "public, max-age=86400"})
        except Exception:
            pass   # 压缩失败回退原图
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})
