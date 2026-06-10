from __future__ import annotations

import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path


def _today_iso() -> str:
    return time.strftime("%Y-%m-%d")


def retrieve_kb_snippets(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    query: str,
    limit: int = 5,
    active_only: bool = True,
) -> list[tuple[str, str]]:
    """
    SQLite kb_entries：LIKE 检索；可选仅返回在生效日期内的条目。
    """
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q[:48]}%"
    today = _today_iso()
    date_clause = ""
    params: list = [brand_id, shop_id, like, like]
    if active_only:
        date_clause = """
          AND (start_at IS NULL OR start_at <= ?)
          AND (end_at IS NULL OR end_at >= ?)
        """
        params.extend([today, today])
    params.append(int(limit))
    cur = conn.execute(
        f"""
        SELECT question, answer FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
          AND COALESCE(entry_type, 'normal') != 'replenish'
          AND (question LIKE ? OR answer LIKE ?)
          {date_clause}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params,
    )
    out: list[tuple[str, str]] = []
    for row in cur.fetchall():
        out.append((str(row[0]), str(row[1])))
    return out


def retrieve_kb_snippet_rows(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    query: str,
    limit: int = 12,
    active_only: bool = True,
) -> list[tuple[str, str, str]]:
    """返回 (kb_id, question, answer)，供严格 RAG 打分与日志节点。"""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q[:48]}%"
    today = _today_iso()
    date_clause = ""
    params: list = [brand_id, shop_id, like, like]
    if active_only:
        date_clause = """
          AND (start_at IS NULL OR start_at <= ?)
          AND (end_at IS NULL OR end_at >= ?)
        """
        params.extend([today, today])
    params.append(int(limit))
    cur = conn.execute(
        f"""
        SELECT kb_id, question, answer FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
          AND COALESCE(entry_type, 'normal') != 'replenish'
          AND (question LIKE ? OR answer LIKE ?)
          {date_clause}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params,
    )
    return [(str(r[0]), str(r[1]), str(r[2])) for r in cur.fetchall()]


def best_kb_match_by_similarity(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    query: str,
    min_score: float = 0.35,
    pool_limit: int = 2000,
    active_only: bool = True,
) -> tuple[float, str, str, str] | None:
    """
    在本地话术库中取与 query 语义最接近的一条（difflib 比率作 MVP 近似）。
    低于 min_score 视为未命中，调用方应阻断编造。
    """
    q = (query or "").strip()
    if not q:
        return None
    today = _today_iso()
    date_clause = ""
    params: list = [brand_id, shop_id]
    if active_only:
        date_clause = """
          AND (start_at IS NULL OR start_at <= ?)
          AND (end_at IS NULL OR end_at >= ?)
        """
        params.extend([today, today])
    params.append(int(pool_limit))
    cur = conn.execute(
        f"""
        SELECT kb_id, question, answer FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
          AND COALESCE(entry_type, 'normal') != 'replenish'
          {date_clause}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params,
    )
    pool: dict[str, tuple[str, str]] = {}
    for kb_id, question, answer in cur.fetchall():
        pool[str(kb_id)] = (str(question), str(answer))

    for kb_id, question, answer in retrieve_kb_snippet_rows(
        conn, brand_id=brand_id, shop_id=shop_id, query=q, limit=16, active_only=active_only
    ):
        pool[kb_id] = (question, answer)

    # 短中文 query：再扫若干子串，扩大 LIKE 召回
    if len(q) >= 4:
        step = max(2, len(q) // 6)
        for i in range(0, min(len(q), 24), step):
            chunk = q[i : i + 6].strip()
            if len(chunk) < 2:
                continue
            for kb_id, question, answer in retrieve_kb_snippet_rows(
                conn,
                brand_id=brand_id,
                shop_id=shop_id,
                query=chunk,
                limit=6,
                active_only=active_only,
            ):
                pool[kb_id] = (question, answer)

    qn = q.lower()
    best: tuple[float, str, str, str] | None = None
    for kb_id, (question, answer) in pool.items():
        a_short = (answer or "")[:520]
        s1 = SequenceMatcher(None, qn, (question or "").lower()).ratio()
        s2 = SequenceMatcher(None, qn, a_short.lower()).ratio()
        s = max(s1, s2)
        if best is None or s > best[0]:
            best = (s, kb_id, question, answer)
    if best is None or best[0] < float(min_score):
        return None
    return best


def retrieve_replenish_answer(conn: sqlite3.Connection, *, brand_id: str, shop_id: str) -> str | None:
    cur = conn.execute(
        """
        SELECT answer FROM kb_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1 AND entry_type = 'replenish'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (brand_id, shop_id),
    )
    row = cur.fetchone()
    return str(row[0]) if row else None


