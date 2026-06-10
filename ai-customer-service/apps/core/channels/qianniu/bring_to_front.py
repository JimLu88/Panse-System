"""千牛窗口置前：最小化优先任务栏还原、可见窗口置前、还原结果校验。"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from collections.abc import Callable

from apps.core.ai.input_quality_gate import (
    BringToFrontSettings,
    BringToFrontStep,
    load_bring_to_front_settings,
)
from apps.core.configs.loader import ShopConfig

LogFn = Callable[[str], None]

SW_RESTORE = 9
SW_SHOW = 5


def restore_and_show(hwnd: int, log: LogFn | None = None) -> None:
    if hwnd <= 0:
        return
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        if log:
            log("置前：ShowWindow(SW_RESTORE)")
    user32.ShowWindow(hwnd, SW_SHOW)


def _user32() -> ctypes.WinDLL:
    return ctypes.windll.user32


def verify_window_ready(
    hwnd: int,
    shop: ShopConfig,
    cfg: BringToFrontSettings,
    *,
    require_foreground: bool = False,
) -> tuple[bool, str]:
    """按配置校验 HWND：非最小化、有足够尺寸；可选要求已是前台。"""
    from apps.core.channels.qianniu.win_hwnd import (
        find_qianniu_main_hwnd_best_effort,
        hwnd_is_user_visible,
    )

    reasons: list[str] = []
    if hwnd <= 0 or not _user32().IsWindow(hwnd):
        return False, "hwnd_invalid"

    if cfg.verify_hwnd_match:
        expected = find_qianniu_main_hwnd_best_effort()
        if expected and hwnd != expected:
            reasons.append(f"hwnd_mismatch got={hwnd} expected={expected}")

    if cfg.verify_not_minimized and _user32().IsIconic(hwnd):
        reasons.append("still_minimized")

    if cfg.verify_window_visible and not hwnd_is_user_visible(hwnd):
        reasons.append("not_user_visible")

    if require_foreground:
        fg = int(_user32().GetForegroundWindow())
        if fg != hwnd:
            reasons.append(f"not_foreground fg={fg}")

    if reasons:
        return False, ",".join(reasons)
    return True, "ok"


def _sleep_ms(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def _click_taskbar_point(
    shop: ShopConfig,
    log: LogFn,
    *,
    x: int | None = None,
    y: int | None = None,
) -> bool:
    import uiautomation as auto

    from apps.core.channels.qianniu.taskbar_ops import (
        check_taskbar_point,
        click_taskbar_restore,
        get_screen_info,
        locate_taskbar_icon_uia,
    )
    from apps.core.channels.qianniu.win_hwnd import hwnd_is_user_visible

    qn = shop.qianniu
    if qn is None:
        return False

    if x is not None and y is not None:
        log(f"置前：任务栏点击（配置步骤坐标）({x},{y}) …")
        try:
            auto.Click(int(x), int(y))
        except Exception as e:
            log(f"置前：任务栏点击失败：{e!r}")
            return False
        _sleep_ms(550)
        from apps.core.channels.qianniu.win_hwnd import find_qianniu_main_hwnd_best_effort

        hwnd = find_qianniu_main_hwnd_best_effort()
        return bool(hwnd and hwnd_is_user_visible(hwnd))

    return click_taskbar_restore(shop, log)


def _try_hwnd_direct_restore(hwnd: int, log: LogFn) -> bool:
    """
    v1.6.8 主路径：有 HWND 时直接用 Win32 把窗口拉到前台，**全程不碰任何坐标**。

    优先级最高——解决两个老问题：
    ① 千牛"假最小化"（IsIconic=False 但不可见）走错到点任务栏序列；
    ② 任务栏图标坐标常变 / YAML 死坐标点偏到别的图标。

    序列：SW_RESTORE（解最小化/隐藏）→ SW_SHOW → AttachThreadInput+SetForegroundWindow
         + 短暂 TOPMOST 翻转（_win32_force_foreground）→ 校验 hwnd_is_user_visible。
    成功返回 True（调用方即可跳过所有任务栏点击）；失败返回 False（退回原序列）。
    """
    from apps.core.channels.qianniu.driver import QianniuDriver
    from apps.core.channels.qianniu.win_hwnd import hwnd_is_user_visible

    if hwnd <= 0 or not _user32().IsWindow(hwnd):
        return False
    try:
        log("置前[主路]：HWND 直接还原（Win32，不碰任务栏坐标）…")
        restore_and_show(hwnd, log)          # SW_RESTORE + SW_SHOW
        _sleep_ms(120)
        try:
            QianniuDriver._win32_force_foreground(hwnd)
        except Exception as e:
            log(f"置前[主路]：force_foreground 异常（继续校验）：{e!r}")
        _sleep_ms(200)
        if hwnd_is_user_visible(hwnd):
            log("置前[主路]：✓ HWND 直接还原成功，窗口已可见（跳过任务栏点击）")
            return True
        log("置前[主路]：HWND 直接还原后仍不可见 → 退回原序列")
        return False
    except Exception as e:
        log(f"置前[主路]：异常 → 退回原序列：{e!r}")
        return False


def _run_step(
    step: BringToFrontStep,
    shop: ShopConfig,
    hwnd: int,
    log: LogFn,
    cfg: BringToFrontSettings,
) -> int:
    """执行单步置前/还原，返回最新 HWND。"""
    from apps.core.channels.qianniu.driver import QianniuDriver
    from apps.core.channels.qianniu.win_hwnd import find_qianniu_main_hwnd_best_effort
    from apps.core.channels.qianniu.window_ops import _foreground_keyboard_nudge

    method = step.method.lower()
    log(f"置前步骤：{method}")

    if method == "taskbar_click":
        xy = step.coords
        if xy:
            _click_taskbar_point(shop, log, x=xy[0], y=xy[1])
        else:
            _click_taskbar_point(shop, log)
    elif method in ("win32_show_normal", "win32_restore", "sw_restore"):
        h = hwnd or find_qianniu_main_hwnd_best_effort() or 0
        if h and cfg.sw_restore_enabled:
            restore_and_show(h, log)
        elif h:
            _user32().ShowWindow(h, SW_SHOW)
    elif method == "win32_setforeground":
        h = hwnd or find_qianniu_main_hwnd_best_effort() or 0
        if h:
            try:
                QianniuDriver._win32_force_foreground(h)
            except Exception as e:
                log(f"置前：SetForeground 异常：{e!r}")
    elif method == "alt_nudge":
        try:
            _foreground_keyboard_nudge()
        except Exception:
            pass
        h = hwnd or find_qianniu_main_hwnd_best_effort() or 0
        if h:
            try:
                QianniuDriver._win32_force_foreground(h)
            except Exception:
                pass
    elif method == "uia_focus":
        qn = shop.qianniu
        if qn:
            try:
                QianniuDriver(qn).focus_main_window()
            except Exception as e:
                log(f"置前：UIA focus 失败：{e!r}")
    else:
        log(f"置前：未知步骤 method={method}，已跳过")

    _sleep_ms(step.wait_after_ms)
    return find_qianniu_main_hwnd_best_effort() or hwnd


def _coerce_steps(steps: tuple[BringToFrontStep, ...]) -> tuple[BringToFrontStep, ...]:
    if not isinstance(steps, tuple):
        raise TypeError(f"置前步骤序列类型错误：{type(steps)!r}")
    return steps


def _run_sequence(
    steps: tuple[BringToFrontStep, ...],
    shop: ShopConfig,
    hwnd: int,
    log: LogFn,
    cfg: BringToFrontSettings,
    *,
    label: str,
    require_foreground: bool = False,
) -> int:
    seq = _coerce_steps(steps)
    log(f"置前：开始 {label}（{len(seq)} 步）")
    h = hwnd
    for step in seq:
        h = _run_step(step, shop, h, log, cfg)
        ok, reason = verify_window_ready(
            h, shop, cfg, require_foreground=require_foreground
        )
        if ok:
            log(f"置前：{label} 中途校验通过 ✓")
            break
        log(f"置前：{label} 中途校验未通过（{reason}），继续下一步…")
    return h


def restore_when_minimized(
    shop: ShopConfig,
    hwnd: int,
    log: LogFn,
    cfg: BringToFrontSettings | None = None,
) -> tuple[int, bool]:
    """
    最小化场景：按 restore_sequence 执行（默认先任务栏再 Win32），
    再 wait_after_restore_ms，并按 verify_conditions 重试。
    """
    cfg = cfg or load_bring_to_front_settings()
    if not cfg.restore_if_minimized:
        restore_and_show(hwnd, log)
        _sleep_ms(cfg.wait_after_restore_ms * 1000)
        ok, _ = verify_window_ready(hwnd, shop, cfg)
        return hwnd, ok

    attempts = 1 + cfg.verify_retry_limit
    h = hwnd
    for attempt in range(1, attempts + 1):
        if attempt > 1 and cfg.verify_fail_action == "retry_restore":
            log(f"置前：还原校验失败，重试还原 ({attempt}/{attempts})…")

        h = _run_sequence(
            cfg.restore_sequence,
            shop,
            h,
            log,
            cfg,
            label="最小化还原序列",
            require_foreground=False,
        )
        extra_ms = cfg.wait_after_restore_ms * 1000
        if extra_ms > 0:
            log(f"置前：还原后额外等待 {cfg.wait_after_restore_ms:.2f}s（窗口动画）…")
            _sleep_ms(extra_ms)

        ok, reason = verify_window_ready(h, shop, cfg)
        if ok:
            log("置前：还原校验通过（非最小化 + 窗口可见 + HWND 匹配）✓")
            return h, True
        log(f"置前：还原校验未通过（{reason}）")
        if cfg.verify_fail_action != "retry_restore" or attempt >= attempts:
            break

    return h, False


def bring_visible_to_foreground(
    shop: ShopConfig,
    hwnd: int,
    log: LogFn,
    cfg: BringToFrontSettings | None = None,
) -> tuple[int, bool]:
    """窗口已可见但不在前台：仅走 foreground_sequence。"""
    cfg = cfg or load_bring_to_front_settings()
    h = _run_sequence(
        cfg.foreground_sequence,
        shop,
        hwnd,
        log,
        cfg,
        label="可见窗口置前序列",
        require_foreground=True,
    )
    # 等 Windows 焦点切换真正落地再校验（TOPMOST 技巧可能让瞬时校验误判）
    _sleep_ms(150)
    ok, reason = verify_window_ready(h, shop, cfg, require_foreground=True)
    if ok:
        log("置前：前台校验通过 ✓")
    else:
        log(f"置前：前台校验未通过（{reason}）")

    # v1.6.1：抢焦点检测 + 重试。某些场景下（如主理人正在动其它窗口）千牛刚到前台
    # 又被其它窗口抢走 → 等 0.3s 二次校验，发现前台变了立即重试一次（最多 2 次）。
    for retry_i in range(2):
        _sleep_ms(300)
        ok2, reason2 = verify_window_ready(h, shop, cfg, require_foreground=True)
        if ok2:
            if not ok:
                log(f"置前：抢焦点重试后校验通过 ✓ (retry={retry_i+1})")
                ok = True
            break
        log(f"置前：检测到焦点被抢回（{reason2}）→ 重新执行可见窗口置前序列 retry={retry_i+1}/2")
        h = _run_sequence(
            cfg.foreground_sequence,
            shop, h, log, cfg,
            label=f"可见窗口置前序列(重试{retry_i+1})",
            require_foreground=True,
        )
        _sleep_ms(150)
        ok, _ = verify_window_ready(h, shop, cfg, require_foreground=True)
        if ok:
            log(f"置前：抢焦点重试 {retry_i+1} 后校验通过 ✓")
            break
    return h, ok


def _maybe_pin_window(shop: ShopConfig, hwnd: int, log: LogFn) -> None:
    """
    v1.5.x：置前完成后，按 BaseSettings.pin_window_enabled 校验/矫正窗口位置。

    设计为"软附加"：任何异常都吞掉只 log，绝不影响 prepare_qianniu_for_capture 的返回。
    """
    try:
        from apps.core.configs.base_settings import load_base_settings, to_pin_settings
        from apps.core.channels.qianniu.window_pin import ensure_pinned_if_drifted
        cfg = to_pin_settings(load_base_settings())
        if not cfg.enabled:
            return
        ensure_pinned_if_drifted(hwnd, cfg, log)
    except Exception as e:
        log(f"窗口锁定（置前后校验）异常（已忽略）：{e!r}")


def _record_bring_to_front_failure(
    *, trigger: str, reason: str, hwnd: int, log: LogFn,
) -> None:
    """v1.6.0：把置前失败写 jsonl 留证，24h 累计 ≥5 次时调用方应暂停接待。"""
    try:
        import json
        import time
        from pathlib import Path
        try:
            from apps.core.runtime_paths import data_dir
            base = Path(data_dir())
        except Exception:
            base = Path.cwd() / "data"
        out_dir = base / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": int(time.time()),
            "trigger": str(trigger or ""),
            "reason": str(reason or ""),
            "hwnd": int(hwnd or 0),
        }
        with open(out_dir / "bring_to_front_failures.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(
            f"[bring_to_front] ⚠ 置前失败已记录 reason={reason!r} "
            f"trigger={trigger!r}；请检查任务栏图标坐标 / 千牛进程"
        )
    except Exception:
        pass


def prepare_qianniu_for_capture(
    shop: ShopConfig,
    trigger: str,
    log: LogFn,
) -> bool:
    """
    接待截图前置前总入口。
    返回 True 表示窗口已通过还原/置前校验（非最小化且用户可见）。

    v1.6.0：返回 False 时写 dist/data/debug/bring_to_front_failures.jsonl。
    """
    result = _prepare_qianniu_for_capture_impl(shop, trigger, log)
    if not result:
        _record_bring_to_front_failure(
            trigger=trigger, reason="prepare_returned_false", hwnd=0, log=log,
        )
    return result


def _prepare_qianniu_for_capture_impl(
    shop: ShopConfig,
    trigger: str,
    log: LogFn,
) -> bool:
    """内层实现（v1.6.0 wrapper 调用此函数）。"""
    from apps.core.channels.qianniu.taskbar_ops import check_taskbar_point, get_screen_info
    from apps.core.channels.qianniu.win_hwnd import (
        find_qianniu_main_hwnd_best_effort,
        hwnd_is_user_visible,
    )
    from apps.core.channels.qianniu.window_ops import (
        _foreground_status_line,
        _is_qianniu_foreground,
    )

    cfg = load_bring_to_front_settings()
    qn = shop.qianniu
    if qn is None:
        return False

    tp = qn.taskbar_icon_point
    cfg_x = int(tp.x) if tp is not None else 0
    cfg_y = int(tp.y) if tp is not None else 0
    tb_check = check_taskbar_point(cfg_x, cfg_y, screen=get_screen_info())
    log(
        f"置前开始 trigger={trigger} | YAML任务栏=({cfg_x},{cfg_y}) "
        f"校验={'通过' if tb_check.ok else '未通过'}"
    )
    if tb_check.reasons:
        for r in tb_check.reasons:
            log(f"  坐标说明：{r}")
    log(_foreground_status_line(shop))

    hwnd = find_qianniu_main_hwnd_best_effort() or 0
    if not hwnd:
        log("置前：未找到千牛 HWND，尝试任务栏还原…")
        _click_taskbar_point(shop, log)
        hwnd = find_qianniu_main_hwnd_best_effort() or 0
        if not hwnd:
            log("置前失败：千牛未启动或无法枚举窗口")
            return False

    user32 = _user32()

    # v1.6.8 主路径：先用 HWND 直接还原（不碰任何坐标）。成功即结束，
    # 不再走任务栏点击/死坐标，从根上解决「叫不出窗口 + 任务栏坐标老变」。
    if _is_qianniu_foreground(shop) and not user32.IsIconic(hwnd):
        log("千牛已在前台且非最小化 ✓（跳过置前）")
        _maybe_pin_window(shop, hwnd, log)
        return True
    if _try_hwnd_direct_restore(hwnd, log):
        # 还原可见后再尽量抢到前台（仍不碰坐标）
        if not _is_qianniu_foreground(shop):
            bring_visible_to_foreground(shop, hwnd, log, cfg)
        _maybe_pin_window(shop, hwnd, log)
        log(_foreground_status_line(shop))
        return _is_qianniu_foreground(shop) or hwnd_is_user_visible(hwnd)

    iconic = bool(cfg.minimized_check and user32.IsIconic(hwnd))

    if iconic:
        log("置前：检测到最小化（IsIconic），走还原序列（优先任务栏点击）")
        hwnd, restored = restore_when_minimized(shop, hwnd, log, cfg)
        if not restored:
            log(
                "⚠ 置前：最小化还原未通过校验，后续会话点击/OCR 可能落空；"
                "请重校 taskbar_icon_point 或检查 restore_sequence"
            )
        log(_foreground_status_line(shop))
        if _is_qianniu_foreground(shop):
            _maybe_pin_window(shop, hwnd, log)  # v1.5.6+: 还原后必须矫正窗口位置，否则 yaml 坐标全错
            return restored or hwnd_is_user_visible(hwnd)
        if hwnd_is_user_visible(hwnd):
            bring_visible_to_foreground(shop, hwnd, log, cfg)
            _maybe_pin_window(shop, hwnd, log)
            return _is_qianniu_foreground(shop) or hwnd_is_user_visible(hwnd)
        # 即便不在前台，只要还原成功也要矫正位置（视觉哨兵后续会用到 ROI）
        if restored:
            _maybe_pin_window(shop, hwnd, log)
        return restored

    if _is_qianniu_foreground(shop):
        log("千牛已在前台且非最小化 ✓（跳过任务栏点击）")
        _maybe_pin_window(shop, hwnd, log)
        return True

    if hwnd_is_user_visible(hwnd):
        log("置前：千牛可见但未在前台，走可见窗口置前序列")
        hwnd, _ = bring_visible_to_foreground(shop, hwnd, log, cfg)
        if not _is_qianniu_foreground(shop):
            log("置前：Win32 序列后仍未到前台，尝试任务栏点击拉起…")
            _click_taskbar_point(shop, log)
            _sleep_ms(cfg.wait_after_restore_ms * 1000)
            hwnd = find_qianniu_main_hwnd_best_effort() or hwnd
            bring_visible_to_foreground(shop, hwnd, log, cfg)
        log(_foreground_status_line(shop))
        _maybe_pin_window(shop, hwnd, log)  # v1.5.6+: 可见窗口路径也要矫正
        return _is_qianniu_foreground(shop)

    log("置前：窗口不可见且非最小化枚举态，尝试最小化还原序列")
    hwnd, restored = restore_when_minimized(shop, hwnd, log, cfg)
    log(_foreground_status_line(shop))
    if restored:
        _maybe_pin_window(shop, hwnd, log)  # v1.5.6+: 第二种还原路径也要矫正
    return restored or _is_qianniu_foreground(shop)


def wait_window_ready(
    hwnd: int,
    *,
    timeout_s: float | None = None,
    poll_s: float | None = None,
    log: LogFn | None = None,
    shop: ShopConfig | None = None,
) -> bool:
    """窗口非最小化、用户可见，且已成为前台后返回 True。"""
    if hwnd <= 0:
        return False
    cfg = load_bring_to_front_settings()
    deadline = time.monotonic() + float(timeout_s if timeout_s is not None else cfg.timeout_s)
    interval = float(poll_s if poll_s is not None else cfg.poll_s)
    user32 = _user32()

    while time.monotonic() < deadline:
        if not user32.IsWindow(hwnd):
            break
        if shop is not None:
            ok, reason = verify_window_ready(
                hwnd, shop, cfg, require_foreground=True
            )
            if ok:
                return True
            if reason == "still_minimized" and cfg.sw_restore_enabled:
                restore_and_show(hwnd, log)
        else:
            if user32.IsIconic(hwnd):
                if cfg.sw_restore_enabled:
                    restore_and_show(hwnd, log)
            else:
                fg = int(user32.GetForegroundWindow())
                if fg == hwnd:
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if w >= 400 and h >= 300:
                            return True
        time.sleep(interval)

    if log:
        log(
            f"置前：窗口就绪等待超时（{cfg.timeout_s:.1f}s），"
            "后续会话点击可能落空"
        )
    return False
