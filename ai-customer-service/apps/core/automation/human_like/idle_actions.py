"""
闲时无意义动作：模拟真人卖家偶尔查订单、刷商品列表的行为。

为什么需要：
  - 真人卖家不会一整天只盯着聊天页 → 千牛风控可统计页面切换频率
  - 全程零页面切换 + 持续高频回复 = 机器特征
  - 偶尔切走再回来，让流量曲线更真实

方案：
  - maybe_perform_idle_action(driver)：按"小时概率"决定是否触发
  - 触发后随机选一个动作：order_page / product_list / refresh
  - 切到目标页 → 停留 3-8 秒 → 切回消息页

模块级 state：
  - _last_action_ts：上次执行时间，避免高频触发
  - 每个 shop 一个实例（如有多店）；当前先做单实例

实现注意：
  - 这些动作的"具体坐标"在 YAML（shop.qianniu.order_tab_point 等）
  - 本模块只管"什么时候触发、概率分配"；具体点击委托给 driver
  - 没配坐标的动作自动跳过
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

LogFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class IdleActionSettings:
    """闲时动作参数。"""
    enabled: bool = False
    #: 每小时执行多少次"无意义动作"的期望值
    expected_per_hour: float = 1.0
    #: 两次动作的最小间隔（秒），避免短时多次
    min_interval_s: float = 600.0
    #: 触发动作后在目标页停留的秒数范围
    dwell_min_s: float = 3.0
    dwell_max_s: float = 8.0


@dataclass(frozen=True, slots=True)
class IdleActionCallbacks:
    """
    上层提供的"具体怎么做"。
    每个 callback 应能在失败时返回 False（坐标缺失等），由本模块决定是否计数。
    """
    go_to_order_page: Callable[[], bool] | None = None
    go_to_product_list: Callable[[], bool] | None = None
    refresh_current_page: Callable[[], bool] | None = None
    return_to_message_page: Callable[[], bool] | None = None


# 模块级 state（多店共用一个节流；如需 per-shop 在上层包装）
_last_action_ts: float = 0.0
_state_lock = threading.Lock()


def reset_idle_state() -> None:
    """测试 / 重启用：清空"上次动作时间"。"""
    global _last_action_ts
    with _state_lock:
        _last_action_ts = 0.0


def maybe_perform_idle_action(
    cfg: IdleActionSettings,
    callbacks: IdleActionCallbacks,
    log: LogFn,
    *,
    tick_interval_s: float = 60.0,
) -> bool:
    """
    主循环调用入口（建议每 60s 一次）。

    @param tick_interval_s 本函数被调用的间隔（决定"每 tick 概率"）
    @return 是否执行了动作
    """
    if not cfg.enabled:
        return False

    global _last_action_ts
    now = time.monotonic()

    with _state_lock:
        if now - _last_action_ts < cfg.min_interval_s:
            return False

    # 每 tick 触发概率 = expected_per_hour * tick_interval_s / 3600
    p_per_tick = cfg.expected_per_hour * tick_interval_s / 3600.0
    p_per_tick = max(0.0, min(1.0, p_per_tick))
    if random.random() > p_per_tick:
        return False

    # 选一个可用动作
    candidates: list[tuple[str, Callable[[], bool]]] = []
    if callbacks.go_to_order_page is not None:
        candidates.append(("订单页", callbacks.go_to_order_page))
    if callbacks.go_to_product_list is not None:
        candidates.append(("商品列表", callbacks.go_to_product_list))
    if callbacks.refresh_current_page is not None:
        candidates.append(("刷新当前页", callbacks.refresh_current_page))

    if not candidates:
        return False

    name, action = random.choice(candidates)
    log(f"闲时动作：选中『{name}』")
    try:
        ok = action()
    except Exception as e:
        log(f"闲时动作失败：{name} → {e!r}")
        return False
    if not ok:
        log(f"闲时动作未执行（callback 返回 False）：{name}")
        return False

    # 停留模拟"看一眼"
    dwell = random.uniform(cfg.dwell_min_s, cfg.dwell_max_s)
    log(f"闲时动作：在『{name}』停留 {dwell:.1f}s")
    time.sleep(dwell)

    # 返回消息页（如果配置了）
    if callbacks.return_to_message_page is not None:
        try:
            callbacks.return_to_message_page()
        except Exception as e:
            log(f"闲时动作：返回消息页失败：{e!r}")

    with _state_lock:
        _last_action_ts = time.monotonic()
    return True
