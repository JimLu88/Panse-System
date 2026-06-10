"""
1+2+3 满血 RAG：元数据 LLM 标签 → 硬过滤 → BM25 + 稠密向量混合（RRF）→ Cross-Encoder/LLM 重排。

- 不使用 HyDE。
- 可选依赖：rank_bm25、sentence-transformers；缺失时自动降级为 RRF(BM25简易版 + 字符相似度排序)。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from apps.core.ai.llm_client import litellm_completion_text
from apps.core.ai.rag_kb import best_kb_match_by_similarity
from apps.core.configs.base_settings import BaseSettings


@dataclass(slots=True)
class KbDoc:
    kb_id: str
    question: str
    answer: str
    kb_tags: str


@dataclass(slots=True)
class KbSearchResult:
    kb_id: str
    question: str
    answer: str
    rerank_score: float
    rrf_score: float
    alternatives: tuple[str, ...]  # 其他候选答案，供 HITL neg


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        return m.group(1).strip()
    return s


def extract_kb_metadata_tags_llm(
    *,
    settings: BaseSettings,
    context_block: str,
    buyer_text: str,
    rewritten_query: str,
) -> list[str]:
    """从上下文 + 重写问句提取类目标签（硬过滤用）；禁止 HyDE。"""
    model = (settings.model_front_desk or "").strip()
    if not model:
        return []
    system = (
        "你是电商家具客服知识库的「类目标签提取器」。"
        "只根据已给出的对话上文与客户意图，输出 JSON：{\"tags\":[\"标签1\",...]}\n"
        "标签为简短中文名词或短语（如：餐边柜、卷帘门柜、安装、售后、AA柱、物流）。"
        "不要编造未出现的具体尺寸数字；不要假设客户未说的产品名；不要输出 HyDE 或虚构段落；"
        "最多 6 个标签；若无明确类目可 {\"tags\":[]}。"
    )
    user = (
        "【对话上文】\n"
        + (context_block or "").strip()
        + "\n\n【客户原话】\n"
        + (buyer_text or "").strip()
        + "\n\n【重写问句】\n"
        + (rewritten_query or "").strip()
    )
    try:
        raw = litellm_completion_text(
            settings=settings,
            model=model,
            system=system,
            user=user,
            max_tokens=200,
            temperature=0.0,
        )
        obj = json.loads(_strip_json_fence(raw))
        tags = obj.get("tags") if isinstance(obj, dict) else None
        if not isinstance(tags, list):
            return []
        out: list[str] = []
        for t in tags:
            s = str(t).strip()
            if s and s not in out and len(s) <= 16:
                out.append(s)
        return out[:8]
    except Exception:
        return []


def _parse_kb_tags_raw(raw: str) -> set[str]:
    t = (raw or "").strip()
    if not t:
        return set()
    if t.startswith("["):
        try:
            arr = json.loads(t)
            if isinstance(arr, list):
                return {str(x).strip() for x in arr if str(x).strip()}
        except json.JSONDecodeError:
            pass
    return {x.strip() for x in re.split(r"[,，;；|/]", t) if x.strip()}


def metadata_prefilter(docs: list[KbDoc], llm_tags: list[str]) -> list[KbDoc]:
    if not llm_tags:
        return docs
    tagset = set(llm_tags)
    kept: list[KbDoc] = []
    bucket_global: list[KbDoc] = []
    for d in docs:
        dt = _parse_kb_tags_raw(d.kb_tags)
        if not dt:
            bucket_global.append(d)
        elif tagset & dt:
            kept.append(d)
    if kept:
        return kept + bucket_global
    return docs


def _tokenize_bm25(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", (text or "").lower())


def _bm25_rank_ids(docs: list[KbDoc], query: str, *, top_n: int = 40) -> list[str]:
    corpus = [_tokenize_bm25(f"{d.question} {d.answer[:400]}") for d in docs]
    qtok = _tokenize_bm25(query)
    if not qtok or not any(corpus):
        return [d.kb_id for d in docs[:top_n]]
    try:
        from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(qtok)
        order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
        return [docs[i].kb_id for i in order[:top_n]]
    except Exception:
        scores: list[tuple[float, str]] = []
        qn = (query or "").lower()
        for d in docs:
            blob = f"{d.question} {d.answer[:400]}".lower()
            scores.append((SequenceMatcher(None, qn, blob).ratio(), d.kb_id))
        scores.sort(reverse=True, key=lambda x: x[0])
        return [kb for _, kb in scores[:top_n]]


def _dense_rank_ids(
    docs: list[KbDoc],
    query: str,
    *,
    embed_model_path: str | None,
    top_n: int = 40,
) -> list[str]:
    texts = [f"{d.question}\n{d.answer[:360]}" for d in docs]
    ids = [d.kb_id for d in docs]
    if embed_model_path:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            m = SentenceTransformer(embed_model_path)
            qv = m.encode([query], normalize_embeddings=True)[0]
            dv = m.encode(texts, normalize_embeddings=True)
            import numpy as np  # noqa: PLC0415

            sims = (np.asarray(dv) @ np.asarray(qv)).tolist()
            order = sorted(range(len(docs)), key=lambda i: sims[i], reverse=True)
            return [ids[i] for i in order[:top_n]]
        except Exception:
            pass
    qn = (query or "").lower()
    scored: list[tuple[float, str]] = []
    for d, blob in zip(docs, texts):
        scored.append((SequenceMatcher(None, qn, blob.lower()).ratio(), d.kb_id))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [kb for _, kb in scored[:top_n]]


def reciprocal_rank_fusion(
    rank_lists: list[list[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranks in rank_lists:
        for i, doc_id in enumerate(ranks):
            scores[doc_id] += 1.0 / (k + i + 1)
    return dict(scores)


def _sigmoid(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _rerank_cross_encoder(
    *,
    settings: BaseSettings,
    query: str,
    candidates: list[KbDoc],
    min_score: float,
) -> tuple[KbDoc | None, float, list[tuple[KbDoc, float]]]:
    pairs = [(query, f"{d.question}\n{d.answer[:500]}") for d in candidates]
    model_id = (settings.panse_rerank_model_id or "BAAI/bge-reranker-base").strip()
    scores: list[float] = []
    try:
        from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

        ce = CrossEncoder(model_id)
        scores = [float(s) for s in ce.predict(pairs)]
    except Exception:
        scores = [
            SequenceMatcher(None, query.lower(), p[1].lower()).ratio() for p in pairs
        ]
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    if not ranked:
        return None, 0.0, []
    top_doc, top_s = ranked[0]
    norm = _sigmoid(top_s) if abs(top_s) > 3 else float(top_s)
    if norm < float(min_score):
        return None, norm, ranked[:10]
    return top_doc, norm, ranked[:10]


def _resolve_embed_dir(settings: BaseSettings) -> str | None:
    """勾选专属向量且目录存在时用本地；否则 None 走轻量降级。"""
    if not getattr(settings, "panse_exclusive_embed_enabled", False):
        return None
    p = Path((settings.panse_embed_model_dir or "").strip() or "./models/panse_custom_embed/").expanduser()
    if p.is_dir() and any(p.iterdir()):
        return str(p.resolve())
    return None


def load_kb_docs(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    pool_limit: int = 400,
) -> list[KbDoc]:
    today = time.strftime("%Y-%m-%d")
    cur = conn.execute(
        """
        SELECT kb_id, question, answer, COALESCE(kb_tags, '')
        FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
          AND COALESCE(entry_type, 'normal') != 'replenish'
          AND (start_at IS NULL OR start_at <= ?)
          AND (end_at IS NULL OR end_at >= ?)
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (brand_id, shop_id, today, today, int(pool_limit)),
    )
    out: list[KbDoc] = []
    for kb_id, q, a, tags in cur.fetchall():
        out.append(KbDoc(str(kb_id), str(q), str(a), str(tags)))
    return out


