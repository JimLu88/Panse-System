"""图库图片向量编码（CLIP）；未安装 torch/open_clip 时占位跳过。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from apps.core.logging.image_embedding_repo import (
    DEFAULT_CLIP_DIM,
    DEFAULT_CLIP_MODEL_NAME,
    list_images_missing_embedding,
    upsert_image_embedding,
)

_CLIP: tuple[object, object, str] | None = None


def clip_available() -> bool:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _clip_bundle() -> tuple[object, object, str]:
    global _CLIP
    if _CLIP is None:
        import open_clip
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai", device=device
        )
        model.eval()
        _CLIP = (model, preprocess, device)
    return _CLIP


def embed_image_file(_path: Path) -> bytes:
    """
    返回 float32 向量 bytes（未安装依赖时抛错由调用方捕获）。
    """
    import numpy as np
    import torch
    from PIL import Image

    model, preprocess, device = _clip_bundle()
    img = preprocess(Image.open(_path).convert("RGB")).unsqueeze(0).to(device)
    with torch.no_grad():
        v = model.encode_image(img)
        v = v / v.norm(dim=-1, keepdim=True)
    return v.float().cpu().numpy().astype(np.float32).tobytes()


def embed_missing_for_shop(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    limit: int = 400,
) -> tuple[int, int]:
    """
    为当前店铺写入缺失的 CLIP 向量。
    返回 (成功条数, 扫描到的待处理条数)。
    """
    lg = log or (lambda _m: None)
    cancel = should_cancel or (lambda: False)
    if not clip_available():
        raise RuntimeError("未安装 torch / open_clip，无法生成向量（可 pip install torch open-clip-torch）")
    rows = list_images_missing_embedding(
        conn, brand_id=brand_id, shop_id=shop_id, model_name=DEFAULT_CLIP_MODEL_NAME, limit=limit
    )
    total = len(rows)
    ok = 0
    for iid, lp in rows:
        if cancel():
            lg("向量分析已取消")
            break
        p = Path(lp)
        if not p.is_file():
            lg(f"跳过（文件不存在）{lp}")
            continue
        try:
            blob = embed_image_file(p)
            upsert_image_embedding(
                conn,
                image_id=iid,
                brand_id=brand_id,
                shop_id=shop_id,
                model_name=DEFAULT_CLIP_MODEL_NAME,
                dim=DEFAULT_CLIP_DIM,
                vector_blob=blob,
            )
            ok += 1
            lg(f"已向量化 {ok}/{total} {p.name}")
        except Exception as e:
            lg(f"向量化失败 {p.name}: {e!r}")
    return ok, total
