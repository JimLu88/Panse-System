"""
从聊天区 OCR 结果提取时间戳，并与本机时间校对（防点错旧会话误回复）。

策略（v1.3.89 修正）：取 ROI 内**最大（最新）** 的时间戳。
- 聊天窗口里时间戳天然按时间顺序排列，最大值就是最近活动时间
- 之前用「与系统时间偏差最小」会在最新时间戳被 OCR 漏识别时
  错误地回退到旧时间戳，触发 stale_discard 误杀
- max() 也比 min(abs(ref-dt)) 更稳健：未来年份 OCR 错读可被 stale 兜底
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from apps.core.ocr.models import OCRSpan

# 千牛常见：2026-5-17 20:46:54 / 2024-5-17 20:46:54 / 2026/05/17 20:46
_TS_IN_TEXT = re.compile(
    r"(\d{4})"
    r"[-/年]"
    r"(\d{1,2})"
    r"[-/月]?"
    r"(\d{1,2}?)"   # v1.6.13：日「非贪婪」。OCR 常把日期与时间粘连成 "2026-6-100:45"，
    r"(?:日)?"      # 贪婪会把日吃成 "10"→6月10日(未来)→被未来戳过滤误删→stale 永丢漏回。
    r"[\sT]*"       # 非贪婪让日只取 "1"、小时 (\d{1,2}) 回溯吃 "00"；真 "6-10 00:45"(有空格)仍正确。
    r"(\d{1,2})"
    r":"
    r"(\d{2})"
    r"(?::(\d{2}))?",
)


@dataclass(frozen=True, slots=True)
class ChatTimeAlignResult:
    ok: bool
    chat_time: datetime | None
    skew_seconds: float | None
    message: str
    candidate_count: int = 0
    """OCR 时间相对截图时刻偏差过大：丢弃本轮，不触发转人工。"""
    stale_discard: bool = False
    """偏差处于警告区间（仍允许自动回复）。"""
    warn_skew: bool = False


def is_chat_timestamp_text(text: str) -> bool:
    """整行或主体为聊天时间戳（非买家正文）。"""
    t = (text or "").strip()
    if not t:
        return False
    if parse_chat_timestamp(t) is not None:
        return True
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}$", t):
        return True
    return False


def parse_chat_timestamp(text: str) -> datetime | None:
    """从单行或片段文本解析聊天时间（本地时区，无时区信息）。"""
    t = (text or "").strip()
    if not t:
        return None
    m = _TS_IN_TEXT.search(t)
    if not m:
        return None
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    sec = int(m.group(6) or 0)
    try:
        return datetime(y, mo, d, h, mi, sec)
    except ValueError:
        return None


def collect_chat_timestamps(
    spans: list[OCRSpan],
    *,
    roi_height: int,
    min_y_ratio: float = 0.15,
    now: datetime | None = None,
    future_tolerance_minutes: float = 5.0,
) -> list[datetime]:
    """收集聊天 ROI 内（偏下区域）全部可解析时间戳。

    v1.6.11：剔除"明显晚于当前时刻"的候选。OCR 偶尔把 6-1 误读成 6-10
    （未来日期），这种戳一旦混入会被 max() 选中 → 偏差巨大 → stale 永丢。
    真实聊天时间不可能晚于现在，故未来戳必为误读，直接丢弃。
    """
    if not spans:
        return []
    ref = now or datetime.now()
    future_cap = ref.timestamp() + float(future_tolerance_minutes) * 60.0
    cutoff = roi_height * min_y_ratio
    out: list[datetime] = []
    seen: set[tuple[int, ...]] = set()
    for s in spans:
        raw = (s.text or "").strip()
        if not raw:
            continue
        dt = parse_chat_timestamp(raw)
        if dt is None:
            continue
        # 未来时间戳：OCR 误读（如 6-1→6-10），丢弃
        if dt.timestamp() > future_cap:
            continue
        x1, y1, x2, y2 = s.bbox
        cy = (y1 + y2) / 2.0
        if cy < cutoff:
            continue
        key = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        if key in seen:
            continue
        seen.add(key)
        out.append(dt)
    return out


def extract_best_chat_timestamp_for_alignment(
    spans: list[OCRSpan],
    *,
    roi_height: int,
    now: datetime | None = None,
) -> datetime | None:
    """
    在全部候选时间戳中，取**最大（最新）** 的一条。
    聊天窗口里时间戳天然按时间顺序排列，最大值即最近活动时间。
    """
    candidates = collect_chat_timestamps(spans, roi_height=roi_height, now=now)
    if not candidates:
        return None
    return max(candidates)


def extract_latest_chat_timestamp(
    spans: list[OCRSpan],
    *,
    roi_height: int,
) -> datetime | None:
    """名副其实：取最大（最新）的时间戳。"""
    return extract_best_chat_timestamp_for_alignment(spans, roi_height=roi_height)


def assess_chat_time_alignment(
    chat_time: datetime | None,
    *,
    now: datetime | None = None,
    capture_ts: datetime | None = None,
    max_skew_minutes: float = 5.0,
    warn_skew_minutes: float = 3.0,
    stale_discard_minutes: float = 15.0,
    capture_skew_discard_minutes: float = 15.0,
    on_missing: str = "allow",
    candidate_count: int = 0,
) -> ChatTimeAlignResult:
    """
    时间校对（分级）：
    - 相对截图时刻 capture_ts：偏差过大视为历史/错帧截图，丢弃本轮（不转人工）。
    - 相对系统时间 now：< warn 正常；warn～stale 记警告仍处理；> stale 丢弃不转人工。
    - max_skew_minutes：兼容旧配置；若大于 stale 则抬升 stale 上限（避免 YAML 写 30 仍按 15 截断）。
    """
    ref = now or datetime.now()
    cap = capture_ts or ref
    stale_m = max(float(stale_discard_minutes), float(max_skew_minutes))
    warn_m = max(0.0, min(float(warn_skew_minutes), stale_m))
    cap_discard_m = max(0.0, float(capture_skew_discard_minutes))
    extra = f"（共 {candidate_count} 个候选，已选最新时间戳）" if candidate_count > 1 else ""

    if chat_time is None:
        if (on_missing or "allow").lower() == "block":
            return ChatTimeAlignResult(
                ok=False,
                chat_time=None,
                skew_seconds=None,
                message="未识别到对话时间戳，已阻断自动回复",
                candidate_count=candidate_count,
            )
        return ChatTimeAlignResult(
            ok=True,
            chat_time=None,
            skew_seconds=None,
            message="未识别到对话时间戳，跳过时间校对",
            candidate_count=candidate_count,
        )

    skew_ref = abs((ref - chat_time).total_seconds())
    skew_cap = abs((cap - chat_time).total_seconds())
    skew_ref_min = skew_ref / 60.0
    skew_cap_min = skew_cap / 60.0

    if cap_discard_m > 0 and skew_cap_min > cap_discard_m:
        return ChatTimeAlignResult(
            ok=False,
            chat_time=chat_time,
            skew_seconds=skew_ref,
            message=(
                f"[OCR] 时间戳相对截图时刻偏差 {skew_cap_min:.1f} 分钟（>{cap_discard_m:g}），"
                f"判定为历史/错帧截图，丢弃本轮{extra}"
            ),
            candidate_count=candidate_count,
            stale_discard=True,
        )

    if skew_ref_min > stale_m:
        return ChatTimeAlignResult(
            ok=False,
            chat_time=chat_time,
            skew_seconds=skew_ref,
            message=(
                f"[OCR] 对话时间 {chat_time:%Y-%m-%d %H:%M:%S} 与系统 "
                f"{ref:%Y-%m-%d %H:%M:%S} 相差 {skew_ref_min:.1f} 分钟（>{stale_m:g}），"
                f"视为历史界面，丢弃本轮{extra}"
            ),
            candidate_count=candidate_count,
            stale_discard=True,
        )

    warn_flag = skew_ref_min > warn_m
    return ChatTimeAlignResult(
        ok=True,
        chat_time=chat_time,
        skew_seconds=skew_ref,
        message=(
            f"对话时间与系统时间偏差 {skew_ref_min:.1f} 分钟"
            f"（≤{stale_m:g} 分钟，警告阈值 {warn_m:g} 分钟）{extra}"
            + ("，已记警告" if warn_flag else "")
        ),
        candidate_count=candidate_count,
        warn_skew=warn_flag,
    )


def latest_date_differs_from_today(
    spans: list[OCRSpan],
    *,
    roi_height: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    v1.6.2 方式B（日期字符串校验）：取 ROI 内最新时间戳，比较其【日期】是否 = 今天。

    与 assess_chat_time_alignment（方式A：绝对时差分钟数）相互独立、互为双保险：
    - 方式A 抓"差多少分钟"，midnight 边界、系统时间漂移时可能漏
    - 方式B 直接比 年-月-日 字符串，跨天的旧会话一抓一个准

    返回 (是否为旧, 说明)：
      - (True,  "...")  最新时间戳日期 ≠ 今天 → 旧会话
      - (False, "...")  日期 = 今天 → 不旧
      - (False, "无时间戳") 抓不到时间戳 → 本函数不下"旧"结论（交给方式A/上层保守处理）
    """
    ref = now or datetime.now()
    best = extract_best_chat_timestamp_for_alignment(
        spans, roi_height=roi_height, now=ref
    )
    if best is None:
        return False, "方式B：未抓到时间戳，不下旧结论"
    if best.date() != ref.date():
        return True, (
            f"方式B：最新消息日期 {best:%Y-%m-%d} ≠ 今天 {ref:%Y-%m-%d}，判定为旧会话"
        )
    return False, f"方式B：最新消息日期 {best:%Y-%m-%d} = 今天，通过"


def assess_chat_time_alignment_from_spans(
    spans: list[OCRSpan],
    *,
    roi_height: int,
    now: datetime | None = None,
    capture_ts: datetime | None = None,
    max_skew_minutes: float = 5.0,
    warn_skew_minutes: float = 3.0,
    stale_discard_minutes: float = 15.0,
    capture_skew_discard_minutes: float = 15.0,
    on_missing: str = "allow",
) -> ChatTimeAlignResult:
    """从 spans 收集全部时间戳并校对。"""
    ref = now or datetime.now()
    candidates = collect_chat_timestamps(spans, roi_height=roi_height, now=ref)
    best = max(candidates) if candidates else None
    return assess_chat_time_alignment(
        best,
        now=ref,
        capture_ts=capture_ts,
        max_skew_minutes=max_skew_minutes,
        warn_skew_minutes=warn_skew_minutes,
        stale_discard_minutes=stale_discard_minutes,
        capture_skew_discard_minutes=capture_skew_discard_minutes,
        on_missing=on_missing,
        candidate_count=len(candidates),
    )