def run_kb_retrieval_pipeline(
    *,
    settings: BaseSettings,
    conn: sqlite3.Connection,
    brand_id: str,
    shop_id: str,
    rewritten_query: str,
    context_block: str,
    buyer_text: str,
    min_rerank_score: float | None = None,
    rrf_k: int | None = None,
) -> KbSearchResult | None:
    """
    Filter → Hybrid(BM25+Dense) → RRF Top10 → Rerank Top1。
    失败返回 None（上层应 Jim）。
    """
    rq = (rewritten_query or "").strip()
    if not rq:
        return None
    pool_limit = int(getattr(settings, "panse_rag_pool_limit", 400) or 400)
    docs = load_kb_docs(conn, brand_id=brand_id, shop_id=shop_id, pool_limit=pool_limit)
    if not docs:
        return None

    tags = extract_kb_metadata_tags_llm(
        settings=settings,
        context_block=context_block,
        buyer_text=buyer_text,
        rewritten_query=rq,
    )
    pool = metadata_prefilter(docs, tags)
    if not pool:
        pool = docs

    embed_dir = _resolve_embed_dir(settings)
    rrf_k_val = int(rrf_k or getattr(settings, "panse_rrf_k", 60) or 60)
    bm25_ids = _bm25_rank_ids(pool, rq, top_n=60)
    dense_ids = _dense_rank_ids(pool, rq, embed_model_path=embed_dir, top_n=60)
    fused = reciprocal_rank_fusion([bm25_ids, dense_ids], k=rrf_k_val)
    top10_ids = sorted(fused.keys(), key=lambda i: fused[i], reverse=True)[:10]
    id_to_doc = {d.kb_id: d for d in pool}
    top10_docs = [id_to_doc[i] for i in top10_ids if i in id_to_doc]
    if not top10_docs:
        return None

    min_rs = float(
        min_rerank_score
        if min_rerank_score is not None
        else getattr(settings, "panse_rerank_min_score", 0.35) or 0.35
    )
    top_doc, rs, _ranked = _rerank_cross_encoder(
        settings=settings, query=rq, candidates=top10_docs, min_score=min_rs
    )
    if top_doc is None:
        return None

    alts = tuple(d.answer[:400] for d in top10_docs if d.kb_id != top_doc.kb_id)[:6]
    return KbSearchResult(
        kb_id=top_doc.kb_id,
        question=top_doc.question,
        answer=top_doc.answer,
        rerank_score=rs,
        rrf_score=float(fused.get(top_doc.kb_id, 0.0)),
        alternatives=alts,
    )


