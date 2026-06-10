from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from apps.core.crm.db import connect, init_db
from apps.core.crm.events import ensure_brand_row, ensure_shop_row
from apps.core.logging.image_embedding_repo import (
    DEFAULT_CLIP_DIM,
    DEFAULT_CLIP_MODEL_NAME,
    upsert_image_embedding,
)


def test_image_embedding_upsert_smoke(tmp_brand_shop_db) -> None:
    db_path, bid, sid = tmp_brand_shop_db
    conn = connect(db_path)
    try:
        iid = "img-smoke-1"
        now = "2026-01-01T00:00:00"
        conn.execute(
            """
            INSERT INTO image_library(
              image_id, brand_id, shop_id, category, local_path, question_label,
              match_keywords, send_count, enabled, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                iid,
                bid,
                sid,
                "product",
                "/tmp/nonexistent_smoke.png",
                "smoke",
                "",
                0,
                1,
                now,
                now,
            ),
        )
        conn.commit()
        blob = b"\x00" * (DEFAULT_CLIP_DIM * 4)
        upsert_image_embedding(
            conn,
            image_id=iid,
            brand_id=bid,
            shop_id=sid,
            model_name=DEFAULT_CLIP_MODEL_NAME,
            dim=DEFAULT_CLIP_DIM,
            vector_blob=blob,
        )
        row = conn.execute(
            "SELECT dim, length(vector_blob) FROM image_embeddings WHERE image_id = ?",
            (iid,),
        ).fetchone()
        assert row is not None
        assert int(row[0]) == DEFAULT_CLIP_DIM
        assert int(row[1]) == DEFAULT_CLIP_DIM * 4
    finally:
        conn.close()
