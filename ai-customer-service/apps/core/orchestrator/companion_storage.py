"""
AI 陪伴持久化：全量对话归档 + 精简上下文文件（供 LLM 每次读取）。

目录结构（位于实例 data/companion/）::

    conversation_full.jsonl     # 每一轮对话追加一行（永久保留）
    ai_retrieval_context.md     # 合并后的精简记忆（AI 每次优先读此文件）
    archive/                    # 每场对话完整 JSON
    condensed/                  # 每场对话精简 Markdown
    repo_scan/                  # 深度检查全仓扫描产物
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from apps.core.crm.events import now_iso
from apps.core.runtime_paths import bundle_root, project_root

if TYPE_CHECKING:
    from apps.core.orchestrator.companion_analysis import ChatTurn

CompanionMode = str

_SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".cursor",
        "node_modules",
        "dist",
        "build",
        ".venv",
        "venv",
        "models",
        "images",
        "data",
        "AIWorkbench_虚拟机拷贝包",
        ".pytest_cache",
        "mcps",
    }
)
_SKIP_SUFFIX = frozenset(
    {
        ".pyc",
        ".pyo",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".exe",
        ".dll",
        ".so",
        ".zip",
        ".db",
        ".sqlite",
        ".bin",
        ".onnx",
        ".pdf",
        ".ico",
        ".whl",
    }
)
_TEXT_SUFFIX = frozenset(
    {
        ".py",
        ".yaml",
        ".yml",
        ".md",
        ".json",
        ".txt",
        ".spec",
        ".ps1",
        ".toml",
        ".ini",
        ".bat",
        ".sql",
        ".csv",
        ".jsonl",
    }
)
_SCAN_TOP_DIRS = ("apps", "configs", "tests", "hooks", "scripts")

# 默认接待核心路径（可被 query_rewrite.yaml 覆盖）
_DEFAULT_PRIORITY_PREFIXES = (
    "apps/core/orchestrator/",
    "apps/core/channels/qianniu/",
    "apps/core/audio/",
    "apps/core/ai/input_quality_gate.py",
    "apps/core/automation/",
    "configs/query_rewrite.yaml",
    "configs/base_settings.yaml",
)


@dataclass(frozen=True, slots=True)
class DeepScanSettings:
    llm_excerpt_max_chars: int = 18_000
    priority_file_max_chars: int = 3500
    outline_max_symbols: int = 40
    priority_path_prefixes: tuple[str, ...] = _DEFAULT_PRIORITY_PREFIXES
    per_file_full_scan_max_chars: int = 12_000


def load_deep_scan_settings() -> DeepScanSettings:
    try:
        from apps.core.ai.input_quality_gate import _load_gate_config

        raw = _load_gate_config()
    except Exception:
        raw = {}
    comp = raw.get("companion") if isinstance(raw.get("companion"), dict) else {}
    ds = comp.get("deep_scan") if isinstance(comp.get("deep_scan"), dict) else {}
    prefixes = ds.get("priority_path_prefixes")
    if isinstance(prefixes, list) and prefixes:
        pp = tuple(str(p).replace("\\", "/") for p in prefixes)
    else:
        pp = _DEFAULT_PRIORITY_PREFIXES
    return DeepScanSettings(
        llm_excerpt_max_chars=int(ds.get("llm_excerpt_max_chars") or 18_000),
        priority_file_max_chars=int(ds.get("priority_file_max_chars") or 3500),
        outline_max_symbols=int(ds.get("outline_max_symbols") or 40),
        priority_path_prefixes=pp,
        per_file_full_scan_max_chars=int(ds.get("per_file_full_scan_max_chars") or 12_000),
    )


def _is_priority_file(rel: str, prefixes: tuple[str, ...]) -> bool:
    r = rel.replace("\\", "/")
    return any(r.startswith(p) or r == p.rstrip("/") for p in prefixes)


def _outline_python(text: str, *, max_symbols: int) -> str:
    symbols: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("def ", "async def ", "class ")):
            symbols.append(s[:140])
        elif s.startswith(("@",)) and symbols:
            symbols.append(s[:100])
        if len(symbols) >= max_symbols:
            break
    return "\n".join(symbols)


def _outline_yaml(text: str, *, max_lines: int = 25) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" in s and not s.startswith("-"):
            lines.append(s[:120])
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def _compact_for_llm(rel: str, raw: str, cfg: DeepScanSettings) -> str:
    """非优先文件：轮廓摘要；优先文件：保留关键段落。"""
    n_lines = raw.count("\n") + 1
    header = f"### `{rel}` ({n_lines} 行, {len(raw)} 字)"
    if _is_priority_file(rel, cfg.priority_path_prefixes):
        body = raw
        if len(body) > cfg.priority_file_max_chars:
            body = (
                body[: cfg.priority_file_max_chars - 80]
                + "\n…（优先文件已截断，完整见 latest_full_scan.txt）"
            )
        return f"{header}\n```\n{body}\n```\n"

    suf = Path(rel).suffix.lower()
    if suf == ".py":
        outline = _outline_python(raw, max_symbols=cfg.outline_max_symbols)
        kind = "Python 轮廓"
    elif suf in (".yaml", ".yml"):
        outline = _outline_yaml(raw)
        kind = "YAML 键名"
    else:
        preview = "\n".join(raw.splitlines()[:12])
        outline = preview[:800]
        kind = "预览"
    if not outline.strip():
        outline = "（无显著符号，可能为空或二进制伪装）"
    return f"{header} · {kind}\n```\n{outline}\n```\n"


def companion_data_dir() -> Path:
    d = project_root() / "data" / "companion"
    for sub in ("archive", "condensed", "repo_scan"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def ai_retrieval_path() -> Path:
    return companion_data_dir() / "ai_retrieval_context.md"


def conversation_log_path() -> Path:
    return companion_data_dir() / "conversation_full.jsonl"


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def load_ai_retrieval_context(*, max_chars: int = 24000) -> str:
    """AI 每次对话优先读取的精简上下文文件。"""
    p = ai_retrieval_path()
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if len(text) > max_chars:
        return text[: max_chars - 120] + "\n\n…（ai_retrieval_context.md 已截断，完整内容见 data/companion/）"
    return text


def append_conversation_turn(
    *,
    mode: str,
    session_id: str,
    role: str,
    content: str,
) -> None:
    """永久追加每一轮对话到 JSONL。"""
    record = {
        "ts": now_iso(),
        "mode": mode,
        "session_id": session_id,
        "role": role,
        "content": content,
    }
    path = conversation_log_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def template_condense_session(*, mode: str, session_id: str, history: list[ChatTurn]) -> str:
    """无 LLM 时的快速精简（关闭窗口时兜底）。"""
    lines = [
        f"### 会话 {session_id} · 模式 `{mode}` · {now_iso()}",
        "",
    ]
    for t in history:
        if t.role == "assistant" and t.content.strip().startswith("你好"):
            continue
        label = "用户" if t.role == "user" else "AI"
        body = (t.content or "").strip().replace("\n", " ")
        if len(body) > 500:
            body = body[:497] + "…"
        lines.append(f"- **{label}**：{body}")
    return "\n".join(lines)


def save_condensed_session(*, mode: str, session_id: str, condensed_md: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = companion_data_dir() / "condensed" / f"{ts}_{mode}_{session_id}.md"
    header = (
        f"<!-- mode={mode} session={session_id} updated={now_iso()} -->\n\n"
        f"# 精简会话 · {mode}\n\n"
    )
    path.write_text(header + condensed_md.strip() + "\n", encoding="utf-8")
    return path


def archive_full_session(
    *,
    mode: str,
    session_id: str,
    history: list[ChatTurn],
    condensed_md: str,
) -> Path:
    """归档完整对话，并更新 AI 检索文件。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    arch = companion_data_dir() / "archive" / f"{ts}_{mode}_{session_id}.json"
    payload = {
        "session_id": session_id,
        "mode": mode,
        "archived_at": now_iso(),
        "condensed_md": condensed_md,
        "history": [{"role": t.role, "content": t.content} for t in history],
    }
    arch.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    save_condensed_session(mode=mode, session_id=session_id, condensed_md=condensed_md)
    rebuild_ai_retrieval_context()
    return arch


