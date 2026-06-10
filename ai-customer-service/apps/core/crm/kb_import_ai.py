"""
Excel / 大批量话术导入：经「深度分析模型」结构化整理（问法可合并/拆分），
「答」一律从原文按 source_indices 回填；在不改变文字内容的前提下，
允许对“单句答句”做最小断句（仅插入“。”/“，”）以模拟人工停顿。

宽表（A=涉及产品锚定，E/F/G=分析线索）：分块调用模型生成真实问法/答句/类型，
并把批注写回 E、F 列（输出为新 xlsx 副本）。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apps.core.ai.llm_client import deep_analysis_completion
from apps.core.ai.answer_splitter import maybe_split_answers_for_import
from apps.core.configs.base_settings import BaseSettings

MAX_AI_SOURCE_ROWS = 120
WIDE_CHUNK_SIZE = 10


@dataclass(frozen=True, slots=True)
class KbImportAIOutcome:
    rows: list[dict[str, str | None]]
    advice: str
    used_ai: bool
    note: str


@dataclass(frozen=True, slots=True)
class KbWideImportAIOutcome:
    rows: list[dict[str, str | None]]
    advice: str
    used_ai: bool
    note: str
    marks: list[tuple[int, str, str]]


def _parse_json_object(raw: str) -> dict[str, Any]:
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        t = m.group(1).strip()
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return obj


def _materialize_from_sources(
    entries: list[dict[str, Any]],
    numbered: list[dict[str, Any]],
) -> list[dict[str, str | None]]:
    """根据 source_indices 从原文回填 answer，并校验同一组内答句一致。"""
    by_idx = {int(r["idx"]): r for r in numbered}
    out: list[dict[str, str | None]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        q = str(e.get("question") or "").strip()
        sis_raw = e.get("source_indices")
        if not q or not isinstance(sis_raw, list) or not sis_raw:
            continue
        try:
            sis = [int(x) for x in sis_raw]
        except (TypeError, ValueError):
            continue
        valid = [i for i in sis if i in by_idx]
        if not valid:
            continue
        ref = by_idx[valid[0]]
        ans = str(ref.get("answer") or "").strip()
        if not ans:
            continue
        if any(str(by_idx[i].get("answer") or "").strip() != ans for i in valid):
            continue
        ets = [
            str(by_idx[i].get("entry_type") or "normal").strip() or "normal"
            for i in valid
        ]
        et = "replenish" if "replenish" in ets else "normal"
        sa = ref.get("start_at")
        ea = ref.get("end_at")
        out.append(
            {
                "question": q,
                "answer": ans,
                "entry_type": et,
                "start_at": sa,
                "end_at": ea,
            }
        )
    return out


def _deterministic_merge(rows: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
    """答句完全一致时合并问法；不拆分复杂问句。"""
    groups: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    order: list[str] = []
    for r in rows:
        a = (r.get("answer") or "").strip()
        if not a:
            continue
        if a not in groups:
            order.append(a)
        groups[a].append(r)
    out: list[dict[str, str | None]] = []
    for ans in order:
        bucket = groups[ans]
        qs: list[str] = []
        seen: set[str] = set()
        for r in bucket:
            q = (r.get("question") or "").strip()
            if q and q not in seen:
                seen.add(q)
                qs.append(q)
        if not qs:
            continue
        etypes = [
            str(r.get("entry_type") or "normal").strip() or "normal" for r in bucket
        ]
        et = "replenish" if "replenish" in etypes else "normal"
        ref = bucket[0]
        out.append(
            {
                "question": " / ".join(qs),
                "answer": ans,
                "entry_type": et,
                "start_at": ref.get("start_at"),
                "end_at": ref.get("end_at"),
            }
        )
    return out


def analyze_kb_rows_with_llm(
    *,
    settings: BaseSettings,
    rows: list[dict[str, str | None]],
) -> KbImportAIOutcome:
    """
    rows 为已解析的问答行（无 idx）。
    使用「深度分析模型」；答句始终从原文行回填。
    """
    if not rows:
        return KbImportAIOutcome([], "", False, "")

    tail_note = ""
    head = rows[:MAX_AI_SOURCE_ROWS]
    tail = rows[MAX_AI_SOURCE_ROWS:]
    if tail:
        tail_note = (
            f"\n\n（单次 AI 分析上限 {MAX_AI_SOURCE_ROWS} 行，"
            f"余下 {len(tail)} 行将原样追加导入，未参与合并/拆分。）"
        )

    numbered: list[dict[str, Any]] = []
    for i, r in enumerate(head):
        numbered.append(
            {
                "idx": i,
                "question": (r.get("question") or "").strip(),
                "answer": (r.get("answer") or "").strip(),
                "entry_type": (r.get("entry_type") or "normal").strip() or "normal",
                "start_at": r.get("start_at"),
                "end_at": r.get("end_at"),
            }
        )

    payload = json.dumps(numbered, ensure_ascii=False)
    system = (
        "你是电商客服知识库整理助手。输入为多行「问法」「答」原文（含 idx）。\n"
        "任务：\n"
        "1）合并重复：若多行 **答** 逐字完全相同，合并为一条「问法」（可概括或并列关键词），"
        "用 source_indices 列出合并所依据的全部行号。\n"
        "2）拆分复杂问法：若某行「问法」明显包含多个独立问题，拆成多行；每行 source_indices "
        "仅含原行 idx，**答** 只能沿用该行原文（即同一 idx 可出现多次对应多个子问法）。\n"
        "硬约束：禁止改写、润色、删减任何「答」的字符；输出中不要填写 answer 字段，"
        "只用 source_indices 引用来源行，由系统从原文回填答句。\n"
        "3）话术意见：在 advice 中用 Markdown 给出优化建议（语气、结构、覆盖盲区等），"
        "不得编造已修改客户「答」原文的事实。\n"
        "只输出一个 JSON 对象，格式：\n"
        '{"entries":[{"question":"…","source_indices":[0,2]}…],"advice":"…"}'
    )
    user = "【待整理数据】\n" + payload

    note = ""
    used_ai = False
    advice = ""
    try:
        raw = deep_analysis_completion(
            settings=settings,
            system=system,
            user=user,
            max_tokens=8192,
            temperature=0.3,
        )
        obj = _parse_json_object(raw)
        used_ai = True
        advice = str(obj.get("advice") or "").strip()
        raw_entries = obj.get("entries")
        if not isinstance(raw_entries, list):
            raw_entries = []
        built = _materialize_from_sources(raw_entries, numbered)
        note = ""
        if not built:
            built = _deterministic_merge(head)
            note = "模型输出无法校验，已改用「答句完全一致」规则合并。"
        final_rows = built + tail
        try:
            final_rows = maybe_split_answers_for_import(
                settings=settings,
                rows=final_rows,
                ratio=0.35,  # per user request
            )
        except Exception:
            # Best-effort: never block KB import.
            pass
        return KbImportAIOutcome(
            rows=final_rows,
            advice=(advice + tail_note).strip(),
            used_ai=used_ai,
            note=note,
        )
    except Exception as e:
        built = _deterministic_merge(head)
        final_rows = built + tail
        try:
            final_rows = maybe_split_answers_for_import(
                settings=settings,
                rows=final_rows,
                ratio=0.35,  # per user request
            )
        except Exception:
            pass
        err = str(e)
        advice = (
            f"**AI 分析未成功**（{err}）。已改用本地规则：仅合并「答」完全相同的行。"
            + tail_note
        )
        return KbImportAIOutcome(
            rows=final_rows,
            advice=advice.strip(),
            used_ai=False,
            note=err,
        )


def _wide_fallback_row(r: dict[str, str | None]) -> dict[str, str | None]:
    """模型失败时的保守拼装（仍带产品锚在 kb_tags）。"""
    a = (r.get("product_anchor") or "").strip()
    b = (r.get("legacy_b") or "").strip()
    c = (r.get("legacy_c") or "").strip()
    d = (r.get("legacy_d") or "").strip()
    e = (r.get("hint_e") or "").strip()
    f = (r.get("hint_f") or "").strip()
    g = (r.get("hint_g") or "").strip()
    q = " | ".join(x for x in (e, f, g, b, d) if x) or f"关于「{a}」的咨询"
    ans = c if len(c) >= 8 else "（请在本行根据产品与场景补充可直接发送客户的答句原文）"
    et = d or "normal"
    return {
        "question": q[:2000],
        "answer": ans[:8000],
        "entry_type": (et or "normal")[:120],
        "kb_tags": a[:2000],
        "start_at": None,
        "end_at": None,
    }


def analyze_kb_wide_hints_with_llm(
    *,
    settings: BaseSettings,
    wide_rows: list[dict[str, str | None]],
    chunk_size: int = WIDE_CHUNK_SIZE,
    on_progress: Callable[[int, int], None] | None = None,
) -> KbWideImportAIOutcome:
    """
    wide_rows：parse_wide_kb_rows 输出（含 product_anchor、hint_e/f/g、sheet_row）。
    逐块调用深度模型；每行生成真实问法/答句/类型；kb_tags=product_anchor 原文。
    marks: (excel_row, mark_e, mark_f) 供写回表格。
    """
    if not wide_rows:
        return KbWideImportAIOutcome([], "", False, "", [])

    all_out: list[dict[str, str | None]] = []
    all_marks: list[tuple[int, str, str]] = []
    advice_parts: list[str] = []
    used_any = False
    note = ""

    total = len(wide_rows)
    for off in range(0, total, chunk_size):
        chunk = wide_rows[off : off + chunk_size]
        if on_progress:
            on_progress(min(off + len(chunk), total), total)
        payload = []
        for j, r in enumerate(chunk):
            try:
                sr = int(str(r.get("sheet_row") or "0"))
            except ValueError:
                sr = off + j + 2
            payload.append(
                {
                    "chunk_idx": j,
                    "sheet_row": sr,
                    "product_anchor": (r.get("product_anchor") or "").strip(),
                    "legacy_b": (r.get("legacy_b") or "").strip(),
                    "legacy_c": (r.get("legacy_c") or "").strip(),
                    "legacy_d": (r.get("legacy_d") or "").strip(),
                    "hint_e": (r.get("hint_e") or "").strip(),
                    "hint_f": (r.get("hint_f") or "").strip(),
                    "hint_g": (r.get("hint_g") or "").strip(),
                }
            )

        system = (
            "你是电商家具客服知识库结构化专家。用户表格中：\n"
            "• **product_anchor**（A 列「涉及产品」）是产品锚定，必须原样出现在输出的 kb_tags 字段中，"
            "禁止改写成别的 SKU/系列名，禁止把甲产品话术套到乙产品上。\n"
            "• B/C/D 列可能是错误映射的分类标签，不要当成标准问法/答句；应参考 **hint_e / hint_f / hint_g** 以及 B/C/D 的语义线索，"
            "推断客户**真实会问的那句话**（question）与坐席**可直接发送的答句原文**（answer）。\n"
            "• entry_type：简短类目（如：物流、材质、尺寸、售后、库存、发票、活动），小写英文或中文短语均可。\n"
            "• mark_e：本行数据质量一句话批注（是否缺信息、是否需人工复核）。\n"
            "• mark_f：建议子类或关键词（便于后续归类）。\n"
            "硬约束：answer 中若提及具体产品名，必须与 product_anchor 一致或为其中明确包含的子款；"
            "若线索不足，answer 里写清「需人工根据链接确认」类表述，不要编造参数。\n"
            "只输出 JSON："
            '{"items":[{"sheet_row":2,"question":"…","answer":"…","entry_type":"…","kb_tags":"…与product_anchor一致…",'
            '"mark_e":"…","mark_f":"…"}]}'
        )
        user = "【本批待分析行】\n" + json.dumps(payload, ensure_ascii=False)

        try:
            raw = deep_analysis_completion(
                settings=settings,
                system=system,
                user=user,
                max_tokens=8192,
                temperature=0.35,
            )
            obj = _parse_json_object(raw)
            used_any = True
            items = obj.get("items")
            if not isinstance(items, list):
                items = []
            by_sr = {int(p["sheet_row"]): p for p in payload if p.get("sheet_row") is not None}
            written_srs: set[int] = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                try:
                    sr = int(it.get("sheet_row"))
                except (TypeError, ValueError):
                    continue
                if sr in written_srs:
                    continue
                q = str(it.get("question") or "").strip()
                a = str(it.get("answer") or "").strip()
                et = str(it.get("entry_type") or "normal").strip() or "normal"
                tag = str(it.get("kb_tags") or "").strip()
                anchor = ""
                if sr in by_sr:
                    anchor = str(by_sr[sr].get("product_anchor") or "").strip()
                if anchor and anchor not in tag and tag != anchor:
                    tag = anchor
                if not tag:
                    tag = anchor
                me = str(it.get("mark_e") or "").strip() or "（无批注）"
                mf = str(it.get("mark_f") or "").strip() or "（无）"
                if q and a:
                    all_out.append(
                        {
                            "question": q[:2000],
                            "answer": a[:8000],
                            "entry_type": et[:120],
                            "kb_tags": (tag or anchor)[:2000],
                            "start_at": None,
                            "end_at": None,
                        }
                    )
                    all_marks.append((sr, me[:2000], mf[:2000]))
                    written_srs.add(sr)
                else:
                    fb = _wide_fallback_row(
                        next(
                            (
                                x
                                for x in chunk
                                if int(str(x.get("sheet_row") or 0)) == sr
                            ),
                            chunk[0],
                        )
                    )
                    all_out.append(fb)
                    all_marks.append((sr, "模型缺字段已回退拼装", mf[:2000]))
                    written_srs.add(sr)
            for r in chunk:
                try:
                    sr0 = int(str(r.get("sheet_row") or "0"))
                except ValueError:
                    continue
                if sr0 and sr0 not in written_srs:
                    all_out.append(_wide_fallback_row(r))
                    all_marks.append((sr0, "模型未返回本行", "回退"))
            adv = str(obj.get("chunk_advice") or obj.get("advice") or "").strip()
            if adv:
                advice_parts.append(adv)
        except Exception as e:
            note = str(e)
            for r in chunk:
                all_out.append(_wide_fallback_row(r))
                try:
                    sr = int(str(r.get("sheet_row") or "0"))
                except ValueError:
                    sr = 0
                if sr:
                    all_marks.append((sr, f"AI失败:{note[:80]}", "回退"))

    adv_text = "\n\n".join(advice_parts).strip()
    if not adv_text:
        adv_text = "（分块分析已完成；未单独汇总 chunk_advice。）"
    return KbWideImportAIOutcome(
        rows=all_out,
        advice=adv_text,
        used_ai=used_any,
        note=note,
        marks=all_marks,
    )
