"""
话术向量库：OpenAI text-embedding-3-small
- embed_texts(): 批量生成向量（API 调用）
- build_kb_vectors(): 为店铺全部话术生成向量并存入 db
- vector_search(): 余弦相似度搜索，返回最相似 top-k 条
"""
from __future__ import annotations

import json
import sqlite3
import struct
import time
from pathlib import Path
from typing import Generator

import numpy as np

from apps.core.configs.base_settings import BaseSettings, load_base_settings

_EMBED_MODEL = "text-embedding-3-large"  # v1.6.7 默认升级（中文更准）；实际以 settings.embedding_model 为准
_EMBED_DIM = 3072
_BATCH = 64  # OpenAI 单次最多 2048，64 条留余量


def _resolve_embed_model(settings: "BaseSettings | None") -> str:
    """实际使用的 embedding 模型名：优先 settings.embedding_model，回退默认。"""
    m = (getattr(settings, "embedding_model", "") or "").strip() if settings else ""
    return m or _EMBED_MODEL


def compose_embed_text(question: str, kb_tags: str = "") -> str:
    """v1.6.7：向量文本 = 「[动机标签] 问句」。

    动机（如 库存可做确认/发货时效）从 kb_tags 提取并前置，让买家口语问句
    （"有现货么"）能匹配到同动机簇。**不含答案**——答案文本长会稀释问句信号。
    """
    q = (question or "").strip()
    tags = (kb_tags or "").strip()
    motive = ""
    if tags:
        for seg in (tags.replace("，", ",").replace("、", ",").replace("|", ",")
                        .replace(";", ",").replace("；", ",").split(",")):
            seg = seg.strip()
            if seg and seg != "通用":
                motive = seg
                break
    return f"[{motive}] {q}" if motive else q


# ── 向量序列化 ─────────────────────────────────────────────────────────────
def _vec_to_blob(v: list[float] | np.ndarray) -> bytes:
    arr = np.array(v, dtype=np.float32)
    return arr.tobytes()


def _blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ── OpenAI 调用 ────────────────────────────────────────────────────────────
def embed_texts(texts: list[str], *, settings: BaseSettings | None = None) -> list[list[float]]:
    """
    调用 OpenAI text-embedding-3-small，返回与 texts 等长的向量列表。
    若 API 不可用则抛异常，由调用方决定是否降级。
    """
    import openai  # type: ignore

    st = settings or load_base_settings()
    api_key = (st.openai_api_key or "").strip()
    if not api_key:
        raise RuntimeError("未配置 OpenAI API Key，无法生成向量。请在「设置中心 → 接入配置」里填写。")

    base_url = (st.llm_api_base or "").strip() or None
    model = _resolve_embed_model(st)
    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    results: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        batch = texts[i : i + _BATCH]
        resp = client.embeddings.create(model=model, input=batch)
        results.extend([d.embedding for d in resp.data])
    return results


# ── 数据库迁移（确保 embedding_blob 列存在） ────────────────────────────────
def ensure_embedding_blob_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(kb_embeddings)")}
    if "embedding_blob" not in cols:
        conn.execute("ALTER TABLE kb_embeddings ADD COLUMN embedding_blob BLOB")
        conn.commit()


# ── 批量建向量库 ───────────────────────────────────────────────────────────
class BuildVectorResult:
    """build_kb_vectors 的详细结果。"""

    def __init__(self) -> None:
        self.done: int = 0
        self.skipped: int = 0
        self.errors: list[str] = []  # 每条批次失败的描述

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def all_ok(self) -> bool:
        return not self.errors and self.done > 0