def rebuild_ai_retrieval_context(*, max_condensed_files: int = 40) -> None:
    """
    合并 condensed/ 下所有精简文件 → ai_retrieval_context.md。
    AI 每次打开陪伴对话时读此文件即可，无需重读全量 JSONL。
    """
    condensed_dir = companion_data_dir() / "condensed"
    files = sorted(condensed_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:max_condensed_files]

    parts = [
        "# AI 陪伴 · 精简记忆库",
        "",
        f"> 自动生成于 {now_iso()}；完整对话见 `conversation_full.jsonl` 与 `archive/`。",
        "",
        "---",
        "",
    ]
    for fp in files:
        try:
            body = fp.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        parts.append(body)
        parts.append("\n---\n")

    full_log = conversation_log_path()
    if full_log.is_file():
        try:
            n = sum(1 for _ in full_log.open(encoding="utf-8"))
        except Exception:
            n = 0
        parts.append(f"\n**归档统计**：JSONL 累计 {n} 条对话轮次；archive 共 {len(list((companion_data_dir() / 'archive').glob('*.json')))} 场会话。\n")

    ai_retrieval_path().write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def _source_roots() -> list[Path]:
    roots: list[Path] = []
    for r in (bundle_root(), project_root()):
        if r.is_dir() and r not in roots:
            roots.append(r)
    return roots


