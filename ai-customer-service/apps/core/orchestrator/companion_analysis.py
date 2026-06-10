"""
AI 陪伴对话：聚合健康日志 + 深度模型分析（修复建议、优化点、完整 Plan）。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from apps.core.runtime_paths import bundle_root, project_root

from apps.core.ai.llm_client import (
    deep_analysis_api_configured,
    deep_analysis_completion,
)
from apps.core.configs.base_settings import BaseSettings, load_base_settings
from apps.core.crm.db import connect, init_db
from apps.core.orchestrator.companion_reports import (
    generate_bug_fix_report,
    generate_optimization_insight,
)

Role = Literal["user", "assistant"]
CompanionMode = Literal["light_fix", "deep_check", "optimization"]


@dataclass(slots=True)
class ChatTurn:
    role: Role
    content: str


@dataclass(slots=True)
class CompanionEvidence:
    hours: int
    total_logs: int
    event_counts: Counter[str]
    samples: list[dict[str, Any]] = field(default_factory=list)
    session_event_counts: Counter[str] = field(default_factory=Counter)
    console_excerpt: str = ""

    def to_prompt_block(self, *, max_chars: int = 14000) -> str:
        lines = [
            f"统计窗口：过去 {self.hours} 小时",
            f"system_health_logs 条数：{self.total_logs}",
            "",
            "【事件计数】",
        ]
        for k, v in self.event_counts.most_common(35):
            lines.append(f"- {k}: {v}")
        if self.session_event_counts:
            lines.extend(["", "【session_events 计数（近窗）】"])
            for k, v in self.session_event_counts.most_common(20):
                lines.append(f"- {k}: {v}")
        lines.extend(["", "【代表性样本（时间 / 类型 / 摘要）】"])
        if not self.samples:
            lines.append("（无样本；请确认已开启「AI 陪伴监控」并有接待流量）")
        else:
            for s in self.samples[:40]:
                lines.append(
                    f"- {s.get('created_at', '')} | {s.get('event_type', '')} | "
                    f"{s.get('summary', '')[:280]}"
                )
        console = (self.console_excerpt or "").strip()
        if console:
            lines.extend(["", "【今日接待动态 / 控制台摘录（最近）】", ""])
            for ln in console.splitlines()[-180:]:
                lines.append(ln[:500])
        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[: max_chars - 80] + "\n…（证据已截断）"
        return text


def _since_iso(hours: int) -> str:
    dt = datetime.now() - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def gather_companion_evidence(
    db_path: Path,
    *,
    hours: int = 72,
    console_excerpt: str = "",
    log_limit: int = 3000,
) -> CompanionEvidence:
    since = _since_iso(hours)
    conn = connect(db_path)
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT created_at, event_type, payload_json
            FROM system_health_logs
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (since, log_limit),
        ).fetchall()
        ev_rows: list[tuple[str, int]] = []
        try:
            ev_rows = conn.execute(
                """
                SELECT event_type, COUNT(*) FROM session_events
                WHERE created_at >= ?
                GROUP BY event_type
                ORDER BY COUNT(*) DESC
                LIMIT 25
                """,
                (since,),
            ).fetchall()
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()

    ctr: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    priority = (
        "pipeline_exception",
        "risk_intercept_llm",
        "risk_intercept_outbound",
        "strategy_takeover",
        "kb_miss",
        "jim_intercept",
    )

    def _summary(et: str, pj: str) -> str:
        try:
            obj = json.loads(pj) if pj else {}
        except Exception:
            obj = {}
        if not isinstance(obj, dict):
            return pj[:200] if pj else ""
        if et in ("pipeline_exception",):
            return str(obj.get("error", obj))[:400]
        if "reason" in obj:
            return str(obj.get("reason", ""))[:400]
        if "segment_preview" in obj:
            return f"{obj.get('reason', '')} | preview={obj.get('segment_preview', '')}"[:400]
        return json.dumps(obj, ensure_ascii=False)[:400]

    for created_at, et, pj in rows:
        et_s = str(et)
        ctr[et_s] += 1

    # 优先采集异常类样本，再补其它
    seen_types: set[str] = set()
    for created_at, et, pj in rows:
        et_s = str(et)
        if et_s in priority or any(x in et_s for x in ("intercept", "exception", "takeover")):
            samples.append(
                {
                    "created_at": str(created_at),
                    "event_type": et_s,
                    "summary": _summary(et_s, str(pj or "")),
                }
            )
            seen_types.add(et_s)
        if len(samples) >= 28:
            break
    if len(samples) < 12:
        for created_at, et, pj in rows:
            et_s = str(et)
            if et_s in seen_types:
                continue
            samples.append(
                {
                    "created_at": str(created_at),
                    "event_type": et_s,
                    "summary": _summary(et_s, str(pj or "")),
                }
            )
            if len(samples) >= 20:
                break

    return CompanionEvidence(
        hours=hours,
        total_logs=len(rows),
        event_counts=ctr,
        samples=samples,
        session_event_counts=Counter({str(a): int(b) for a, b in ev_rows}),
        console_excerpt=(console_excerpt or "")[-14000:],
    )


def _format_history(turns: list[ChatTurn], *, max_turns: int = 24) -> str:
    chunk = turns[-max_turns:]
    lines: list[str] = []
    for t in chunk:
        label = "用户" if t.role == "user" else "助手"
        lines.append(f"【{label}】\n{t.content.strip()}\n")
    return "\n".join(lines)


_COMPANION_SYSTEM = """你是「AI 工作台 / 千牛自动接待」的运维陪伴助手。
你只能依据用户提供的运行日志证据、代码摘要与对话上下文作答；不要编造未出现的错误。
输出使用 Markdown，结构清晰、可执行。
禁止建议：改价、代付、绕过平台风控、批量刷评等违规操作。"""

_GREETINGS: dict[CompanionMode, str] = {
    "light_fix": (
        "你好！我是 **AI 陪伴助手**（轻度问题修复模式）。\n\n"
        "请先描述你这次遇到的问题或现象（例如：有叮咚但不弹窗、OCR 为空、点错会话等）。"
        "我会结合本机运行日志，给出**可执行的修复步骤**；你可以随时追问、补充细节。\n\n"
        "对话结束后可点击 **「生成简短摘要」**，下次会基于摘要继续，不必重读超长日志。"
    ),
    "deep_check": (
        "你好！当前为 **深度检查模式**。\n\n"
        "我会对**整个代码仓库**（apps/configs/tests 等）与近期**全部运行日志**做扫描，"
        "输出系统性诊断报告。完整扫描结果保存在 `data/companion/repo_scan/`。\n\n"
        "你可以先说明重点怀疑的模块，也可等我检查完再讨论。"
    ),
    "optimization": (
        "你好！当前为 **功能优化模式**。\n\n"
        "我会根据近期运行数据与（若已有）深度检查结果，提出**针对性优化建议**。"
        "请告诉我你最想改进的方向，或直接让我分析日志。\n\n"
        "支持多轮讨论；结束后可生成简短摘要供下次沿用。"
    ),
}

_KEY_SOURCE_PATHS = (
    "apps/core/orchestrator/event_pipeline.py",
    "apps/core/channels/qianniu/bring_to_front.py",
    "apps/core/channels/qianniu/window_ops.py",
    "apps/core/channels/qianniu/session_list_unread.py",
    "apps/core/audio/process_audio_listener.py",
    "apps/core/ai/input_quality_gate.py",
    "configs/query_rewrite.yaml",
    "configs/base_settings.yaml",
)


def greeting_for_mode(mode: CompanionMode) -> str:
    return _GREETINGS.get(mode, _GREETINGS["light_fix"])


def gather_code_snapshot(*, max_chars: int = 24000) -> str:
    root = bundle_root()
    blocks: list[str] = ["【关键代码与配置摘录】", ""]
    used = 0
    for rel in _KEY_SOURCE_PATHS:
        p = root / rel
        if not p.is_file():
            alt = project_root() / rel
            p = alt if alt.is_file() else p
        if not p.is_file():
            blocks.append(f"- `{rel}`：（未找到文件）")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            blocks.append(f"- `{rel}`：读取失败 {e!r}")
            continue
        chunk = text[: min(5000, len(text))]
        block = f"### `{rel}`\n```\n{chunk}\n```\n"
        if used + len(block) > max_chars:
            blocks.append(f"…（后续文件已截断，上限 {max_chars} 字）")
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


def _opening_user_prompt(evidence: CompanionEvidence) -> str:
    return (
        "请根据以下运行证据，输出首轮分析报告。\n\n"
        "必须包含以下章节（标题用 ##）：\n"
        "1. ## 观察到的 Bug 与异常（按严重程度排序，无样本则说明原因）\n"
        "2. ## 修改建议（与上文 Bug 对应，指向配置项/YAML/阈值/校准步骤等可操作项）\n"
        "3. ## 两条新的优化建议（必须且仅有 2 条，标题分别为「优化 1」「优化 2」）\n\n"
        f"{evidence.to_prompt_block()}"
    )


def _fallback_opening(evidence: CompanionEvidence, db_path: Path) -> str:
    bug_md = generate_bug_fix_report(db_path, hours=min(72, evidence.hours))
    opt_md = generate_optimization_insight(db_path, days=7)
    return (
        "## 观察到的 Bug 与异常（模板汇总，未调用大模型）\n\n"
        f"{bug_md}\n\n"
        "---\n\n"
        "## 修改建议\n\n"
        "请在「设置中心」配置 **AI 陪伴与深度分析模型** 及对应 API 密钥后，"
        "点击「开始」重新生成，可获得针对你本机日志的逐条修复建议。\n\n"
        "---\n\n"
        "## 两条新的优化建议\n\n"
        "**优化 1**：开启 AI 陪伴监控并运行至少一个接待日，确保 `system_health_logs` 有足够样本。\n\n"
        "**优化 2**：对高频 `risk_intercept` / `pipeline_exception` 事件，优先重校 `session_list_rect` 与 `ocr_chat_rect`。\n\n"
        "---\n\n"
        f"<details><summary>近 7 天粗览（节选）</summary>\n\n{opt_md[:3500]}\n</details>"
    )


def generate_opening_analysis(
    db_path: Path,
    *,
    hours: int = 72,
    console_excerpt: str = "",
    settings: BaseSettings | None = None,
) -> tuple[str, CompanionEvidence]:
    evidence = gather_companion_evidence(
        db_path, hours=hours, console_excerpt=console_excerpt
    )
    st = settings or load_base_settings()
    if not deep_analysis_api_configured(st):
        return _fallback_opening(evidence, db_path), evidence
    try:
        text = deep_analysis_completion(
            settings=st,
            system=_COMPANION_SYSTEM,
            user=_opening_user_prompt(evidence),
            max_tokens=8192,
            temperature=0.25,
        )
        return (text or "").strip() or _fallback_opening(evidence, db_path), evidence
    except Exception as e:
        body = _fallback_opening(evidence, db_path)
        return (
            f"> 深度模型调用失败：`{e!r}`\n\n{body}",
            evidence,
        )


def build_llm_context(
    *,
    mode: CompanionMode,
    history: list[ChatTurn],
    evidence: CompanionEvidence | None,
    session_summary: str = "",
    console_excerpt: str = "",
    include_code: bool = False,
    evidence_max_chars: int = 6000,
) -> str:
    """拼装 LLM 上下文：优先 ai_retrieval_context.md + 本场对话 + 精简证据。"""
    from apps.core.orchestrator.companion_storage import (
        load_ai_retrieval_context,
        scan_full_repository,
    )

    parts: list[str] = []
    ai_mem = load_ai_retrieval_context()
    if ai_mem.strip():
        parts.extend(["【AI 精简记忆库 · ai_retrieval_context.md】", ai_mem, ""])
    sm = (session_summary or "").strip()
    if sm and sm not in (ai_mem or ""):
        parts.extend(["【当前模式最近摘要】", sm, ""])
    if history:
        parts.extend(["【本次对话（最近轮次）】", _format_history(history, max_turns=16), ""])
    if evidence is not None:
        ev = evidence
        if console_excerpt.strip():
            ev = CompanionEvidence(
                hours=ev.hours,
                total_logs=ev.total_logs,
                event_counts=ev.event_counts,
                samples=ev.samples,
                session_event_counts=ev.session_event_counts,
                console_excerpt=console_excerpt[-8000:],
            )
        parts.extend(["【运行证据（精简）】", ev.to_prompt_block(max_chars=evidence_max_chars), ""])
    if include_code:
        scan = scan_full_repository()
        parts.extend(
            [
                f"【全仓代码扫描】共 {scan.file_count} 文件；"
                f"完整：`{scan.full_scan_path}`",
                scan.llm_excerpt,
                "",
            ]
        )
    parts.append(f"【当前模式】{mode}")
    return "\n".join(parts)


def generate_deep_check_report(
    db_path: Path,
    *,
    console_excerpt: str = "",
    session_summary: str = "",
    settings: BaseSettings | None = None,
) -> tuple[str, CompanionEvidence]:
    evidence = gather_companion_evidence(
        db_path, hours=168, console_excerpt=console_excerpt, log_limit=8000
    )
    scan = scan_full_repository()
    code = scan.llm_excerpt
    scan_note = (
        f"\n\n> 全仓共扫描 **{scan.file_count}** 个文件；"
        f"完整内容：`data/companion/repo_scan/latest_full_scan.txt`\n"
    )
    st = settings or load_base_settings()
    if not deep_analysis_api_configured(st):
        return (
            "## 深度检查（模板）\n\n"
            "请配置深度分析模型后重新运行。\n\n"
            f"{evidence.to_prompt_block(max_chars=12000)}\n\n---\n\n"
            f"{code[:12000]}{scan_note}",
            evidence,
        )
    ctx = build_llm_context(
        mode="deep_check",
        history=[],
        evidence=evidence,
        session_summary=session_summary,
        console_excerpt=console_excerpt,
        include_code=True,
        evidence_max_chars=12000,
    )
    user = (
        "请执行 **深度检查**，输出 Markdown 报告。\n\n"
        "必须包含：\n"
        "## 链路健康度总览\n## 代码与配置风险点\n## 日志异常清单（按严重度）\n"
        "## 根因推断\n## 建议修复优先级\n\n"
        f"{ctx}\n\n{code}{scan_note}"
    )
    try:
        text = deep_analysis_completion(
            settings=st,
            system=_COMPANION_SYSTEM + "\n当前任务：深度检查（全仓代码+全量日志）。",
            user=user,
            max_tokens=8192,
            temperature=0.2,
        ).strip()
        return text or "（深度检查未返回内容）", evidence
    except Exception as e:
        return f"深度检查失败：`{e!r}`", evidence


def generate_optimization_report(
    db_path: Path,
    *,
    console_excerpt: str = "",
    session_summary: str = "",
    prior_check_excerpt: str = "",
    settings: BaseSettings | None = None,
) -> tuple[str, CompanionEvidence]:
    evidence = gather_companion_evidence(
        db_path, hours=168, console_excerpt=console_excerpt, log_limit=4000
    )
    st = settings or load_base_settings()
    if not deep_analysis_api_configured(st):
        opt = generate_optimization_insight(db_path, days=7)
        return f"## 优化建议（模板）\n\n{opt[:6000]}", evidence
    ctx = build_llm_context(
        mode="optimization",
        history=[],
        evidence=evidence,
        session_summary=session_summary,
        console_excerpt=console_excerpt,
        evidence_max_chars=8000,
    )
    extra = ""
    if prior_check_excerpt.strip():
        extra = f"\n\n【近期深度检查摘要】\n{prior_check_excerpt[:4000]}\n"
    user = (
        "请基于运行证据输出 **功能与体验优化报告**（Markdown）。\n\n"
        "必须包含 **3～5 条**可落地优化项，每条含：现状、建议、预期收益、实施难度。\n"
        "侧重：接待稳定性、误触/漏触、OCR 质量、可观测性、配置可维护性。\n\n"
        f"{ctx}{extra}"
    )
    try:
        text = deep_analysis_completion(
            settings=st,
            system=_COMPANION_SYSTEM + "\n当前任务：未来功能与体验优化。",
            user=user,
            max_tokens=6144,
            temperature=0.35,
        ).strip()
        return text or "（优化分析未返回内容）", evidence
    except Exception as e:
        return f"优化分析失败：`{e!r}`", evidence


def generate_session_summary(
    *,
    mode: CompanionMode,
    history: list[ChatTurn],
    evidence: CompanionEvidence | None,
    settings: BaseSettings | None = None,
) -> str:
    """将本场对话压缩为简短摘要，供下次会话沿用。"""
    st = settings or load_base_settings()
    if not history:
        return "（空会话）"
    if not deep_analysis_api_configured(st):
        lines = [f"模式：{mode}", "对话要点："]
        for t in history[-8:]:
            prefix = "用户" if t.role == "user" else "AI"
            lines.append(f"- {prefix}：{t.content[:200]}")
        return "\n".join(lines)
    ev_block = ""
    if evidence is not None:
        ev_block = evidence.to_prompt_block(max_chars=2000)
    user = (
        "请将以下对话压缩为 **简短摘要**（Markdown，300～600 字）。\n\n"
        "保留：用户问题、已确认根因、达成的修复方案、待验证项。\n"
        "不要复述大段日志。\n\n"
        f"【模式】{mode}\n\n【对话】\n{_format_history(history, max_turns=30)}\n\n"
        f"【证据参考】\n{ev_block}"
    )
    try:
        return deep_analysis_completion(
            settings=st,
            system=_COMPANION_SYSTEM + "\n当前任务：生成可复用的会话摘要。",
            user=user,
            max_tokens=1024,
            temperature=0.2,
        ).strip()
    except Exception as e:
        return f"摘要生成失败：`{e!r}`"


def generate_companion_reply(
    *,
    mode: CompanionMode,
    history: list[ChatTurn],
    user_message: str,
    evidence: CompanionEvidence | None,
    session_summary: str = "",
    console_excerpt: str = "",
    settings: BaseSettings | None = None,
) -> str:
    st = settings or load_base_settings()
    hist = list(history) + [ChatTurn("user", user_message)]
    if not deep_analysis_api_configured(st):
        return (
            "当前未配置「AI 陪伴与深度分析模型」或 API 密钥，无法在对话中推理。\n\n"
            "请到 **设置中心 → 接入配置** 填写后重试。\n\n"
            "你刚才的问题已记录，配置完成后可再次发送。"
        )
    ctx = build_llm_context(
        mode=mode,
        history=hist[:-1],
        evidence=evidence,
        session_summary=session_summary,
        console_excerpt=console_excerpt,
        include_code=(mode == "deep_check"),
        evidence_max_chars=5000 if mode == "light_fix" else 8000,
    )
    task_by_mode = {
        "light_fix": "针对用户问题给出：**问题理解**、**可能原因**、**修复方案**（分步骤，可改配置请写键名）。",
        "deep_check": "结合深度检查上下文回答用户追问；若涉及代码/日志请引用证据。",
        "optimization": "结合优化目标回答；给出可落地建议与优先级。",
    }
    task = task_by_mode.get(mode, task_by_mode["light_fix"])
    user = (
        f"{ctx}\n\n【用户本轮输入】\n{user_message.strip()}\n\n"
        f"请{task} 若信息不足，列出需用户补充的现象。"
    )
    try:
        return deep_analysis_completion(
            settings=st,
            system=_COMPANION_SYSTEM,
            user=user,
            max_tokens=6144,
            temperature=0.3,
        ).strip()
    except Exception as e:
        return f"生成回复失败：`{e!r}`\n\n请检查网络与 API 配额后重试。"


def generate_companion_plan(
    *,
    history: list[ChatTurn],
    evidence: CompanionEvidence,
    settings: BaseSettings | None = None,
) -> str:
    st = settings or load_base_settings()
    if not deep_analysis_api_configured(st):
        return (
            "## 完整实施计划（模板）\n\n"
            "1. 在设置中心配置深度分析模型与密钥\n"
            "2. 开启 AI 陪伴监控，复现问题后再次点击「开始」\n"
            "3. 按控制台与 `system_health_logs` 中的 `pipeline_exception` 优先修复 OCR/窗口校准\n"
            "4. 验证：叮咚触发 → 置前 → 点黄条 → OCR → 自动回复\n"
        )
    user = (
        "请根据整场对话与运行证据，输出一份 **完整实施计划**（Markdown）。\n\n"
        "必须包含：\n"
        "## 目标\n## 问题清单\n## 修复步骤（按优先级，可勾选 checklist）\n"
        "## 验证方式\n## 风险与回滚\n## 后续优化（含对话中提到的优化点）\n\n"
        f"【运行证据】\n{evidence.to_prompt_block()}\n\n"
        f"【对话全文】\n{_format_history(history, max_turns=40)}"
    )
    try:
        return deep_analysis_completion(
            settings=st,
            system=_COMPANION_SYSTEM + "\n当前任务：输出可交付给开发/运维的完整实施计划，条理清晰。",
            user=user,
            max_tokens=8192,
            temperature=0.2,
        ).strip()
    except Exception as e:
        return f"生成计划失败：`{e!r}`"
