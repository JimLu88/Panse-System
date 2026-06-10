"""
回复时间拟人化：基础延迟 + 长消息打字时间 + 深夜降级 + 正态分布扰动。

为什么需要：
  - 真人客服收到消息后不会 0.5 秒就回复 → 千牛风控可统计回复时间分布
  - 真人凌晨 2 点不会秒回 → 时段聚集本身就是机器特征
  - 真人长消息要"打字"更久 → 不该和短消息同样的延迟

设计：
  - compute_reply_delay()：基础 8-20s 随机 + 长消息按字数加 30s/200 字
  - jitter_delay()：把任意基础延迟加正态分布噪声（消除人为参数的"整数化"痕迹）
  - is_in_quiet_hours()：凌晨 1-7 点返回 True，上层应直接跳过回复

不引入新依赖：只用 random / datetime / time
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ReplyTimingSettings:
    """回复时间拟人化参数（由 BaseSettings → load_reply_timing_settings 构造）。"""
    enabled: bool = True
    #: 基础回复延迟下限（秒）
    base_delay_min_s: float = 8.0
    #: 基础回复延迟上限（秒）
    base_delay_max_s: float = 20.0
    #: 每 N 字额外加的"打字时间"秒数（按字数线性）
    typing_extra_s_per_chars: float = 30.0
    typing_extra_chars_unit: int = 200
    #: 深夜降级开关
    quiet_hours_enabled: bool = True
    quiet_hours_start: int = 1   # 凌晨 1 点
    quiet_hours_end: int = 7     # 早上 7 点
    #: 正态分布扰动标准差（占基础延迟的比例，0.15 = 15%）
    gaussian_jitter_ratio: float = 0.15


def compute_reply_delay(
    text: str,
    cfg: ReplyTimingSettings,
) -> float:
    """
    计算一条回复应该等多久才发出去。

    公式：
        base = uniform(min, max)
        typing_extra = ceil(len(text) / unit) * typing_extra_s_per_chars
        raw = base + typing_extra
        return gaussian_jitter(raw)

    @return 秒数（>= 0）
    """
    if not cfg.enabled:
        return 0.0

    base = random.uniform(cfg.base_delay_min_s, cfg.base_delay_max_s)

    chars = len(text or "")
    # 修正：用整除而非 ceil，避免短消息（< unit 字）也被加 30s
    # 例：unit=200 → 0-199 字 +0；200-399 字 +30s；400-599 字 +60s
    if chars > 0 and cfg.typing_extra_chars_unit > 0:
        blocks = chars // cfg.typing_extra_chars_unit
        typing_extra = blocks * cfg.typing_extra_s_per_chars
    else:
        typing_extra = 0.0

    raw = base + typing_extra
    jittered = jitter_delay(raw, cfg.gaussian_jitter_ratio)
    return max(0.0, jittered)


def jitter_delay(base_s: float, ratio: float) -> float:
    """
    给基础延迟加正态分布扰动。

    例子：base=10s, ratio=0.15 → 标准差 1.5s 的正态噪声
    用 random.gauss 而非 uniform，让分布像真人（中间多，两端少）。

    @return 新的延迟（>= 0）
    """
    if ratio <= 0:
        return base_s
    sigma = base_s * ratio
    noise = random.gauss(0.0, sigma)
    return max(0.0, base_s + noise)


def is_in_quiet_hours(
    cfg: ReplyTimingSettings,
    *,
    now: datetime | None = None,
) -> bool:
    """
    判断当前时间是否在"深夜降级"时段（默认凌晨 1-7 点）。

    返回 True → 上层应跳过自动回复（要么完全不回，要么转人工兜底）

    跨午夜处理：start > end 时（如 22:00-07:00），按 OR 判定。
    """
    if not cfg.quiet_hours_enabled:
        return False
    n = now or datetime.now()
    start = cfg.quiet_hours_start % 24
    end = cfg.quiet_hours_end % 24
    if start == end:
        return False
    hr = n.hour
    if start < end:
        # 不跨午夜：1-7 → 凌晨 1 <= hr < 7
        return start <= hr < end
    # 跨午夜：22-7 → hr >= 22 OR hr < 7
    return hr >= start or hr < end


def sleep_for_reply_delay(
    text: str,
    cfg: ReplyTimingSettings,
    *,
    log_fn=None,
) -> float:
    """
    便利包装：算出延迟 + 实际 sleep。

    @return 实际 sleep 的秒数（便于上层记账）
    """
    delay = compute_reply_delay(text, cfg)
    if log_fn:
        log_fn(f"回复延迟：拟人化等 {delay:.2f}s（文本 {len(text)} 字）")
    if delay > 0:
        time.sleep(delay)
    return delay