def _should_scan_file(path: Path) -> bool:
    suf = path.suffix.lower()
    if suf in _SKIP_SUFFIX:
        return False
    if suf in _TEXT_SUFFIX:
        return True
    if suf == "" and path.name in (
        "requirements.txt",
        "AIWorkbench.spec",
        "README.md",
        "LICENSE",
    ):
        return True
    return False


def _iter_repo_files(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def _add(rel: str, p: Path) -> None:
        if rel in seen:
            return
        seen.add(rel)
        out.append((rel, p))

    for top in _SCAN_TOP_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts):
                continue
            if _should_scan_file(path):
                _add(rel, path)

    for name in (
        "requirements.txt",
        "AIWorkbench.spec",
        "README.md",
        "build_aiworkbench.ps1",
        "build_exe.ps1",
    ):
        p = root / name
        if p.is_file():
            _add(name, p)

    return out


@dataclass(slots=True)
class RepoScanResult:
    file_count: int
    total_bytes: int
    manifest_path: Path
    full_scan_path: Path
    llm_excerpt_path: Path
    llm_excerpt: str


def scan_full_repository(
    *,
    per_file_max_chars: int | None = None,
    llm_excerpt_max_chars: int | None = None,
    settings: DeepScanSettings | None = None,
) -> RepoScanResult:
    """
    全仓扫描：磁盘保留完整内容；发给 LLM 的为「优先路径正文 + 其余文件轮廓」。
    """
    cfg = settings or load_deep_scan_settings()
    per_cap = per_file_max_chars if per_file_max_chars is not None else cfg.per_file_full_scan_max_chars
    excerpt_cap = llm_excerpt_max_chars if llm_excerpt_max_chars is not None else cfg.llm_excerpt_max_chars
    scan_dir = companion_data_dir() / "repo_scan"
    scan_dir.mkdir(parents=True, exist_ok=True)

    merged_files: list[tuple[str, Path]] = []
    for root in _source_roots():
        for rel, p in _iter_repo_files(root):
            if not any(rel == m[0] for m in merged_files):
                merged_files.append((rel, p))

    merged_files.sort(key=lambda x: x[0])
    manifest = {
        "scanned_at": now_iso(),
        "file_count": len(merged_files),
        "files": [rel for rel, _ in merged_files],
    }
    manifest_path = scan_dir / "latest_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    full_parts: list[str] = [
        f"# 全仓代码扫描 · {now_iso()}",
        f"# 文件数：{len(merged_files)}",
        "",
    ]
    excerpt_parts: list[str] = [
        f"【全仓智能摘要】共 {len(merged_files)} 个文件（非优先文件仅 def/class/配置键轮廓）。",
        f"完整扫描：`data/companion/repo_scan/latest_full_scan.txt`",
        f"本摘要上限约 {excerpt_cap} 字。",
        "",
    ]
    total_bytes = 0
    excerpt_used = 0

    # 优先文件先入 excerpt，避免被尾部 tests 挤掉
    ordered = sorted(
        merged_files,
        key=lambda x: (0 if _is_priority_file(x[0], cfg.priority_path_prefixes) else 1, x[0]),
    )

    for rel, path in ordered:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raw = f"（读取失败：{e!r}）"
        total_bytes += len(raw.encode("utf-8", errors="ignore"))
        chunk = raw if len(raw) <= per_cap else raw[: per_cap - 80] + "\n…（文件内截断）"
        full_parts.append(
            f"\n\n{'=' * 72}\nFILE: {rel}\n{'=' * 72}\n{chunk}\n"
        )
        if excerpt_used >= excerpt_cap:
            continue
        compact = _compact_for_llm(rel, raw, cfg)
        room = excerpt_cap - excerpt_used
        if len(compact) > room:
            if room < 200:
                continue
            compact = compact[: room - 40] + "\n…\n"
        excerpt_parts.append(compact)
        excerpt_used += len(compact)

    full_path = scan_dir / "latest_full_scan.txt"
    full_path.write_text("".join(full_parts), encoding="utf-8")

    excerpt_text = "".join(excerpt_parts)
    excerpt_path = scan_dir / "latest_llm_excerpt.txt"
    excerpt_path.write_text(excerpt_text, encoding="utf-8")

    return RepoScanResult(
        file_count=len(merged_files),
        total_bytes=total_bytes,
        manifest_path=manifest_path,
        full_scan_path=full_path,
        llm_excerpt_path=excerpt_path,
        llm_excerpt=excerpt_text,
    )
