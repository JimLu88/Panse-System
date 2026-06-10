"""
HITL 图库：按问题标签检索、入库、发送计数；供控制台与自动回复共用。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from apps.core.runtime_paths import image_library_products_dir, image_library_root, image_library_tutorials_dir


def _now_compact() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _safe_under_library(p: Path) -> bool:
    try:
        root = str(image_library_root().resolve())
        ap = str(p.resolve())
        return os.path.commonpath([root, ap]) == root
    except (OSError, ValueError):
        return False


def insert_image_entry(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    category: str,
    src_file: Path,
    question_label: str,
    match_keywords: str = "",
) -> str:
    """复制图片到图库目录并写入 image_library；返回 image_id。"""
    cat = category.strip().lower()
    if cat not in ("product", "tutorial"):
        raise ValueError("category 须为 product 或 tutorial")
    src = Path(src_file)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    ext = src.suffix.lower() if src.suffix else ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        ext = ".png"
    image_id = uuid.uuid4().hex
    dest_dir = image_library_products_dir() if cat == "product" else image_library_tutorials_dir()
    dest = dest_dir / f"{image_id}{ext}"
    import shutil

    shutil.copy2(src, dest)
    now = _now_compact()
    conn.execute(
        """
        INSERT INTO image_library (
          image_id, brand_id, shop_id, category, local_path, question_label,
          match_keywords, send_count, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
        """,
        (
            image_id,
            brand_id,
            shop_id,
            cat,
            str(dest.resolve()),
            question_label.strip()[:500],
            (match_keywords or "").strip()[:2000],
            now,
            now,
        ),
    )
    conn.commit()
    return image_id


def bump_send_count(conn: sqlite3.Connection, *, image_id: str) -> None:
    now = _now_compact()
    conn.execute(
        """
        UPDATE image_library
        SET send_count = send_count + 1, updated_at = ?
        WHERE image_id = ?
        """,
        (now, image_id),
    )
    conn.commit()


def _score_row(query: str, question_label: str, match_keywords: str | None) -> float:
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    lab = (question_label or "").strip().lower()
    s1 = SequenceMatcher(None, q, lab).ratio() if lab else 0.0
    s2 = 0.0
    if match_keywords:
        for kw in re.split(r"[,，;；\s]+", match_keywords):
            k = kw.strip().lower()
            if len(k) >= 2 and k in q:
                s2 = max(s2, 0.18)
    return min(1.0, max(s1, s1 * 0.85 + s2))


@dataclass(slots=True)
class ImageSearchHit:
    image_id: str
    path: Path
    score: float
    question_label: str


def search_images_for_question(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    query: str,
    categories: tuple[str, ...] = ("product", "tutorial"),
    top_k: int = 3,
    min_score: float = 0.58,
) -> list[ImageSearchHit]:
    """按重写问句与图库 question_label / match_keywords 相似度检索。"""
    q = (query or "").strip()
    if not q:
        return []
    cats = tuple(c for c in categories if c in ("product", "tutorial"))
    if not cats:
        cats = ("product", "tutorial")
    placeholders = ",".join("?" * len(cats))
    cur = conn.execute(
        f"""
        SELECT image_id, local_path, question_label, match_keywords
        FROM image_library
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1 AND category IN ({placeholders})
        """,
        (brand_id, shop_id, *cats),
    )
    hits: list[ImageSearchHit] = []
    for row in cur.fetchall():
        iid, lp, ql, mk = str(row[0]), str(row[1]), str(row[2] or ""), row[3]
        p = Path(lp)
        if not p.is_file() or not _safe_under_library(p):
            continue
        sc = _score_row(q, ql, str(mk) if mk else "")
        if sc >= min_score:
            hits.append(ImageSearchHit(image_id=iid, path=p, score=sc, question_label=ql))
    hits.sort(key=lambda h: -h.score)
    return hits[: int(top_k)]


def list_images(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    category: str,
) -> list[tuple[str, str, str, int]]:
    """返回 (image_id, local_path, question_label, send_count) 列表。"""
    cat = category.strip().lower()
    if cat not in ("product", "tutorial"):
        raise ValueError("category 须为 product 或 tutorial")
    cur = conn.execute(
        """
        SELECT image_id, local_path, question_label, send_count
        FROM image_library
        WHERE brand_id = ? AND shop_id = ? AND category = ? AND enabled = 1
        ORDER BY updated_at DESC
        """,
        (brand_id, shop_id, cat),
    )
    return [(str(r[0]), str(r[1]), str(r[2] or ""), int(r[3] or 0)) for r in cur.fetchall()]
