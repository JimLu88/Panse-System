# -*- coding: utf-8 -*-
"""定价图册 (带图导出) — 用户 2026-07-01。

一产品一行: 左侧大图 + 右侧该产品各 SKU 的 5 档售价表。
自包含 HTML: 图片服务端抓取 → 缩成 WebP → base64 内嵌, 浏览器直接打开即好看
(离线也能看、不受淘宝防盗链影响), Ctrl+P 可存 PDF。
选图: 本地图库主图优先 (始终可用), 无则退回 image_url(淘宝CDN, 老品常已失效)。
取不到的图退回占位框 (而非浏览器裂图)。
"""
from __future__ import annotations

import base64
import concurrent.futures
import html
import io
import urllib.request
from decimal import Decimal
from typing import Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.product import Product

_CATALOG_IMG_EDGE = 560   # 图册大图最长边 (base64 内嵌, 兼顾清晰与体积)

# 非产品 SKU (作废/服务/占位), 不进图册 — 图册只放能拍照的真实产品
_SKIP_NAME_KEYWORDS = (
    "作废", "全屋定制", "纯定制", "商家安装", "安装sku", "安装SKU",
    "送货入户", "自动生成", "占位", "测试链接",
)


def _is_real_product(name: str, code: str) -> bool:
    """排除作废/服务/占位类非产品 SKU (它们没有实物照片, 混进图册显脏)。"""
    if code == "PPS99999999999":          # 纯定制兜底码
        return False
    low = (name or "")
    return not any(k in low for k in _SKIP_NAME_KEYWORDS)


def _money(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    try:
        return f"¥{Decimal(str(v)):,.0f}"
    except Exception:
        return "—"


def _encode_webp(data: bytes) -> Optional[str]:
    """原始图字节 → 缩到 560px 的 WebP base64 data URI。失败返回 None。"""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((_CATALOG_IMG_EDGE, _CATALOG_IMG_EDGE))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=80, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _img_data_uri(image_url: Optional[str]) -> Optional[str]:
    """产品图 → base64 data URI (WebP)。淘宝 CDN 走 HTTP 抓取, 本地图库读文件。取不到返回 None。"""
    if not image_url:
        return None
    try:
        if image_url.startswith(("http://", "https://")):
            req = urllib.request.Request(image_url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://www.taobao.com/",
            })
            with urllib.request.urlopen(req, timeout=8) as r:   # noqa: S310 (只取自家商品图 URL)
                return _encode_webp(r.read())
        rel = (parse_qs(urlparse(image_url).query).get("path") or [None])[0]
        if not rel:
            return None
        from app.api.gallery import _compressed, _safe_resolve
        p = _safe_resolve(rel)
        if not p.is_file():
            return None
        return "data:image/webp;base64," + base64.b64encode(
            _compressed(p, _CATALOG_IMG_EDGE).read_bytes()).decode("ascii")
    except Exception:
        return None


def _source_bytes(src: str) -> Optional[bytes]:
    """图片来源 (http(s) URL 或 本地 path= URL) → 图片字节。取不到返回 None。"""
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://www.taobao.com/",
        })
        with urllib.request.urlopen(req, timeout=8) as r:   # noqa: S310 (只取自家商品图 URL)
            return r.read()
    rel = (parse_qs(urlparse(src).query).get("path") or [None])[0]
    if not rel:
        return None
    from app.api.gallery import _compressed, _safe_resolve
    p = _safe_resolve(rel)
    if not p.is_file():
        return None
    return _compressed(p, _CATALOG_IMG_EDGE).read_bytes()


def _to_png(data: bytes, max_edge: int) -> Optional[bytes]:
    """图片字节 → 缩到 max_edge 的 PNG (Excel 内嵌只认 PNG/JPEG, 不认 WebP)。失败 None。"""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


def product_image_map(codes, url_by_code, max_edge: int = 240) -> dict[str, bytes]:
    """产品编码 → PNG 图片字节 (供 Excel 内嵌)。本地图库主图优先, 否则 url_by_code 兜底
    (淘宝 CDN)。并行抓取; 取不到的编码不入结果。"""
    local_map: dict[str, str] = {}
    try:
        from app.services import gallery_lookup
        local_map = gallery_lookup.main_image_url_map(list(codes))
    except Exception:
        local_map = {}
    src_by_code: dict[str, str] = {}
    for c in codes:
        s = local_map.get(c) or (url_by_code.get(c) if url_by_code else None)
        if s:
            src_by_code[c] = s

    def _one(item):
        c, s = item
        try:
            raw = _source_bytes(s)
            return (c, _to_png(raw, max_edge) if raw else None)
        except Exception:
            return (c, None)

    out: dict[str, bytes] = {}
    if src_by_code:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for c, png in ex.map(_one, list(src_by_code.items())):
                if png:
                    out[c] = png
    return out


