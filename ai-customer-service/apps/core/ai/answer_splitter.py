"""
Answer splitter for KB import.

Purpose:
Randomly (~35%) split a "single-sentence" reply into two sentences by inserting
one punctuation between two continuous substrings, so the executor sends it in
multiple timed segments (more human-like).

Strict constraints:
- Never delete/replace characters; only insert join punctuation between parts.
- Only attempt when the reply would otherwise be treated as one segment.
- Skip short greetings / placeholders (e.g. "您好，在的呢").
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass

from apps.core.ai.llm_client import deep_analysis_completion
from apps.core.configs.base_settings import BaseSettings


_SENTENCE_SPLIT = re.compile(r"([,，。.!！？?；;：:])")

# Hard skip for very short / greeting-ish placeholders.
_NO_SPLIT_SUBSTRINGS = (
    "您好，在的呢",
    "您好，在的哦",
    "收到",
    "嗯嗯",
    "好的呢",
    "在的呢",
)


def _crc32_int(s: str) -> int:
    return zlib.crc32(s.encode("utf-8", errors="ignore")) & 0xFFFFFFFF


def _count_sentence_segments(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    parts = _SENTENCE_SPLIT.split(t)
    segments: list[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if _SENTENCE_SPLIT.fullmatch(p):
            buf += p
            if buf.strip():
                segments.append(buf.strip())
            buf = ""
        else:
            buf = (buf + p).strip() if buf else p.strip()
    if buf.strip():
        segments.append(buf.strip())
    return len(segments)


def _is_eligible_base(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if any(x in t for x in _NO_SPLIT_SUBSTRINGS):
        return False
    if len(t) < 10:
        return False

    # Only attempt when it would be treated as ONE segment.
    return _count_sentence_segments(t) <= 1


def should_attempt_split(*, key: str, ratio: float) -> bool:
    r = float(ratio)
    if r <= 0.0:
        return False
    if r >= 1.0:
        return True
    bucket = int(r * 100.0)  # 0~100
    return (_crc32_int(key) % 100) < bucket


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        return m.group(1).strip()
    return s


@dataclass(frozen=True, slots=True)
class SplitResult:
    first: str
    second: str
    join: str  # "。" or "，"

    def apply(self, original: str) -> str:
        _ = original
        return f"{self.first}{self.join}{self.second}"


def _verify_split(original: str, first: str, second: str, join: str) -> bool:
    o = (original or "").strip()
    if not o or not first or not second:
        return False
    if (first + second) != o:
        return False
    if join not in ("。", "，"):
        return False
    return True


def _json_escape_for_prompt(s: str) -> str:
    # Keep it simple; we still validate locally.
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "")


def analyze_splits_with_llm(
    *,
    settings: BaseSettings,
    candidates: list[tuple[int, str]],
) -> dict[int, SplitResult | None]:
    """
    candidates: list of (idx, text). idx is local index within import rows.
    """
    if not candidates:
        return {}

    system = (
        "你是客服话术断句助手。"
        "你要把每条 reply_text 拆成两句以模拟人工。"
        "严格约束："
        "1) 不得删减、改写、替换任何文字字符；只能在两段之间插入一个标点 join（只能是“。”或“，”）。"
        "2) first 和 second 必须分别是原字符串（去首尾空格后）的连续子串，并满足 first+second==原字符串。"
        "3) 若不适合拆分，返回 ok=false，其它字段可为空字符串。"
        "只输出一个 JSON 对象："
        '{"splits":[{"idx":0,"ok":true,"first":"...","second":"...","join":"。"}]}'
    )

    payload_lines = []
    for idx, t in candidates:
        payload_lines.append(f"- idx={idx} text={_json_escape_for_prompt(t.strip())}")
    user = "待处理回复列表：\n" + "\n".join(payload_lines)

    raw = deep_analysis_completion(
        settings=settings,
        system=system,
        user=user,
        max_tokens=900,
        temperature=0.2,
    )

    try:
        obj = json.loads(_strip_json_fence(raw))
    except Exception:
        return {idx: None for idx, _ in candidates}

    splits = obj.get("splits") if isinstance(obj, dict) else None
    if not isinstance(splits, list):
        return {idx: None for idx, _ in candidates}

    by_idx = {idx: t for idx, t in candidates}
    out: dict[int, SplitResult | None] = {idx: None for idx, _ in candidates}

    for it in splits:
        if not isinstance(it, dict):
            continue
        idx = it.get("idx")
        if not isinstance(idx, int) or idx not in by_idx:
            continue
        if not bool(it.get("ok")):
            out[idx] = None
            continue
        first = it.get("first")
        second = it.get("second")
        join = it.get("join")
        if not isinstance(first, str) or not isinstance(second, str) or not isinstance(join, str):
            out[idx] = None
            continue
        original = by_idx[idx]
        if not _verify_split(original, first.strip(), second.strip(), join.strip()):
            out[idx] = None
            continue
        out[idx] = SplitResult(first=first.strip(), second=second.strip(), join=join.strip())

    return out


def maybe_split_answers_for_import(
    *,
    settings: BaseSettings,
    rows: list[dict[str, str | None]],
    ratio: float = 0.35,
    max_ai_candidates: int = 40,
) -> list[dict[str, str | None]]:
    """
    Update rows answer in place-like manner and return a new list.
    """
    if not rows:
        return []

    eligible: list[tuple[int, str]] = []
    for i, r in enumerate(rows):
        a = r.get("answer") if isinstance(r, dict) else None
        if not isinstance(a, str):
            continue
        if not _is_eligible_base(a):
            continue

        q = r.get("question") if isinstance(r, dict) else None
        key = f"{q or ''}||{a.strip()}"
        if not should_attempt_split(key=key, ratio=ratio):
            continue
        eligible.append((i, a.strip()))
        if len(eligible) >= max_ai_candidates:
            break

    if not eligible:
        return [dict(r) for r in rows]

    out_rows = [dict(r) for r in rows]
    CHUNK = 10
    for start in range(0, len(eligible), CHUNK):
        chunk = eligible[start : start + CHUNK]
        splits = analyze_splits_with_llm(settings=settings, candidates=chunk)
        for idx, _text in chunk:
            res = splits.get(idx)
            if res is None:
                continue
            original = str(out_rows[idx].get("answer") or "")
            out_rows[idx]["answer"] = res.apply(original)

    return out_rows

