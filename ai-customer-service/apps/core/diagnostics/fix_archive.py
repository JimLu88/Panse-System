"""
v1.6.1 修复档案（全自动）：
  - 程序运行时，每条业务日志自动匹配「已知问题指纹」，命中即落盘记录（issue_events.jsonl）
  - 开发者每次修复写入 configs/fix_changelog.json（哪个版本、改了什么、根因）
  - 一键导出 markdown：把「问题出现历史」×「历次修复」交叉对比
    → 一眼看出「同一个问题修了 N 次仍复现」，定位为什么反复修不好

为什么这样设计（用户原话）：
  「同样的问题已经来回修了 15 天了，一直没改好」「我的目的是我到时候能复制出来你能看到」
  → 全自动记录（用户零操作）+ 一键导出（复制给开发者）

数据文件：
  - data/logs/issue_events.jsonl  每行 {"ts":ISO,"version":"1.5.12","sig_id":"...","label":"...","severity":"P1","line":"..."}
  - configs/fix_changelog.json    {"issues":[{"sig_id","title","fixes":[{"version","date","summary","root_cause","files"}]}]}

日志前缀：[fix_archive]
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("apps.core.diagnostics.fix_archive")

_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class IssueSignature:
    sig_id: str
    label: str
    severity: str
    pattern: re.Pattern


_RAW_SIGNATURES: list[tuple[str, str, str, str]] = [
    ("yellow_bar_zero", "黄条识别 max=0（未读高亮检测失效）", "P1",
     r"黄条第[12]次 无达阈行.*max=0|yelMax=0"),
    ("switch_failed", "未读会话切换失败 switched=False", "P1",
     r"步骤2完成：switched=False"),
    ("ocr_empty_buyer", "OCR 买家文本为空被跳过", "P1",
     r"非有效买家留言（''）|buyer_text=''"),
    ("repeat_reply_noise", "在么/寒暄被当噪声不回复", "P1",
     r"非有效买家留言（'在[么吗]'"),
    ("single_char_risk", "单字 OCR 触发风控话术", "P0",
     r"single_char_ocr_noise|请问有什么可以帮到您"),
    ("dup_reply", "重复回复（预算拦截）", "P1",
     r"\[budget\] 拒发|hard_limit_3_reached|similar_to_prev"),
    ("risk_warning_popup", "淘宝风控弹窗（返回修改）", "P0",
     r"\[risk_warn\]|风控弹窗|请勿重复提问|服务态度提醒"),
    ("fatal_exception", "致命异常（cycle 崩溃）", "P0",
     r"\[致命\]|UnboundLocalError|Traceback|排队事件处理异常"),
    ("bring_front_fail", "千牛置前失败/被抢焦点", "P1",
     r"置前.*超时|窗口就绪.*超时|前台匹配=False|焦点被抢回"),
    ("minibubble_fail", "右下角迷你气泡兜底失败", "P1",
     r"\[minibubble\] 兜底失败|no_tb_nick_found"),
    ("time_skew_discard", "时间戳偏差大丢弃本轮（点错旧会话）", "P1",
     r"判定为历史/错帧截图|可能点错了旧会话"),
    ("coord_calib_miss", "坐标自动识别漏识别", "P2",
     r"未识别 客服按钮|未识别 聊天区"),
]

_SIGNATURES: list[IssueSignature] = [
    IssueSignature(sig_id=s, label=l, severity=sev, pattern=re.compile(rx))
    for (s, l, sev, rx) in _RAW_SIGNATURES
]


def _logs_dir() -> Path:
    try:
        from apps.core.runtime_paths import default_panse_customer_chat_log_csv
        return default_panse_customer_chat_log_csv().parent
    except Exception:
        return Path.cwd() / "data" / "logs"


def _issue_events_path() -> Path:
    return _logs_dir() / "issue_events.jsonl"


def _changelog_path() -> Path:
    try:
        from apps.core.runtime_paths import configs_dir
        return configs_dir() / "fix_changelog.json"
    except Exception:
        return Path.cwd() / "configs" / "fix_changelog.json"


def current_version() -> str:
    try:
        from apps.__version__ import __version__  # type: ignore
        return str(__version__)
    except Exception:
        pass
    try:
        from apps import release_info  # type: ignore
        return str(getattr(release_info, "VERSION", "") or "unknown")
    except Exception:
        return "unknown"


def record_log_line(line: str, *, version: str | None = None) -> None:
    """每条业务日志调用一次：匹配已知问题指纹，命中即落盘。全 try/except。"""
    try:
        s = (line or "").strip()
        if not s:
            return
        hits = [sig for sig in _SIGNATURES if sig.pattern.search(s)]
        if not hits:
            return
        ver = version or current_version()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        path = _issue_events_path()
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for sig in hits:
                    rec = {
                        "ts": ts, "version": ver, "sig_id": sig.sig_id,
                        "label": sig.label, "severity": sig.severity,
                        "line": s[:300],
                    }
                    f.write(json.dumps(rec, ensure_ascii=False))
                    f.write("\n")
    except Exception as e:  # noqa: BLE001
        _log.debug("[fix_archive] record_log_line 异常（忽略）：%r", e)


@dataclass(slots=True)
class IssueAggregate:
    sig_id: str
    label: str
    severity: str
    count: int = 0
    first_ts: str = ""
    last_ts: str = ""
    versions: list[str] = field(default_factory=list)
    last_line: str = ""


def _read_events() -> list[dict]:
    path = _issue_events_path()
    out: list[dict] = []
    if not path.is_file():
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except Exception as e:
        _log.warning("[fix_archive] 读 issue_events 失败：%r", e)
    return out


def _version_key(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def aggregate_events() -> dict[str, IssueAggregate]:
    aggs: dict[str, IssueAggregate] = {}
    for ev in _read_events():
        sid = str(ev.get("sig_id") or "")
        if not sid:
            continue
        a = aggs.get(sid)
        if a is None:
            a = IssueAggregate(
                sig_id=sid, label=str(ev.get("label") or sid),
                severity=str(ev.get("severity") or ""),
            )
            aggs[sid] = a
        a.count += 1
        ts = str(ev.get("ts") or "")
        if ts:
            if not a.first_ts or ts < a.first_ts:
                a.first_ts = ts
            if not a.last_ts or ts > a.last_ts:
                a.last_ts = ts
        ver = str(ev.get("version") or "")
        if ver and ver not in a.versions:
            a.versions.append(ver)
        a.last_line = str(ev.get("line") or "")
    # 版本按语义排序
    for a in aggs.values():
        a.versions.sort(key=_version_key)
    return aggs


def load_fix_changelog() -> dict:
    path = _changelog_path()
    if not path.is_file():
        return {"issues": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"issues": []}
    except Exception as e:
        _log.warning("[fix_archive] 读 fix_changelog 失败：%r", e)
        return {"issues": []}


def _changelog_by_sig() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for issue in (load_fix_changelog().get("issues") or []):
        sid = str(issue.get("sig_id") or "")
        if sid:
            out[sid] = issue
    return out


def _is_recurring_after_fix(agg: IssueAggregate, fixes: list[dict]) -> bool:
    """出现的最新版本 >= 最后一次修复版本 → 修了还在复现。"""
    if not fixes or not agg.versions:
        return False
    fix_versions = [str(f.get("version") or "") for f in fixes if f.get("version")]
    if not fix_versions:
        return False
    last_fix = max(fix_versions, key=_version_key)
    last_seen = max(agg.versions, key=_version_key)
    return _version_key(last_seen) >= _version_key(last_fix)


def build_report_markdown() -> str:
    aggs = aggregate_events()
    cl = _changelog_by_sig()
    lines: list[str] = []
    lines.append("# AIWorkbench 修复档案报告")
    lines.append("")
    lines.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 当前版本：{current_version()}")
    lines.append(f"- 已追踪问题指纹：{len(_SIGNATURES)} 种")
    lines.append(f"- 累计自动记录事件：{sum(a.count for a in aggs.values())} 条")
    lines.append("")

    all_sids = set(aggs) | set(cl)

    def _sort_key(sid: str):
        a = aggs.get(sid)
        fixes = (cl.get(sid, {}).get("fixes") or [])
        recurring = a is not None and _is_recurring_after_fix(a, fixes)
        return (0 if recurring else 1, -(a.count if a else 0))

    lines.append("## 一览表")
    lines.append("")
    lines.append("| 问题 | 级别 | 出现次数 | 出现版本 | 修复次数 | 状态 |")
    lines.append("|---|---|---|---|---|---|")
    for sid in sorted(all_sids, key=_sort_key):
        a = aggs.get(sid)
        issue = cl.get(sid, {})
        fixes = issue.get("fixes") or []
        label = (a.label if a else issue.get("title") or sid)
        sev = (a.severity if a else "")
        cnt = a.count if a else 0
        vers = "、".join(a.versions) if a else "—"
        fix_cnt = len(fixes)
        if a and _is_recurring_after_fix(a, fixes):
            status = "⚠ 修了还复现"
        elif fix_cnt and not a:
            status = "✅ 已修未再现"
        elif a and not fix_cnt:
            status = "🆕 未修复"
        else:
            status = "—"
        lines.append(f"| {label} | {sev} | {cnt} | {vers} | {fix_cnt} | {status} |")
    lines.append("")

    lines.append("## 逐问题详情")
    lines.append("")
    for sid in sorted(all_sids, key=_sort_key):
        a = aggs.get(sid)
        issue = cl.get(sid, {})
        fixes = issue.get("fixes") or []
        label = (a.label if a else issue.get("title") or sid)
        lines.append(f"### {label}  `({sid})`")
        if a:
            lines.append(
                f"- 自动记录：出现 **{a.count}** 次，"
                f"首次 {a.first_ts}（{a.versions[0] if a.versions else '?'}），"
                f"最近 {a.last_ts}（{a.versions[-1] if a.versions else '?'}）"
            )
            if a.last_line:
                lines.append(f"- 最近一条日志：`{a.last_line}`")
        else:
            lines.append("- 自动记录：暂无（运行中尚未触发，或已消失）")
        if fixes:
            lines.append(f"- 历次修复（{len(fixes)} 次）：")
            for fx in fixes:
                lines.append(
                    f"  - **{fx.get('version','?')}** "
                    f"({fx.get('date','')}) — {fx.get('summary','')}"
                )
                if fx.get("root_cause"):
                    lines.append(f"    - 根因判断：{fx.get('root_cause')}")
                if fx.get("files"):
                    lines.append(f"    - 改动文件：{', '.join(fx.get('files'))}")
        else:
            lines.append("- 历次修复：尚无记录")
        if a and _is_recurring_after_fix(a, fixes):
            lines.append(
                "- ⚠ **诊断：此问题在最近一次修复后仍复现 —— "
                "此前的根因判断或修复点可能不对，需换思路。**"
            )
        lines.append("")
    return "\n".join(lines)


def export_markdown(path: Path | str | None = None) -> Path:
    md = build_report_markdown()
    if path is None:
        out = _logs_dir() / f"修复档案_{time.strftime('%Y%m%d_%H%M%S')}.md"
    else:
        out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return out


def summarize_for_llm() -> str:
    aggs = aggregate_events()
    cl = _changelog_by_sig()
    blocks: list[str] = []
    for sid, a in aggs.items():
        fixes = cl.get(sid, {}).get("fixes") or []
        if not _is_recurring_after_fix(a, fixes):
            continue
        fix_txt = "; ".join(
            f"{f.get('version')}: {f.get('summary')}（根因猜测:{f.get('root_cause','无')}）"
            for f in fixes
        )
        blocks.append(
            f"问题「{a.label}」出现 {a.count} 次，版本 {'、'.join(a.versions)}；"
            f"历次修复：{fix_txt}；最近日志：{a.last_line}"
        )
    return "\n".join(blocks) if blocks else "（暂无『修了仍复现』的问题）"