def retrieve_product_snippets(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    query: str,
    limit: int = 4,
) -> list[tuple[str, str, str, str]]:
    """返回 (name, product_code, size_details, customization_scope)。"""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q[:24]}%"
    cur = conn.execute(
        """
        SELECT name, product_code, COALESCE(size_details,''),
               COALESCE(customization_scope,'') FROM products
        WHERE brand_id = ? AND shop_id = ?
          AND (name LIKE ? OR product_code LIKE ? OR COALESCE(copywriting,'') LIKE ?)
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (brand_id, shop_id, like, like, like, int(limit)),
    )
    out: list[tuple[str, str, str, str]] = []
    for row in cur.fetchall():
        out.append((str(row[0]), str(row[1]), str(row[2]), str(row[3])))
    return out


def customization_requires_jim(scope: str) -> bool:
    """产品知识库「可定制范围」中含以下字样则转人工（与导入列约定一致）。"""
    s = (scope or "").strip()
    if not s:
        return False
    keys = ("超出范围", "不支持定制", "需主管", "转人工", "停自动")
    return any(k in s for k in keys)


def retrieve_active_campaigns(conn: sqlite3.Connection, *, brand_id: str, shop_id: str) -> list[tuple[str, str, str]]:
    """返回 (name, start_at, end_at) 当前日期有效。"""
    today = _today_iso()
    cur = conn.execute(
        """
        SELECT name, start_at, end_at FROM campaigns
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
          AND start_at <= ? AND end_at >= ?
        ORDER BY start_at DESC
        """,
        (brand_id, shop_id, today, today),
    )
    return [(str(r[0]), str(r[1]), str(r[2])) for r in cur.fetchall()]


def retrieve_gallery_hints(conn: sqlite3.Connection, *, brand_id: str, shop_id: str, limit: int = 12) -> list[str]:
    lines: list[str] = []
    cur = conn.execute(
        """
        SELECT title, local_path, taobao_url FROM gallery_entries
        WHERE brand_id = ? AND shop_id = ? AND enabled = 1
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (brand_id, shop_id, int(limit)),
    )
    for row in cur.fetchall():
        title, local_p, url = str(row[0]), row[1], row[2]
        parts = [title]
        if local_p:
            parts.append(f"本地:{local_p}")
        if url:
            parts.append(f"链接:{url}")
        lines.append(" | ".join(parts))
    return lines


def format_rag_block(snips: list[tuple[str, str]]) -> str:
    if not snips:
        return "（暂无匹配知识点）"
    lines = []
    for i, (qu, an) in enumerate(snips, 1):
        lines.append(f"[{i}] 问：{qu}\n答：{an}")
    return "\n\n".join(lines)


def format_product_block(rows: list[tuple[str, str, str, str]]) -> str:
    if not rows:
        return ""
    lines = []
    for name, code, sizes, cust in rows:
        extra = f"；定制说明：{cust}" if (cust or "").strip() else ""
        lines.append(f"- {name}（编码 {code}）规格：{sizes}{extra}")
    return "【产品库匹配】\n" + "\n".join(lines)


def format_campaign_block(camps: list[tuple[str, str, str]]) -> str:
    if not camps:
        return ""
    lines = [f"- {n}（{a} ~ {b}）" for n, a, b in camps]
    return "【当前生效活动】\n" + "\n".join(lines)


def format_gallery_block(lines: list[str]) -> str:
    if not lines:
        return ""
    return "【可引用图库】\n" + "\n".join(lines)
