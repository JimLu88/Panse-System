# -*- coding: utf-8 -*-
"""图库查找: 给 产品/SKU 找 主图 与 SKU 图 (工厂下单图用, 2026-06 用户需求)。

目录结构 (两代并存):
    新: 编码 产品名/SKU/SKU-1.2m-砂白.jpg + 主图/1-1/*.jpg + 主图/3-4/*.jpg
    旧: 编码 产品名/SKU 图/SKU-曜黑色-餐桌1.4米.jpg + 主图/*.jpg
SKU 图匹配顺序:
    1. 文件名以 sku_code 开头 (子代理重命名后的标准名 "SKU编号 SKU名.jpg")
    2. 尺寸 token (1.2m/1.2米) + 其余 token 包含匹配, 唯一命中才算
返回的都是相对图库根的路径, 前端拼 /api/gallery/file?path=… 取图。
找不到一律返回 None, 绝不抛错 (图库缺失不能影响下单图生成)。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_CODE_RE = re.compile(r"^(P[A-Z]{0,3}\d{8,})")
_SKU_DIR_NAMES = ("SKU", "SKU 图", "SKU图", "sku")
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:米|m|M)")


def _root() -> Path:
    return Path(os.environ.get("GALLERY_ROOT", "/app/gallery"))


def product_folder(product_code: str) -> Optional[Path]:
    """编码 → 图库产品文件夹 (品牌前缀宽匹配: PPS/PFG 数字主体相同算同一产品)。"""
    root = _root()
    if not product_code or not root.exists():
        return None
    digits = re.sub(r"^[A-Z]+", "", product_code)
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        m = _CODE_RE.match(d.name.strip())
        if not m:
            continue
        code = m.group(1)
        if code == product_code or (digits and re.sub(r"^[A-Z]+", "", code) == digits):
            return d
    return None


def _rel(p: Path) -> str:
    return str(p.relative_to(_root()))


def _main_image_in(base: Path) -> Optional[str]:
    """产品文件夹内找主图: 优先 主图/1-1→3-4→主图 第一张; 没有「主图」子目录(或其为空)
    则兜底取文件夹根第一张 —— 相机直导的扁平文件夹(DSCF*.JPG 平铺)也能关联上主图。"""
    zhu = base / "主图"
    if zhu.is_dir():
        for sub in (zhu / "1-1", zhu / "3-4", zhu):
            if not sub.is_dir():
                continue
            imgs = sorted(f for f in sub.iterdir()
                          if f.is_file() and f.suffix.lower() in _IMAGE_EXT)
            if imgs:
                return _rel(imgs[0])
        imgs = sorted(f for f in zhu.rglob("*")
                      if f.is_file() and f.suffix.lower() in _IMAGE_EXT)
        if imgs:
            return _rel(imgs[0])
    # 兜底: 无「主图」子目录 → 取文件夹根第一张图 (新拍产品扁平文件夹)
    root_imgs = sorted(f for f in base.iterdir()
                       if f.is_file() and f.suffix.lower() in _IMAGE_EXT)
    return _rel(root_imgs[0]) if root_imgs else None


def main_image_rel(product_code: str) -> Optional[str]:
    """主图: 优先 主图/1-1 第一张, 其次 主图 下任意第一张。"""
    try:
        base = product_folder(product_code)
        if base is None:
            return None
        return _main_image_in(base)
    except OSError:
        return None


def main_image_url_map(codes) -> dict[str, str]:
    """批量: 产品编码 → 图库主图缩略 URL (产品列表"图库优先"显示用)。

    根目录只扫一次再按编码数字主体配文件夹, 避免逐产品全盘扫描拖慢列表。
    找不到的编码不入结果 (前端回退 image_url / 史莱姆占位)。
    """
    from urllib.parse import quote
    out: dict[str, str] = {}
    root = _root()
    if not root.exists():
        return out
    by_digits: dict[str, Path] = {}
    try:
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            m = _CODE_RE.match(d.name.strip())
            if m:
                by_digits.setdefault(re.sub(r"^[A-Z]+", "", m.group(1)), d)
    except OSError:
        return out
    for code in codes:
        if not code:
            continue
        base = by_digits.get(re.sub(r"^[A-Z]+", "", code))
        if base is None:
            continue
        try:
            rel = _main_image_in(base)
        except OSError:
            rel = None
        if rel:
            out[code] = f"/api/gallery/file?path={quote(rel)}&thumb=1"
    return out


def _norm_size_tokens(text: str) -> set[str]:
    """提取尺寸 token 统一成 '1.2米' 形式 (1.2m/1.2M/1.2 米 → 1.2米, 1m → 1米)。"""
    out = set()
    for m in _SIZE_RE.finditer(text or ""):
        num = m.group(1)
        if num.endswith(".0"):
            num = num[:-2]
        out.add(f"{num}米")
    return out


def sku_gallery_url_map(items) -> dict[str, str]:
    """批量: [(product_code, sku_code, sku_name)] → {sku_code: 图库图 URL}。

    用户拍板 (2026-06-12): SKU 图片列全部图库优先。每个产品只列一次 SKU 文件夹
    (避免逐 SKU 扫盘), 标准名前缀直配; 没中再走单条 token 匹配兜底。
    找不到的 SKU 不入结果 (前端回退淘宝 image_url)。
    """
    from urllib.parse import quote
    cache: dict[str, list[tuple[str, str]]] = {}   # product_code → [(stem, rel)]
    out: dict[str, str] = {}
    for pc, sc, sn in items:
        if not pc or not sc:
            continue
        if pc not in cache:
            entries: list[tuple[str, str]] = []
            try:
                base = product_folder(pc)
                if base is not None:
                    sku_dir = next((base / n for n in _SKU_DIR_NAMES
                                    if (base / n).is_dir()), None)
                    if sku_dir is not None:
                        entries = [(f.stem, _rel(f)) for f in sorted(sku_dir.iterdir())
                                   if f.is_file() and f.suffix.lower() in _IMAGE_EXT]
            except OSError:
                pass
            cache[pc] = entries
        rel = next((r for st, r in cache[pc] if st.startswith(sc)), None)
        if rel is None and cache[pc] and sn:
            rel = sku_image_rel(pc, sc, sn)
        if rel:
            out[sc] = f"/api/gallery/file?path={quote(rel)}&thumb=1"
    return out


def sku_image_rel(product_code: str, sku_code: Optional[str],
                  sku_name: Optional[str]) -> Optional[str]:
    """SKU 图: 标准名前缀直配, 否则 尺寸+token 唯一命中。"""
    try:
        base = product_folder(product_code)
        if base is None:
            return None
        sku_dir = next((base / n for n in _SKU_DIR_NAMES if (base / n).is_dir()), None)
        if sku_dir is None:
            return None
        imgs = sorted(f for f in sku_dir.iterdir()
                      if f.is_file() and f.suffix.lower() in _IMAGE_EXT)
        if not imgs:
            return None
        # 1. 标准名: "SKU编号 …"
        if sku_code:
            for f in imgs:
                if f.stem.startswith(sku_code):
                    return _rel(f)
        # 2. 尺寸 token + 其余文字 token
        if not sku_name:
            return None
        want_sizes = _norm_size_tokens(sku_name)
        # SKU 名去掉尺寸后的关键词 (款式/颜色), 取长度≥2的段
        rest = _SIZE_RE.sub("", sku_name)
        words = [w for w in re.split(r"[-—()（）\s/]+", rest) if len(w) >= 2]
        cands = []
        for f in imgs:
            fsizes = _norm_size_tokens(f.stem)
            if want_sizes and fsizes and want_sizes != fsizes:
                continue
            if want_sizes and not fsizes:
                continue
            score = sum(1 for w in words if w in f.stem)
            cands.append((score, f))
        if not cands:
            return None
        cands.sort(key=lambda t: (-t[0], t[1].name))
        top_score = cands[0][0]
        top = [f for s, f in cands if s == top_score]
        if len(top) == 1:
            return _rel(top[0])
        return None   # 多候选不猜 — 等子代理重命名后走标准名直配
    except OSError:
        return None
