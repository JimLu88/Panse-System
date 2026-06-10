"""
apps/mobile/orchestrator/shadow_diff.py
=========================================
影子模式报告生成器。

读取 data/mobile_state/shadow_replies.jsonl，输出统计 Markdown 报告。

用法：
  python -m apps.mobile.orchestrator.shadow_diff
  python -m apps.mobile.orchestrator.shadow_diff --days 7 --output report.md
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from apps.core.runtime_paths import shadow_replies_path as _shadow_replies_path

# 通过 runtime_paths 解析，与 shadow_mode.py 指向同一文件。
# 测试时可用 patch.object(sd, "_SHADOW_FILE", fake_path) 覆盖。
_SHADOW_FILE: Path = _shadow_replies_path()

_THRESHOLD_COUNT = 100   # 达到此数量建议切换真实接待


def load_records(days: int) -> list[dict]:
    """加载最近 N 天的影子记录。"""
    if not _SHADOW_FILE.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records: list[dict] = []
    try:
        with _SHADOW_FILE.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    ts = datetime.fromisoformat(r.get("ts", ""))
                    if ts >= cutoff:
                        records.append(r)
                except Exception:
                    continue
    except Exception:
        pass
    return records


def _all_count() -> int:
    if not _SHADOW_FILE.exists():
        return 0
    n = 0
    try:
        with _SHADOW_FILE.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    except Exception:
        pass
    return n


def generate_report(days: int = 1) -> str:
    records = load_records(days)
    total   = len(records)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    header = [
        "# 影子模式测试报告",
        "",
        f"**生成时间：** {now_str}　　**统计范围：** 最近 {days} 天",
        "",
    ]

    if not records:
        return "\n".join(header + [
            f"> ⚠️ 最近 {days} 天内无影子记录。",
            ">",
            "> 请先在「手机接待」Tab 中开启**影子模式**，等待买家消息触发后再查看报告。",
        ])

    by_device: dict[str, list[dict]] = {}
    for r in records:
        by_device.setdefault(r.get("device_id", "unknown"), []).append(r)

    all_count = _all_count()
    lines = header + [
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 统计设备数 | {len(by_device)} 台 |",
        f"| 本期拦截条数 | **{total} 条** |",
        f"| 历史累计条数 | {all_count} 条 |",
        "",
    ]

    if all_count >= _THRESHOLD_COUNT:
        lines += [
            "### ✅ 建议切换",
            "",
            f"累计已拦截 **{all_count} 条**（阈值 {_THRESHOLD_COUNT} 条），回复流程已充分验证。",
            "可在「手机接待」Tab 关闭影子模式，切换为**真实接待**。",
            "",
        ]
    else:
        lines += [
            "### ⏳ 继续观察",
            "",
            f"累计 **{all_count} 条**，还需 **{_THRESHOLD_COUNT - all_count} 条**"
            f" 达到切换建议阈值（{_THRESHOLD_COUNT} 条）。",
            "",
        ]

    lines += ["## 各设备明细", ""]
    for dev_id, recs in sorted(by_device.items()):
        lines.append(f"### 📱 {dev_id}　（{len(recs)} 条）")
        lines.append("")
        lines.append("| 时间 | 拦截的回复内容（前 80 字）|")
        lines.append("| --- | --- |")
        for r in recs[-20:]:
            text = r.get("text", "")[:80].replace("|", "｜").replace("\n", " ")
            lines.append(f"| {r.get('ts','')[:19]} | {text} |")
        lines.append("")

    lines += [
        "---",
        "",
        "**说明：** 影子模式下系统完整走采集 → AI 路由 → LLM → 话术生成全流程，",
        "仅在最后发送环节拦截，不向买家发出任何消息。",
        "关闭影子模式后，所有逻辑完全一致，消息会真实发出。",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成影子模式测试报告")
    parser.add_argument("--days",   type=int, default=1,  help="统计最近 N 天（默认 1）")
    parser.add_argument("--output", type=str, default="", help="报告输出路径（默认打印）")
    args   = parser.parse_args()
    report = generate_report(days=args.days)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"报告已保存: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
