from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Iterable, List

from apps.core.automation.actions.driver import PhysicalDriver
from apps.core.automation.human_like.keyboard import keystroke_delay
from apps.core.automation.human_like.timing import confirm_pause_before_send, per_chars_delay, sleep_range


# 只在句号/感叹号/问号处拆（不在逗号处拆），且单段不超 60 字
_STRONG_SPLIT = re.compile(r"([。.!！？?])")


def _split_at_strong_punct(text: str) -> list[str]:
    """在句号/感叹号/问号处拆，逗号不拆，最多返回 2 段。"""
    t = (text or "").strip()
    if not t:
        return []
    parts = _STRONG_SPLIT.split(t)
    out: list[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if _STRONG_SPLIT.fullmatch(p):
            buf += p
            out.append(buf.strip())
            buf = ""
        else:
            buf += p if buf else p
    if buf.strip():
        out.append(buf.strip())
    # 最多返回 2 段（避免过于碎片化）
    if len(out) <= 2:
        return out
    # 超过 2 段时：第一段保留，其余合并
    return [out[0], "".join(out[1:]).strip()]


def apply_split_policy(text: str, *, force_no_split: bool = False) -> list[str]:
    """
    拆分策略：
    - force_no_split=True（问候语等）：永不拆分，整段发送
    - 其他消息：30% 概率在强标点处拆成最多 2 条；70% 整段发送
    """
    t = (text or "").strip()
    if not t:
        return []
    if force_no_split:
        return [t]
    if random.random() < 0.30:
        parts = _split_at_strong_punct(t)
        if len(parts) >= 2:
            return parts
    return [t]


@dataclass(frozen=True, slots=True)
class SendTextPlan:
    segments: list[str]


def plan_send_text(text: str, *, force_no_split: bool = False) -> SendTextPlan:
    return SendTextPlan(segments=apply_split_policy(text, force_no_split=force_no_split))


def execute_send_text(driver: PhysicalDriver, text: str, *, force_no_split: bool = False) -> SendTextPlan:
    """
    Executes a human-like segmented send.

    Rules implemented (PRD):
    - Split into short sentences by punctuation.
    - For every 10 chars: delay 0.8~1.5s.
    - Between segments: pause 1.0~2.0s.
    - Before final send/enter: pause 0.5~1.2s.
    - Keystroke delay primitive exists (0.1~0.3s) for future true typing mode;
      current mode uses paste+enter per segment, so we keep it as a micro-jitter hook.

    v1.5.x 追加：
    - 深夜降级（默认凌晨 1-7 点）：直接返回空 plan（上层判 segments 为空即可跳过）
    - 回复延迟（默认 8-20s + 长消息加权）：在分段循环之前一次性 sleep
    """
    # 拟人化总开关：先尝试拉配置，失败则按原行为继续（向后兼容）
    try:
        from apps.core.configs.base_settings import load_base_settings, to_reply_timing_settings
        bs = load_base_settings()
        rt_cfg = to_reply_timing_settings(bs)
    except Exception:
        rt_cfg = None

    if rt_cfg is not None and rt_cfg.enabled:
        from apps.core.automation.human_like.reply_timing import (
            is_in_quiet_hours,
            sleep_for_reply_delay,
        )
        # 深夜降级：返回空 plan，上层据此跳过本次发送
        if is_in_quiet_hours(rt_cfg):
            return SendTextPlan(segments=[])
        # 回复延迟（一次性 sleep，避免每段都 sleep 导致过长）
        sleep_for_reply_delay(text, rt_cfg)

    plan = plan_send_text(text, force_no_split=force_no_split)
    for idx, seg in enumerate(plan.segments):
        # paste (segment)
        driver.paste_text(seg)
        keystroke_delay()

        # "reading/typing" delay by length
        per_chars_delay(len(seg))

        # Confirm pause and send (enter)
        confirm_pause_before_send()
        driver.press_enter()

        # Between segments pause (if more to send)
        if idx < len(plan.segments) - 1:
            sleep_range(1.0, 2.0)

    return plan

