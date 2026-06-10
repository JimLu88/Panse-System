"""image_embeddings 表读写（CLIP 等向量）。"""

from __future__ import annotations

import sqlite3
from apps.core.crm.events import now_iso

# 与 image_embed_service.embed_image_file 使用的权重一致
DEFAULT_CLIP_MODEL_NAME = "open_clip_ViT-B-32_openai"
DEFAULT_CLIP_DIM = 512


def list_images_missing_embedding(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    model_name: str = DEFAULT_CLIP_MODEL_NAME,
    limit: int = 500,
) -> list[tuple[str, str]]:
    """返回 (image_id, local_path) 尚未写入该 model 向量的图库行。"""
    cur = conn.execute(
        """
        SELECT il.image_id, il.local_path
        FROM image_library il
        LEFT JOIN image_embeddings ie
          ON ie.image_id = il.image_id AND ie.model_name = ?
        WHERE il.brand_id = ? AND il.shop_id = ? AND il.enabled = 1
          AND COALESCE(il.local_path,'') != ''
          AND ie.embedding_id IS NULL
        ORDER BY il.updated_at DESC
        LIMIT ?
        """,
        (model_name, brand_id, shop_id, int(limit)),
    )
    out: list[tuple[str, str]] = []
    for r in cur.fetchall():
        out.append((str(r[0]), str(r[1])))
    return out


def upsert_image_embedding(
    conn: sqlite3.Connection,
    *,
    image_id: str,
    brand_id: str,
    shop_id: str,
    model_name: str,
    dim: int,
    vector_blob: bytes,
) -> None:
    eid = f"{image_id}|{model_name}"[:180]
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO image_embeddings(
          embedding_id, image_id, brand_id, shop_id, model_name, dim, vector_blob, updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(image_id, model_name) DO UPDATE SET
          dim=excluded.dim,
          vector_blob=excluded.vector_blob,
          updated_at=excluded.updated_at
        """,
        (eid, image_id, brand_id, shop_id, model_name, int(dim), vector_blob, ts),
    )
    conn.commit()