def count_kb_entries(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
) -> int:
    today = time.strftime("%Y-%m-%d")
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
          AND COALESCE(entry_type, 'normal') != 'replenish'
          AND (start_at IS NULL OR start_at <= ?)
          AND (end_at IS NULL OR end_at >= ?)
        """,
        (brand_id, shop_id, today, today),
    )
    return int(cur.fetchone()[0] or 0)


def diagnose_kb_miss(
    *,
    settings: BaseSettings,
    conn: sqlite3.Connection,
    brand_id: str,
    shop_id: str,
    rewritten_query: str,
    legacy_min_score: float,
) -> list[str]:
    """话术库未命中时生成可读的诊断行（供业务日志展示）。"""
    lines: list[str] = []
    rq = (rewritten_query or "").strip()
    n = count_kb_entries(conn, brand_id=brand_id, shop_id=shop_id)
    lines.append(f"话术库：本店有效条目数={n}")
    if n == 0:
        lines.append("话术库：当前店铺无可用条目，请检查 kb_entries 是否导入/启用")
        return lines

    min_rerank = float(getattr(settings, "panse_rerank_min_score", 0.35) or 0.35)
    lines.append(f"话术库：满血RAG重排门槛≥{min_rerank:.0%}（panse_rerank_min_score）")

    try:
        from apps.core.ai.kb_vector import has_vectors_for_shop, vector_search

        if has_vectors_for_shop(conn, brand_id=brand_id, shop_id=shop_id):
            vec_hits = vector_search(
                conn,
                brand_id=brand_id,
                shop_id=shop_id,
                query=rq,
                top_k=3,
                min_score=0.0,
                settings=settings,
            )
            if vec_hits:
                best = vec_hits[0]
                lines.append(
                    f"话术库：向量检索最高={float(best[0]):.0%}（需≥60%）"
                    f" 问句≈{str(best[2])[:40]!r}"
                )
            else:
                lines.append("话术库：向量检索无候选（或未建向量）")
        else:
            lines.append("话术库：未建向量索引，已跳过向量路")
    except Exception as e:
        lines.append(f"话术库：向量诊断异常 {e!r}")

    legacy_any = best_kb_match_by_similarity(
        conn,
        brand_id=brand_id,
        shop_id=shop_id,
        query=rq,
        min_score=0.0,
    )
    if legacy_any:
        sc, _kid, q, _a = legacy_any
        lines.append(
            f"话术库：字符串相似度最高={float(sc):.0%}（需≥{legacy_min_score:.0%}）"
            f" 问句≈{q[:40]!r}"
        )
    else:
        lines.append("话术库：字符串相似度无候选")

    return lines


_BUYER_ID_RE = re.compile(r"\b(?:tb|t)?[a-z]*\d{6,}\b", re.IGNORECASE)


def clean_query_for_retrieval(text: str) -> str:
    """v1.6.7：检索前清洗 query——剥掉买家ID/订单号等噪声（如 'tb697331180593'）。

    OCR 常把买家昵称ID混进问句，稀释向量相似度。去掉纯数字ID串与连续长数字，
    保留真正的问题文字与带单位尺寸（1.8米/60cm）。清完太短则回退原文。
    """
    t = (text or "").strip()
    if not t:
        return t
    cleaned = _BUYER_ID_RE.sub(" ", t)
    cleaned = re.sub(r"(?<![\d.])\d{6,}(?![\d.])", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。.|")
    return cleaned if len(cleaned) >= 2 else t


def _llm_pick_best_candidate(
    *,
    settings: BaseSettings,
    query: str,
    candidates: list[tuple[float, str, str, str]],
) -> tuple[str, str, str, float] | None:
    """v1.6.7：向量分数中等时，让 LLM 从 top3 候选里判断哪条真正答得上买家问题。

    candidates: [(score, kb_id, question, answer), ...]
    返回选中的 (kb_id, question, answer, score)，或 None（都不合适）。
    """
    model = (settings.model_front_desk or "").strip()
    if not model or not candidates:
        return None
    lines = []
    for i, (sc, kid, q, a) in enumerate(candidates):
        lines.append(f"[{i}] 问：{q}\n    答：{a[:120]}")
    system = (
        "你是电商客服知识库匹配判官。给你买家问题和若干候选问答，"
        "判断哪一条的『答案』能直接回答买家问题。只返回 JSON："
        '{"pick": <候选编号, 都不合适填 -1>}。'
        "宁可填 -1 也不要勉强选一个答非所问的。"
    )
    user = f"买家问题：{query}\n\n候选：\n" + "\n".join(lines)
    try:
        raw = litellm_completion_text(
            settings=settings, model=model, system=system, user=user,
            max_tokens=30, temperature=0.0,
        )
        m = re.search(r"-?\d+", _strip_json_fence(raw) or "")
        if not m:
            return None
        idx = int(m.group())
        if 0 <= idx < len(candidates):
            sc, kid, q, a = candidates[idx]
            return kid, q, a, sc
        return None
    except Exception:
        return None


def kb_retrieval_or_legacy_fallback(
    *,
    settings: BaseSettings,
    conn: sqlite3.Connection,
    brand_id: str,
    shop_id: str,
    rewritten_query: str,
    context_block: str,
    buyer_text: str,
    legacy_min_score: float = 0.35,
) -> tuple[KbSearchResult | None, str]:
    """
    检索优先级：向量搜索 → 满血 pipeline → 字符串相似度 legacy。
    返回 (result, source_tag)，source_tag 为 vector|pipeline|legacy|none
    """
    # v1.6.7：检索前清洗 query（剥买家ID/订单号噪声）
    rq_clean = clean_query_for_retrieval(rewritten_query)

    # 1. 向量搜索：门槛 0.50 取 top3；≥0.62 直采，0.50~0.62 交 LLM 从 top3 判断
    HIGH_CONF = 0.62
    FLOOR = 0.50
    try:
        from apps.core.ai.kb_vector import vector_search, has_vectors_for_shop
        if has_vectors_for_shop(conn, brand_id=brand_id, shop_id=shop_id):
            vec_hits = vector_search(
                conn,
                brand_id=brand_id,
                shop_id=shop_id,
                query=rq_clean,
                top_k=3,
                min_score=FLOOR,
                settings=settings,
            )
            if vec_hits:
                top_score, top_kid, top_q, top_a = vec_hits[0]
                if top_score >= HIGH_CONF:
                    return (
                        KbSearchResult(
                            kb_id=top_kid, question=top_q, answer=top_a,
                            rerank_score=float(top_score), rrf_score=0.0, alternatives=(),
                        ),
                        "vector",
                    )
                # 中等分：让 LLM 从 top3 里判断哪条真答得上，避免误采
                picked = _llm_pick_best_candidate(
                    settings=settings, query=rq_clean, candidates=vec_hits,
                )
                if picked is not None:
                    kid, q, a, sc = picked
                    return (
                        KbSearchResult(
                            kb_id=kid, question=q, answer=a,
                            rerank_score=float(sc), rrf_score=0.0, alternatives=(),
                        ),
                        "vector+llm",
                    )
                # LLM 判定都不合适 → 继续 pipeline/legacy
    except Exception:
        pass  # 向量搜索失败则降级

    # 2. 满血 pipeline
    hit = run_kb_retrieval_pipeline(
        settings=settings,
        conn=conn,
        brand_id=brand_id,
        shop_id=shop_id,
        rewritten_query=rq_clean,
        context_block=context_block,
        buyer_text=buyer_text,
    )
    if hit is not None:
        return hit, "pipeline"

    # 3. 字符串相似度 legacy
    legacy = best_kb_match_by_similarity(
        conn,
        brand_id=brand_id,
        shop_id=shop_id,
        query=rq_clean,
        min_score=legacy_min_score,
    )
    if legacy is None:
        return None, "none"
    score, kb_id, question, answer = legacy
    return (
        KbSearchResult(
            kb_id=kb_id,
            question=question,
            answer=answer,
            rerank_score=float(score),
            rrf_score=0.0,
            alternatives=(),
        ),
        "legacy",
    )