_CSS = """
:root { --ink:#1e293b; --sub:#94a3b8; --line:#eef0f4; --bg:#f5f6f8;
        --green:#16a34a; --violet:#7c3aed; --chip:#eef2ff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
.wrap { max-width:960px; margin:0 auto; padding:28px 20px 60px; }
.head { display:flex; align-items:flex-end; justify-content:space-between; margin:0 4px 20px; flex-wrap:wrap; gap:8px; }
.head h1 { margin:0; font-size:24px; font-weight:800; letter-spacing:-.01em; }
.head .meta { color:var(--sub); font-size:13px; }
.card { display:flex; gap:20px; background:#fff; border:1px solid var(--line); border-radius:14px;
        box-shadow:0 1px 3px rgba(15,23,42,.05); padding:16px; margin-bottom:16px; break-inside:avoid; }
.card .pic { width:240px; flex-shrink:0; }
.card .pic img { width:240px; height:240px; object-fit:contain; border-radius:10px;
        background:#f8fafc; border:1px solid var(--line); padding:6px; display:block; }
.card .pic .noimg { width:240px; height:240px; border-radius:10px; background:#f8fafc; border:1px solid var(--line);
        color:#cbd5e1; display:flex; align-items:center; justify-content:center; font-size:14px; }
.card .body { flex:1; min-width:0; }
.card .name { font-size:18px; font-weight:700; line-height:1.3; }
.card .code { display:inline-block; margin-top:4px; font-size:12px; color:#6366f1;
        background:var(--chip); border-radius:6px; padding:1px 8px; }
table { width:100%; border-collapse:collapse; margin-top:12px; font-size:13px; }
th,td { padding:7px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th { color:var(--sub); font-weight:600; background:#fafbfc; }
th.big { color:var(--green); }
th:first-child,td:first-child { text-align:left; white-space:normal; }
tbody tr:nth-child(even) td { background:#fbfcfe; }
td.spec { color:var(--ink); }
td.size { color:var(--sub); font-size:12px; }
.big { color:var(--green); font-weight:700; }
.muted { color:var(--sub); }
@media print { body{background:#fff;} .card{box-shadow:none;} .wrap{max-width:none;padding:0;} }
"""


def build_catalog_html(db: Session, limit: Optional[int] = None) -> str:
    """生成定价图册 HTML (一产品一行, 大图 + 各 SKU 5 档售价)。limit: 只取前 N 个产品 (预览用)。"""
    skus_by_code: dict[str, list[PricingSku]] = {}
    for s in db.execute(select(PricingSku).order_by(PricingSku.sku_code)).scalars().all():
        skus_by_code.setdefault(s.product_code, []).append(s)

    products = db.execute(select(Product).order_by(Product.code)).scalars().all()
    prod_by_code = {p.code: p for p in products}
    codes = [p.code for p in products if p.code in skus_by_code]
    for c in skus_by_code:                       # 有 SKU 但产品表无记录的, 也补进来
        if c not in prod_by_code:
            codes.append(c)

    def _name_of(c: str) -> str:
        prod = prod_by_code.get(c)
        return (prod.name if prod else None) or (
            skus_by_code[c][0].product_name if skus_by_code.get(c) else "") or ""
    codes = [c for c in codes if _is_real_product(_name_of(c), c)]   # 剔非产品 SKU

    if limit and limit > 0:
        codes = codes[:limit]

    # 每产品选一张图: 本地图库主图优先 (14k 张、始终可用、永不失效), 否则退回
    # 产品/ SKU 的 image_url (淘宝 CDN, 老品常已下架失效)。去重后并行抓取 → base64。
    local_map: dict[str, str] = {}
    try:
        from app.services import gallery_lookup
        local_map = gallery_lookup.main_image_url_map(codes)   # 根目录只扫一次
    except Exception:
        local_map = {}
    chosen: dict[str, str] = {}
    for code in codes:
        prod = prod_by_code.get(code)
        u = local_map.get(code) or (prod.image_url if prod else None) or next(
            (s.image_url for s in skus_by_code.get(code, []) if s.image_url), None)
        if u:
            chosen[code] = u
    uniq = list({u for u in chosen.values()})
    img_map: dict[str, Optional[str]] = {}
    if uniq:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for u, r in zip(uniq, ex.map(_img_data_uri, uniq)):
                img_map[u] = r

    cards: list[str] = []
    for code in codes:
        skus = skus_by_code.get(code, [])
        prod = prod_by_code.get(code)
        name = (prod.name if prod else None) or (skus[0].product_name if skus else None) or "(未命名)"
        img = img_map.get(chosen.get(code, ""))
        pic = (f'<img src="{img}" alt="">' if img else '<div class="noimg">暂无图片</div>')

        rows = []
        for s in skus:
            spec = html.escape(s.sku or s.size_category or "默认")
            size = html.escape(s.size_info or "")
            rows.append(
                f'<tr><td class="spec">{spec}'
                + (f'<div class="size">{size}</div>' if size else '')
                + f'</td><td>{_money(s.list_price)}</td><td>{_money(s.daily_price)}</td>'
                + f'<td>{_money(s.small_promo)}</td><td>{_money(s.mid_promo)}</td>'
                + f'<td class="big">{_money(s.big_promo)}</td></tr>'
            )
        table = (
            '<table><thead><tr><th>规格</th><th>标价</th><th>日常价</th>'
            '<th>小促</th><th>中促</th><th class="big">大促</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        ) if rows else '<div class="muted" style="margin-top:12px">暂无定价</div>'

        cards.append(
            f'<div class="card"><div class="pic">{pic}</div>'
            f'<div class="body"><div class="name">{html.escape(name)}</div>'
            f'<span class="code">{html.escape(code)}</span>{table}</div></div>'
        )

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    body = "".join(cards) or '<div class="muted">暂无带定价的产品。</div>'
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>畔色 · 定价图册</title><style>{_CSS}</style></head><body><div class="wrap">'
        f'<div class="head"><h1>畔色孚格 · 定价图册</h1>'
        f'<div class="meta">{len(codes)} 个产品 · 生成于 {now}</div></div>'
        f'{body}</div></body></html>'
    )
