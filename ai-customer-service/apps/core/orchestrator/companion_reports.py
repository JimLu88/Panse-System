"""
AI 陪伴报表：纯 SQLite 聚合 + 模板化 Markdown，可在后台线程执行，不触碰物理动作队列。
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from apps.core.crm.db import connect, init_db


def _since_iso(hours: int) -> str:
    dt = datetime.now() - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def generate_bug_fix_report(db_path: Path, hours: int = 24) -> str:
    """磨合期：Bug Fix Report — 拦截 / 失败类事件汇总与提示词修正建议。"""
    since = _since_iso(hours)
    conn = connect(db_path)
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT event_type, payload_json FROM system_health_logs
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT 5000
            """,
            (since,),
        ).fetchall()
    finally:
        conn.close()

    ctr = Counter(str(r[0]) for r in rows)
    lines: list[str] = [
        f"# Bug Fix Report（过去 {hours} 小时）",
        "",
        f"- 生成时间：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- 样本条数：{len(rows)}",
        "",
        "## 事件分布",
        "",
    ]
    for k, v in ctr.most_common(40):
        lines.append(f"- `{k}`：**{v}** 次")
    lines.extend(["", "## 拦截失败摘要（用于复盘）", ""])

    if not rows:
        lines.append("_当前窗口内无健康日志样本；请确认已开启「AI 陪伴」并有接待流量。_")
    else:
        top_fail = [
            k
            for k in ctr
            if "intercept" in k or "takeover" in k or k == "pipeline_exception"
        ]
        if top_fail:
            lines.append("高频失败类型：" + "、".join(f"`{x}`" for x in top_fail[:8]))
        lines.append("")

    lines.extend(
        [
            "## 提示词修正建议（模板，可按店铺话术微调）",
            "",
            "1. **风控 LLM 段落被拒**：在 Claude 系统提示中收紧「数字承诺 / 极限词」，增加「不确定则转人工」条款。",
            "2. **出站文本被拒**：检查 `configs/rules/reply_rules.yaml` 与短语黑名单，删除易触发误判的原型话术。",
            "3. **低置信度接管**：适当下调 `unknown_topic_threshold` 或补充知识库条目覆盖该类问法。",
            "4. **知识库未命中**：为高频问句写入 `kb_entries`，或放宽触发长度阈值前的样本采集。",
            "5. **管线异常**：查看「今日接待动态」原始摘要（若开启），核对 OCR 区域是否偏移。",
            "",
            "_说明：本报告为启发式归纳；若要接入大模型二次加工，可在后续版本对接 Claude 总结。_",
            "",
        ]
    )
    return "\n".join(lines)


def generate_optimization_insight(db_path: Path, days: int = 7) -> str:
    """成熟期：Optimization Insight — 会话事件粗统计与进化建议。"""
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = connect(db_path)
    init_db(conn)
    try:
        ev_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) FROM session_events
            WHERE created_at >= ?
            GROUP BY event_type
            ORDER BY COUNT(*) DESC
            LIMIT 40
            """,
            (since,),
        ).fetchall()
        hl_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) FROM system_health_logs
            WHERE created_at >= ?
            GROUP BY event_type
            ORDER BY COUNT(*) DESC
            LIMIT 20
            """,
            (since + " 00:00:00",),
        ).fetchall()
    except sqlite3.OperationalError:
        ev_rows = []
        hl_rows = []
    finally:
        conn.close()

    lines = [
        f"# Optimization Insight（近 {days} 天粗览）",
        "",
        f"- 生成时间：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "## 会话事件分布（session_events）",
        "",
    ]
    if ev_rows:
        for et, c in ev_rows:
            lines.append(f"- `{et}`：**{c}**")
    else:
        lines.append("_暂无 session_events 样本（或表为空）。_")
    lines.extend(["", "## 健康日志分布（system_health_logs）", ""])
    if hl_rows:
        for et, c in hl_rows:
            lines.append(f"- `{et}`：**{c}**")
    else:
        lines.append("_暂无健康日志；请开启 AI 陪伴并运行一段时间。_")

    lines.extend(
        [
            "",
            "## 客户意向与转化（启发式）",
            "",
            "- 若 **jim_intercept / 风控** 占比高：优先扩充安全话术与规则表，减少误杀。",
            "- 若 **补单 / 活动** 相关事件少：检查 campaigns / kb_entries 是否覆盖主推 SKU。",
            "- 若 **愤怒 streak** 事件可见：复核激怒阈值与安抚话术模板。",
            "",
            "## 系统进化建议",
            "",
            "- 将高频未命中问句导入知识库；对周期性活动配置 `campaigns` 时间窗。",
            "- 维持「AI 陪伴」开启，以便后续报表具备足够样本密度。",
            "",
        ]
    )
    return "\n".join(lines)


def pick_report_for_schedule(*, db_path: Path, anchor_started_at: str | None) -> tuple[str, str]:
    """返回 (report_kind, markdown)。磨合期前 3 天（按锚点日历天）走 Bug Fix；之后走 Optimization。"""
    from apps.core.orchestrator.health import days_since_anchor

    ds = days_since_anchor(anchor_started_at)
    if ds is None:
        ds = 999
    if ds < 3:
        return "bug_fix_daily", generate_bug_fix_report(db_path)
    return "optimization_weekly", generate_optimization_insight(db_path)
