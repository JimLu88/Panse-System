# -*- coding: utf-8 -*-
"""产品图库 (用户需求 2026-06-11)。

挂载: 主机 `D:\\畔色 产品图库` → 容器 GALLERY_ROOT(/app/gallery, 只读)。
      群晖部署时只改 docker-compose 卷的主机路径, 代码零改动。
结构: 「PPS编码 产品名…」文件夹 / 「主图|SKU 图|详情页」子目录 / 图片文件。
匹配: 文件夹名以产品编码开头 → 自动对应产品总表。
性能: 列表用 ?thumb=1 缩略图 (最长边 320px 的 WebP, 磁盘缓存),
      点开大图走 ?max_edge=1280 压缩版 (同样缓存) — 外网带宽友好;
      原图仅在需要时显式请求。缓存放 storage/gallery_thumbs (随 storage 卷持久)。
      压缩限并发 + JPEG draft 降采样, 防一夹大图同时压垮弱 CPU (见 _compressed)。
安全: 相对路径 resolve 后必须仍在 GALLERY_ROOT 内, 否则 403 (防 ../ 穿越)。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product

router = APIRouter(prefix="/api/gallery", tags=["gallery"])

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_CODE_RE = re.compile(r"^(P[A-Z]{0,3}\d{8,})")   # 文件夹名开头的产品编码
_ROOT_GROUP = "(根目录)"           # tree 里直接放文件夹根的图片用这个组名
_MAX_UPLOAD_BYTES = 30 * 1024 * 1024   # 单张上限 30MB
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')   # 文件名里不许出现的字符

# 缩略/预览压缩参数 (2026-06-25 图库性能优化):
#   源图多是 6-8MB / 24MP 相机原图, 弱 CPU(群晖 2 核) 现场压缩极慢; 一夹 169 张
#   首访=169 张同时解码+编码 → CPU 过载、请求超时 → 平板上一片裂图。对策:
#   - 列表缩略 320px (120px 槽位 @2.6x 够清晰; 原 480 偏大、4 倍像素白烧 CPU)
#   - 预览默认 1280px (平板足够锐, 比 1600 省 ~1/3 解码+带宽)
#   - 全局信号量限并发: 再多请求一起来, 也只放 N 张进 PIL, 其余排队不压垮 NAS
#     (可经环境变量 GALLERY_COMPRESS_CONCURRENCY 调; 默认 3 ≈ 群晖核数)
_THUMB_EDGE = 320
_PREVIEW_EDGE = 1280
_COMPRESS_CONCURRENCY = max(1, int(os.environ.get("GALLERY_COMPRESS_CONCURRENCY", "3")))
_compress_sem = threading.Semaphore(_COMPRESS_CONCURRENCY)


class MoveImagesRequest(BaseModel):
    """在同一产品图库内整理图片；目标分组不存在时自动创建。"""

    folder: str
    paths: list[str]
    target_group: str


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


def _validated_group_name(group: str) -> str:
    """校验界面输入的一级分组名，禁止路径字符和隐藏目录。"""
    grp = group.strip()
    if not grp:
        raise HTTPException(400, "目标文件夹名不能为空")
    if grp != _ROOT_GROUP:
        if len(grp) > 60:
            raise HTTPException(400, "目标文件夹名不能超过 60 个字符")
        if grp in {".", ".."} or grp.startswith(".") or _UNSAFE_NAME_RE.search(grp):
            raise HTTPException(400, "目标文件夹名包含不允许的字符")
    return grp


@router.post("/move")
def move_images(payload: MoveImagesRequest):
    """在当前产品图库内批量移动图片，不覆盖目标文件夹中的同名图片。"""
    folder_name = payload.folder.strip()
    if not folder_name:
        raise HTTPException(400, "产品图库文件夹不能为空")
    if not payload.paths:
        raise HTTPException(400, "请至少选择一张图片")
    if len(payload.paths) > 500:
        raise HTTPException(400, "单次最多整理 500 张图片")

    base = _safe_resolve(folder_name)
    if not base.is_dir():
        raise HTTPException(404, "产品图库文件夹不存在")
    base_resolved = base.resolve()
    target_group = _validated_group_name(payload.target_group)
    target_dir = (base if target_group == _ROOT_GROUP
                  else _safe_resolve(str(Path(folder_name) / target_group)))

    # 先把全部来源路径做越界校验，再创建目录/移动，避免错误请求只执行一半。
    sources: list[Path] = []
    seen: set[Path] = set()
    for rel in payload.paths:
        src = _safe_resolve(rel)
        try:
            src.relative_to(base_resolved)
        except ValueError:
            raise HTTPException(403, "只能整理当前产品图库内的图片")
        if src not in seen:
            seen.add(src)
            sources.append(src)

    target_dir.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, str]] = []
    conflicts: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    failed: list[dict[str, str]] = []
    skipped_same = 0
    gallery_root = _root().resolve()

    for src in sources:
        src_rel = str(src.relative_to(gallery_root)).replace("\\", "/")
        if not src.exists():
            missing.append(src_rel)
            continue
        if not src.is_file() or src.suffix.lower() not in _IMAGE_EXT:
            invalid.append(src_rel)
            continue
        dst = target_dir / src.name
        if src == dst.resolve():
            skipped_same += 1
            continue
        if dst.exists():
            conflicts.append(src.name)
            continue

        # 用独占创建保证同名绝不覆盖；复制成功后才删除来源，任一步失败都保留原图。
        created_dst = False
        try:
            with src.open("rb") as reader, dst.open("xb") as writer:
                created_dst = True
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            shutil.copystat(src, dst)
            src.unlink()
        except FileExistsError:
            conflicts.append(src.name)
            continue
        except OSError as exc:
            if created_dst:
                dst.unlink(missing_ok=True)
            failed.append({"path": src_rel, "reason": str(exc)})
            continue

        dst_rel = str(dst.relative_to(gallery_root)).replace("\\", "/")
        moved.append({"from": src_rel, "to": dst_rel})

    return {
        "ok": not failed,
        "folder": folder_name,
        "target_group": target_group,
        "requested": len(sources),
        "moved": len(moved),
        "moved_paths": moved,
        "conflicts": len(conflicts),
        "conflict_names": conflicts,
        "missing": len(missing),
        "missing_paths": missing,
        "invalid": len(invalid),
        "invalid_paths": invalid,
        "skipped_same": skipped_same,
        "failed": len(failed),
        "failures": failed,
    }


def _safe_filename(name: str, default_ext: str = ".jpg") -> str:
    """清洗上传文件名: 去路径分隔符/非法字符, 保留扩展名, 兜底名。"""
    base = Path(name or "").name                  # 去掉任何目录部分 (防 ../)
    base = _UNSAFE_NAME_RE.sub("_", base).strip().strip(".")
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in _IMAGE_EXT:
        ext = default_ext
    stem = stem or "image"
    return f"{stem}{ext}"


def _dedupe_path(target_dir: Path, filename: str) -> Path:
    """同名文件已存在 → 追加 _1/_2…, 不覆盖既有图。"""
    out = target_dir / filename
    if not out.exists():
        return out
    stem, ext = os.path.splitext(filename)
    i = 1
    while True:
        cand = target_dir / f"{stem}_{i}{ext}"
        if not cand.exists():
            return cand
        i += 1


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    folder: Optional[str] = Form(None, description="目标产品文件夹名(没有则用 product_code 新建)"),
    product_code: Optional[str] = Form(None, description="产品编码(folder 缺省时据此建/找文件夹)"),
    group: Optional[str] = Form(None, description="分组子目录: 主图/SKU 图/场景图…; 空或(根目录)=放文件夹根"),
):
    """上传一张产品图到图库 (用户需求 2026-06-14: 自己加新图)。

    存到 GALLERY_ROOT/<产品文件夹>/<分组>/<文件名>。文件夹/分组不存在自动建;
    同名不覆盖(追加 _1); 路径越界/非图片/超 30MB 一律拒绝。需图库卷可写(去掉 :ro)。
    """
    root = _root()
    if not root.exists():
        raise HTTPException(503, "图库目录未挂载")

    # 1) 定位/新建产品文件夹
    folder_name = (folder or "").strip()
    if not folder_name:
        if not (product_code or "").strip():
            raise HTTPException(400, "需提供 folder 或 product_code")
        folder_name = product_code.strip()
    base = _safe_resolve(folder_name)
    base.mkdir(parents=True, exist_ok=True)

    # 2) 分组子目录 ((根目录) / 空 = 直接放文件夹根)
    grp = (group or "").strip()
    if grp and grp != _ROOT_GROUP:
        target_dir = _safe_resolve(str(Path(folder_name) / grp))
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = base

    # 3) 校验 + 落盘 (大小上限 + 扩展名)
    raw_name = file.filename or ""
    if Path(raw_name).suffix.lower() not in _IMAGE_EXT:
        raise HTTPException(400, "只允许图片文件 (jpg/png/webp/gif/bmp)")
    fname = _safe_filename(raw_name)
    if Path(fname).suffix.lower() not in _IMAGE_EXT:
        raise HTTPException(400, "只允许图片文件 (jpg/png/webp/gif/bmp)")
    out = _dedupe_path(target_dir, fname)
    try:
        out.relative_to(root.resolve())          # 双保险: 最终路径仍在根内
    except ValueError:
        raise HTTPException(403, "路径越界")
    size = 0
    try:
        with out.open("wb") as w:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    w.close()
                    out.unlink(missing_ok=True)
                    raise HTTPException(413, "图片超过 30MB 上限")
                w.write(chunk)
    except HTTPException:
        raise
    except OSError as e:
        out.unlink(missing_ok=True)
        # 卷只读 / 磁盘满 等
        raise HTTPException(500, f"写入失败(图库卷是否可写?): {e}")
    # 预热: 立即生成该图 缩略+预览 两个尺寸, 上传后浏览/点开大图即秒开 (用户 2026-06-25)
    for _e in (_THUMB_EDGE, _PREVIEW_EDGE):
        try:
            _compressed(out, _e)
        except Exception:  # noqa: BLE001 — 预热失败不影响上传成功
            pass
    rel = str(out.relative_to(root.resolve())).replace("\\", "/")
    return {"ok": True, "folder": folder_name, "group": grp or _ROOT_GROUP,
            "path": rel, "filename": out.name, "size": size}


@router.post("/import-folder")
async def import_folder_bulk(
    files: list[UploadFile] = File(...),
    folder: Optional[str] = Form(None, description="目标文件夹名(优先); 空则据 product_code 定位/新建"),
    product_code: Optional[str] = Form(None, description="产品编码: 定位既有「编码 产品名」文件夹, 无则按产品总表名新建"),
    group: Optional[str] = Form(None, description="分组子目录; 空或(根目录)=放文件夹根(与相机直导扁平结构一致)"),
    db: Session = Depends(get_db),
):
    """批量导入整个产品文件夹的图 (用户 2026-07-05: 网页选整个文件夹一次传, 自动关联产品)。

    - 定位既有「编码 产品名」文件夹(品牌前缀宽匹配), 没有则按产品总表名新建;
    - **同名跳过**(不覆盖、不建 _1 副本): 补缺不降质 —— 已在库的全尺寸原图不会被压缩副本顶掉;
    - 每张新图预热 缩略+预览; 返回 added/skipped(已存在)/invalid 统计。需图库卷可写。
    """
    root = _root()
    if not root.exists():
        raise HTTPException(503, "图库目录未挂载")

    # 1) 定位/新建目标文件夹
    target_name = (folder or "").strip()
    if not target_name:
        pc = (product_code or "").strip()
        if not pc:
            raise HTTPException(400, "需提供 folder 或 product_code")
        from app.services import gallery_lookup
        existing = gallery_lookup.product_folder(pc)   # 宽匹配既有「编码 产品名」
        if existing is not None:
            target_name = existing.name
        else:
            prod = db.execute(select(Product).where(Product.code == pc)).scalar_one_or_none()
            pname = (prod.name if prod and prod.name else "").strip()
            target_name = f"{pc} {pname}".strip()       # 新建「编码 产品名」
    base = _safe_resolve(target_name)
    base.mkdir(parents=True, exist_ok=True)

    grp = (group or "").strip()
    if grp and grp != _ROOT_GROUP:
        target_dir = _safe_resolve(str(Path(target_name) / grp))
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = base

    added = skipped = invalid = 0
    too_large = unsupported = write_failed = 0
    saved: list[Path] = []
    for f in files:
        raw_name = f.filename or ""
        if Path(raw_name).suffix.lower() not in _IMAGE_EXT:
            invalid += 1
            unsupported += 1
            continue
        fname = _safe_filename(raw_name)
        out = target_dir / fname
        if out.exists():                # 同名跳过: 不覆盖原图、不建压缩副本
            skipped += 1
            continue
        size = 0
        ok = True
        reject_reason: Optional[str] = None
        try:
            with out.open("wb") as w:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _MAX_UPLOAD_BYTES:
                        ok = False
                        reject_reason = "too_large"
                        break
                    w.write(chunk)
        except OSError:
            ok = False
            reject_reason = "write_failed"
        if not ok:
            out.unlink(missing_ok=True)
            invalid += 1
            if reject_reason == "too_large":
                too_large += 1
            elif reject_reason == "write_failed":
                write_failed += 1
            continue
        added += 1
        saved.append(out)

    # 预热: 新图立即生成 缩略+预览 两尺寸, 浏览/点开秒开 (失败不影响导入)
    for out in saved:
        for _e in (_THUMB_EDGE, _PREVIEW_EDGE):
            try:
                _compressed(out, _e)
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "folder": target_name, "group": grp or _ROOT_GROUP,
            "added": added, "skipped": skipped, "invalid": invalid,
            "too_large": too_large, "unsupported": unsupported,
            "write_failed": write_failed, "total": len(files)}


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


def _thumb_cache_path(src: Path, max_edge: int) -> Path:
    """压缩图缓存路径 (key 含源 mtime+尺寸, 源图改自动失效)。供 _compressed 与预热共用 — key 逻辑只此一处, 防漂移。"""
    key = hashlib.md5(f"{src}|{src.stat().st_mtime_ns}|{max_edge}|v2".encode()).hexdigest()
    return _thumb_cache_dir() / f"{key}.webp"


def _compressed(src: Path, max_edge: int) -> Path:
    """生成/复用压缩 WebP (按 源路径+mtime+尺寸 哈希缓存, 源图更新自动失效)。

    弱 CPU 优化:
      - 信号量限并发: 任一时刻最多 _COMPRESS_CONCURRENCY 张进 PIL, 余者排队,
        防 169 张大图同时压垮群晖 (这是平板上"缩略图全裂"的根因)。
      - JPEG draft 解码期降采样: 24MP 原图按 1/8 解码再缩, 快约一个量级 (非 JPEG 空操作)。
      - EXIF 方向矫正: 相机竖拍图缩略不躺倒。
      - 原子落盘 (临时文件改名): 读到的要么不存在、要么是完整文件, 杜绝半截缓存→裂图。
    """
    out = _thumb_cache_path(src, max_edge)
    if out.exists():
        return out
    with _compress_sem:
        if out.exists():        # 等锁期间已被别的线程生成
            return out
        from PIL import Image, ImageOps
        tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        with Image.open(src) as im:
            im.draft("RGB", (max_edge, max_edge))   # 大 JPEG 解码期降采样 (非 JPEG 无副作用)
            im2 = ImageOps.exif_transpose(im)       # 尊重相机方向
            im2 = im2.convert("RGB")
            im2.thumbnail((max_edge, max_edge))
            im2.save(tmp, "WEBP", quality=80, method=4)
        os.replace(tmp, out)    # 原子改名: 半截 .tmp 永不会被当缓存返回
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
    edge = _THUMB_EDGE if thumb else (max_edge if 0 < max_edge <= 4000 else 0)
    if edge:
        try:
            cached = _compressed(p, edge)
            # 压缩图 key 含源 mtime, 内容不变 → 30 天 immutable, 浏览器连 304 都省
            return FileResponse(cached, media_type="image/webp",
                                headers={"Cache-Control": "public, max-age=2592000, immutable"})
        except Exception:
            pass   # 压缩失败回退原图
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})