def build_kb_vectors(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    settings: BaseSettings | None = None,
    progress_cb: "callable[[int, int], None] | None" = None,
) -> BuildVectorResult:
    """
    为店铺所有话术生成向量并写入 kb_embeddings.embedding_blob。
    遇到批次失败时不中断，继续处理其余批次，失败信息记录在 result.errors。
    progress_cb(done_so_far, total) 用于 UI 进度回调。
    """
    ensure_embedding_blob_column(conn)
    st = settings or load_base_settings()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    result = BuildVectorResult()

    rows = conn.execute(
        """
        SELECT kb_id, question, answer, COALESCE(kb_tags, '') FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
        ORDER BY created_at
        """,
        (brand_id, shop_id),
    ).fetchall()

    if not rows:
        return result

    total = len(rows)
    model_name = _resolve_embed_model(st)

    for i in range(0, total, _BATCH):
        batch = rows[i : i + _BATCH]
        batch_end = i + len(batch)
        # v1.6.7：向量文本 = 「[动机] 问句」（kb_tags 第4列含动机；不含答案避免稀释）
        texts = [compose_embed_text(r[1], r[3]) for r in batch]

        try:
            vecs = embed_texts(texts, settings=st)
        except Exception as e:
            result.skipped += len(batch)
            err_msg = f"第 {i+1}–{batch_end} 条（共 {len(batch)} 条）：{e}"
            result.errors.append(err_msg)
            if progress_cb:
                progress_cb(result.done + result.skipped, total)
            continue  # 跳过本批，继续下一批

        for row, vec in zip(batch, vecs):
            kb_id = row[0]
            blob = _vec_to_blob(vec)
            conn.execute(
                """
                INSERT INTO kb_embeddings(kb_id, brand_id, shop_id, embedding_ref, model, updated_at, embedding_blob)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kb_id) DO UPDATE SET
                    embedding_blob = excluded.embedding_blob,
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (kb_id, brand_id, shop_id, "", model_name, now, blob),
            )
            result.done += 1
        conn.commit()
        if progress_cb:
            progress_cb(result.done + result.skipped, total)

    return result


# ── 向量搜索 ───────────────────────────────────────────────────────────────
def vector_search(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    query: str,
    top_k: int = 5,
    min_score: float = 0.60,
    settings: BaseSettings | None = None,
) -> list[tuple[float, str, str, str]]:
    """
    用向量相似度在话术库中检索。
    返回 [(score, kb_id, question, answer), ...] 按分数降序。
    若无向量数据或 API 不可用，返回空列表（调用方自行降级）。
    """
    ensure_embedding_blob_column(conn)

    # 1. 查询向量化
    try:
        q_vecs = embed_texts([query], settings=settings)
    except Exception:
        return []
    q_vec = np.array(q_vecs[0], dtype=np.float32)

    # 2. 加载本店铺全部向量
    cur = conn.execute(
        """
        SELECT ke.kb_id, ke.question, ke.answer, kv.embedding_blob
        FROM kb_entries ke
        JOIN kb_embeddings kv ON ke.kb_id = kv.kb_id
        WHERE ke.brand_id = ? AND ke.shop_id = ? AND ke.enabled = 1
          AND kv.embedding_blob IS NOT NULL
        """,
        (brand_id, shop_id),
    )

    results: list[tuple[float, str, str, str]] = []
    dim_mismatch = 0
    for kb_id, question, answer, blob in cur:
        if not blob:
            continue
        v = _blob_to_vec(blob)
        # v1.6.7：换 embedding 模型后维度会变（small=1536/large=3072），
        # 旧维度向量与新 query 不可比——跳过，避免 np.dot 形状崩溃。
        if v.shape != q_vec.shape:
            dim_mismatch += 1
            continue
        score = _cosine(q_vec, v)
        if score >= min_score:
            results.append((score, str(kb_id), str(question), str(answer)))
    if dim_mismatch and not results:
        # 全部维度不匹配 = 向量是旧模型建的，需重建
        import logging
        logging.getLogger("apps.core.ai.kb_vector").warning(
            "vector_search: %d 条向量维度与当前模型(%s)不符，请重建向量库",
            dim_mismatch, _resolve_embed_model(settings),
        )

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


def has_vectors_for_shop(conn: sqlite3.Connection, *, brand_id: str, shop_id: str) -> bool:
    """检查该店铺是否已有至少一条向量记录。"""
    ensure_embedding_blob_column(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) FROM kb_embeddings kv
        JOIN kb_entries ke ON ke.kb_id = kv.kb_id
        WHERE ke.brand_id = ? AND ke.shop_id = ? AND kv.embedding_blob IS NOT NULL
        """,
        (brand_id, shop_id),
    ).fetchone()
    return bool(row and row[0] > 0)
