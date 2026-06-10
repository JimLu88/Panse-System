"""
左侧会话列表 ROI：基于像素差分 + 黄色/红色启发式，在「叮咚」前尝试点选未读会话。

row 含义：会话列表 ROI 截图中的「像素行号」(0=ROI 顶部)，不是「第 N 个会话」。
点击时用该行附近高亮像素的重心，并估算会话行高，避免只偏移几个像素未点到昵称区。
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
import uiautomation as auto

from apps.core.capture.screen import ScreenCapture
from apps.core.configs.loader import Rect, ShopConfig
from apps.core.orchestrator.models import NewMessageEvent

LogFn = Callable[[str], None]


def _yellow_mask_rgb(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    return (r > 140) & (g > 100) & (b < 170) & ((r + g) > (b + 70))


def _yellow_mask_hsv(rgb: np.ndarray, lower: tuple[int, int, int], upper: tuple[int, int, int]) -> np.ndarray:
    import cv2

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    lo = np.array(lower, dtype=np.uint8)
    hi = np.array(upper, dtype=np.uint8)
    return cv2.inRange(hsv, lo, hi) > 0


def _yellow_mask(rgb: np.ndarray) -> np.ndarray:
    from apps.core.ai.input_quality_gate import load_session_detection_settings

    sd = load_session_detection_settings()
    if sd.highlight_method == "hsv":
        try:
            return _yellow_mask_hsv(rgb, sd.hsv_lower, sd.hsv_upper)
        except Exception:
            pass
    return _yellow_mask_rgb(rgb)


def _row_sums(mask: np.ndarray) -> np.ndarray:
    return np.sum(mask, axis=1).astype(np.float64)


def _red_badge_mask(rgb: np.ndarray) -> np.ndarray:
    # v1.6.18：实测千牛未读角标/红色计时器是「纯红」(R≈255,G/B<100)，
    # 而买家橙黄色卡通头像 (R高,G中等>100) 之前被旧 orange 规则误判成角标，
    # 导致红标恒选中头像行(如 pixelRow=134)而非顶部真新消息。
    # 收紧为纯红：排除橙色头像，只命中真角标/红计时器。
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    return (r > 200) & (g < 100) & (b < 100) & ((r - g) > 120) & ((r - b) > 120)


def _estimate_session_row_height_px(roi_height: int) -> int:
    return max(44, min(78, roi_height // 11))


def _top_row_limit(h: int, sd: object, *, for_audio: bool) -> int | None:
    if not for_audio:
        return None
    ratio = float(getattr(sd, "audio_yellow_top_row_ratio", 0.28))
    return max(8, int(h * max(0.12, min(0.5, ratio))))


def _format_list_roi(rect: Rect) -> str:
    return (
        f"list_roi=({rect.left},{rect.top})-({rect.right},{rect.bottom}) "
        f"size={rect.width()}x{rect.height()}"
    )


def _maybe_save_session_list_debug(rgb: np.ndarray, tag: str, log: LogFn) -> None:
    try:
        from apps.core.ai.input_quality_gate import load_debug_snapshot_settings
        from apps.core.runtime_paths import default_sqlite_db_path
        from PIL import Image

        if not load_debug_snapshot_settings().save_snapshot:
            return
        base = default_sqlite_db_path().parent / "debug"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"session_list_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        Image.fromarray(rgb).save(path)
        log(f"未读切换诊断：会话列表截图已保存 {path}")
    except Exception as e:
        log(f"未读切换诊断：列表截图保存失败 {e!r}")


def _log_row_candidates(
    rows: np.ndarray,
    *,
    threshold: float,
    max_row_index: int | None,
    log: LogFn,
    label: str,
) -> None:
    hits = np.flatnonzero(rows >= threshold)
    if max_row_index is not None:
        hits = hits[hits <= max_row_index]
    if hits.size == 0:
        log(f"未读切换：{label} 无达阈行 (threshold={threshold:.0f} max={float(rows.max()):.0f})")
        return
    preview = ", ".join(f"r{int(r)}:{rows[int(r)]:.0f}" for r in hits[:6])
    log(f"未读切换：{label} 候选行 [{preview}]")


def pick_yellow_row(
    rows_yel: np.ndarray,
    *,
    threshold: float,
    pick_mode: str = "topmost",
    max_row_index: int | None = None,
) -> tuple[int | None, float]:
    if rows_yel.size == 0:
        return None, 0.0
    yel_max = float(rows_yel.max())
    if yel_max < threshold:
        return None, yel_max
    mode = (pick_mode or "topmost").lower()
    if mode == "max":
        scoped = rows_yel.copy()
        if max_row_index is not None:
            scoped[max_row_index + 1 :] = 0.0
        row = int(np.argmax(scoped))
        if scoped[row] < threshold:
            return None, yel_max
        return row, float(rows_yel[row])
    hits = np.flatnonzero(rows_yel >= threshold)
    if max_row_index is not None:
        hits = hits[hits <= max_row_index]
    if hits.size:
        row = int(hits[0])
        return row, float(rows_yel[row])
    # 过滤后无命中行 → 不点击（避免点到当前选中会话的高亮）
    return None, yel_max


def _click_session_row(
    *,
    rect: Rect,
    pixel_row: int,
    rgb: np.ndarray,
    mask: np.ndarray,
    log: LogFn,
    sd: object,
    trigger: str,
    detail: str,
) -> bool:
    h, w = rgb.shape[:2]
    row_h = _estimate_session_row_height_px(h)
    y0 = max(0, pixel_row - row_h // 3)
    y1 = min(h, pixel_row + (2 * row_h) // 3)
    band = mask[y0:y1, :]
    ys, xs = np.where(band)
    hit_px = int(xs.size) if xs.size > 0 else 0

    # X 坐标：始终点击会话列表 ROI 水平中心偏右（昵称文字区域），
    # 不用黄色像素的 X 重心——黄条/红标往往偏左侧头像区，容易点歪。
    cx = rect.left + max(w // 2, 40)

    # Y 坐标：优先用黄色像素在 band 内的 Y 重心（更贴近闪烁行中心），
    # 无黄色像素时退回 pixel_row + 半行高
    if ys.size > 0:
        cy = rect.top + y0 + int(ys.mean())
    else:
        cy = rect.top + pixel_row + row_h // 2

    try:
        auto.Click(int(cx), int(cy))
        log(
            f"未读会话切换：{detail} 已点击屏幕({cx},{cy}) "
            f"pixelRow={pixel_row} bandY={y0}-{y1} rowH≈{row_h} "
            f"maskPx={hit_px} {_format_list_roi(rect)} trigger={trigger}"
        )
        time.sleep(float(getattr(sd, "post_switch_click_sleep_s", 0.45)))
        return True
    except Exception as e:
        log(f"未读会话切换点击失败：{e!r} ({cx},{cy})")
        return False


def _scan_yellow_row(
    rgb: np.ndarray,
    *,
    sd: object,
    for_audio: bool,
) -> tuple[int | None, float, np.ndarray]:
    rows_yel = _row_sums(_yellow_mask(rgb))
    threshold = (
        float(getattr(sd, "yel_max_first_frame_audio", 10.0))
        if for_audio
        else float(sd.yel_max_first_frame_visual)
    )
    max_row = _top_row_limit(rgb.shape[0], sd, for_audio=for_audio)
    row, yel_max = pick_yellow_row(
        rows_yel,
        threshold=threshold,
        pick_mode=str(getattr(sd, "yellow_row_pick", "topmost")),
        max_row_index=max_row,
    )
    return row, yel_max, rows_yel


def _two_pass_yellow_switch(
    cap: ScreenCapture,
    rect: Rect,
    ev: NewMessageEvent,
    log: LogFn,
    sd: object,
    curr: np.ndarray,
) -> tuple[np.ndarray | None, bool, float]:
    """
    v1.6.19 多帧轮询黄条扫描（替代原「两次间隔 0.12s」）：

    千牛未读黄条在 0~4 秒是「黄白交替闪烁」，5 秒后才恒定黄。
    原来只扫 2 帧、间隔 0.12s，极易两帧都落在「白」相→ yelMax=0 漏判。
    现改为在 poll_window_s 内每隔 poll_interval_s 扫一次，
    **任意一帧捕到达阈黄条就立即采用**（闪烁期偶亮也能抓，5s 后恒亮必中）。
    捕到即返回，不必等满；全程未捕到才判失败。
    """
    rounds = max(1, int(getattr(sd, "yellow_poll_rounds", 8)))
    poll_interval = max(0.05, float(getattr(sd, "yellow_poll_interval_s", 0.5)))
    max_row = _top_row_limit(curr.shape[0], sd, for_audio=True)
    if max_row is not None:
        log(f"未读切换：黄条仅在 ROI 像素行 0–{max_row} 内识别")
    log(f"未读切换：多帧轮询黄条（最多 {rounds} 帧 × {poll_interval:.2f}s，捕到即用）…")

    best_yel = 0.0
    frame = curr
    for i in range(rounds):
        if i > 0:
            time.sleep(poll_interval)
            try:
                frame = cap.grab_rgb(rect)
            except Exception as e:
                log(f"会话列表 ROI 第{i+1}帧截图失败：{e!r}")
                continue
        row, yel, rows = _scan_yellow_row(frame, sd=sd, for_audio=True)
        best_yel = max(best_yel, yel)
        if row is not None:
            log(f"未读切换：黄条第{i+1}帧命中 → pixelRow={row} yelMax={yel:.0f}（捕到即用）")
            ok = _click_session_row(
                rect=rect,
                pixel_row=row,
                rgb=frame,
                mask=_yellow_mask(frame),
                log=log,
                sd=sd,
                trigger=ev.trigger or "",
                detail=f"黄条第{i+1}帧 pixelRow={row}",
            )
            return frame, ok, yel
        # 未命中只在首帧/末帧打详情，避免刷屏
        if i == 0 or i == rounds - 1:
            _log_row_candidates(
                rows, threshold=float(sd.yel_max_first_frame_audio),
                max_row_index=max_row, log=log, label=f"黄条第{i+1}帧",
            )

    log(f"未读切换：多帧轮询均未达阈值（best yelMax={best_yel:.0f}）")
    return frame, False, best_yel


def _try_red_badge_switch(
    cap: ScreenCapture,
    rect: Rect,
    ev: NewMessageEvent,
    log: LogFn,
    sd: object,
    curr: np.ndarray | None,
    *,
    silent_if_miss: bool = False,
) -> tuple[np.ndarray | None, bool]:
    if not bool(getattr(sd, "enable_red_badge_switch", True)):
        return curr, False
    if curr is None:
        try:
            curr = cap.grab_rgb(rect)
        except Exception as e:
            log(f"红色未读检测截图失败：{e!r}")
            return None, False

    h, w = curr.shape[:2]
    # 扫描前 N 像素行：需覆盖红色角标 + 红色秒数计时器（如「30秒」「88秒」）
    max_scan_row = int(getattr(sd, "red_badge_max_pixel_row", 140))
    max_scan_row = min(h - 1, max(20, max_scan_row))
    roi = curr[: max_scan_row + 1, :, :]
    rows_red = _row_sums(_red_badge_mask(roi))
    threshold = float(getattr(sd, "red_badge_min_pixels", 18.0))

    hits = np.flatnonzero(rows_red >= threshold)
    if hits.size == 0:
        if not silent_if_miss:
            log(
                f"未读切换：ROI 顶部 0–{max_scan_row}px 内无红色角标 "
                f"(threshold={threshold:.0f})"
            )
        return curr, False

    row = int(hits[0])
    red_px = float(rows_red[row])
    preview = ", ".join(f"r{int(r)}:{rows_red[int(r)]:.0f}" for r in hits[:6])
    log(f"未读切换：红标候选 [{preview}] → 点 pixelRow={row}")

    ok = _click_session_row(
        rect=rect,
        pixel_row=row,
        rgb=curr,
        mask=_red_badge_mask(curr),
        log=log,
        sd=sd,
        trigger=ev.trigger or "",
        detail=f"红标兜底 pixelRow={row} redPx={red_px:.0f}",
    )
    return curr, ok


def retry_switch_top_unread_session(shop: ShopConfig, log: LogFn) -> bool:
    """时间校对失败时：再做一轮「两次黄条 + 红标兜底」。"""
    qn = shop.qianniu
    if qn is None or not qn.unread_session_switch:
        return False
    rect = qn.session_list_rect
    if rect is None or rect.width() < 8:
        return False
    from apps.core.ai.input_quality_gate import load_session_detection_settings

    sd = load_session_detection_settings()
    cap = ScreenCapture()
    ev = NewMessageEvent(source_id="", session_id="", trigger="audio_peak")
    log("未读切换补救：时间过旧，重新两次黄条扫描…")
    try:
        curr = cap.grab_rgb(rect)
    except Exception as e:
        log(f"补救截图失败：{e!r}")
        return False
    _maybe_save_session_list_debug(curr, "retry", log)
    _, ok, _ = _two_pass_yellow_switch(cap, rect, ev, log, sd, curr)
    if ok:
        return True
    curr2, ok = _try_red_badge_switch(cap, rect, ev, log, sd, curr, silent_if_miss=True)
    return ok


def _audio_first_frame_switch(
    cap: ScreenCapture,
    rect: Rect,
    ev: NewMessageEvent,
    log: LogFn,
    sd: object,
) -> tuple[np.ndarray | None, bool]:
    """叮咚：settle →（v1.6.18 优先）右下角新消息气泡 → 两次黄条（第2次优先）→ 红标兜底。"""
    settle = float(getattr(sd, "session_list_settle_wait_s", 0.0))
    if settle > 0:
        log(f"未读切换：等待会话列表渲染 {settle:.2f}s …")
        time.sleep(settle)

    # v1.6.18：右下角悬浮新消息气泡优先——点它直接进对应会话，
    # 比猜会话列表哪一行（黄条/红标常点错旧会话）更准。失败再走黄条/红标。
    if bool(getattr(sd, "minibubble_first", True)):
        try:
            from apps.core.channels.qianniu.minibubble_fallback import try_click_minibubble
            _mini = try_click_minibubble(log=log)
            if _mini.success:
                log(
                    f"未读切换：✓ 右下角气泡优先命中 nick={_mini.matched_nick!r} "
                    f"clicked={_mini.clicked_at}（跳过黄条/红标）"
                )
                try:
                    return cap.grab_rgb(rect), True
                except Exception:
                    return None, True
            log(f"未读切换：右下角气泡未命中（{_mini.reason}），回退黄条/红标")
        except Exception as e:
            log(f"未读切换：右下角气泡优先异常（忽略，回退黄条）：{e!r}")

    try:
        first_curr = cap.grab_rgb(rect)
    except Exception as e:
        log(f"会话列表 ROI 截图失败：{e!r}")
        return None, False

    _maybe_save_session_list_debug(first_curr, "audio_peak", log)
    log(f"未读切换：{_format_list_roi(rect)}")

    curr, yel_ok, yel_max = _two_pass_yellow_switch(cap, rect, ev, log, sd, first_curr)
    yel_weak = float(getattr(sd, "yel_weak_match_audio", 25.0))

    if yel_ok and yel_max >= yel_weak:
        # 强黄条命中（像素充足），直接信任
        return curr, True

    if yel_ok:
        # 弱黄条命中（像素数不足 yel_weak_match_audio）→ 同时检查红标
        log(
            f"未读切换：黄条弱命中 yelMax={yel_max:.0f} < {yel_weak:.0f}，"
            f"继续检查红标…"
        )
        red_curr, red_ok = _try_red_badge_switch(
            cap, rect, ev, log, sd,
            curr if curr is not None else first_curr,
        )
        if red_ok:
            return red_curr, True
        # 红标也未命中 → 信任弱黄条点击
        return curr, True

    log("未读切换：黄条未命中，尝试红标兜底（仅 ROI 最顶部区域）…")
    red_curr, red_ok = _try_red_badge_switch(
        cap, rect, ev, log, sd, curr if curr is not None else first_curr,
    )
    if red_ok:
        return red_curr, True
    # v1.6.0 L3 兜底：右下角千牛迷你新消息小气泡 OCR + 点击
    log("未读切换：红标也 miss → L3 兜底（右下角迷你气泡 OCR）…")
    try:
        from apps.core.channels.qianniu.minibubble_fallback import try_click_minibubble
        mini_result = try_click_minibubble(log=log)
        if mini_result.success:
            log(
                f"未读切换：L3 兜底成功 nick={mini_result.matched_nick!r} "
                f"clicked={mini_result.clicked_at}"
            )
            return red_curr, True
        log(f"未读切换：L3 兜底未命中 reason={mini_result.reason}")
    except Exception as e:
        log(f"未读切换：L3 兜底异常：{e!r}")
    return red_curr, False


def maybe_switch_unread_session(
    shop: ShopConfig,
    prev_rgb: np.ndarray | None,
    ev: NewMessageEvent,
    log: LogFn,
) -> tuple[np.ndarray | None, bool]:
    qn = shop.qianniu
    if qn is None or not qn.unread_session_switch:
        return prev_rgb, False
    rect = qn.session_list_rect
    if rect is None or rect.width() < 8 or rect.height() < 8:
        return prev_rgb, False

    cap = ScreenCapture()
    from apps.core.ai.input_quality_gate import load_session_detection_settings

    sd = load_session_detection_settings()

    try:
        curr = cap.grab_rgb(rect)
    except Exception as e:
        log(f"会话列表 ROI 截图失败：{e!r}")
        return prev_rgb, False

    if prev_rgb is None or prev_rgb.shape != curr.shape:
        if ev.trigger == "audio_peak":
            return _audio_first_frame_switch(cap, rect, ev, log, sd)

        row, yel_max, _ = _scan_yellow_row(curr, sd=sd, for_audio=False)
        if row is not None:
            ok = _click_session_row(
                rect=rect,
                pixel_row=row,
                rgb=curr,
                mask=_yellow_mask(curr),
                log=log,
                sd=sd,
                trigger=ev.trigger or "",
                detail=f"首帧黄条 yelMax={yel_max:.0f}",
            )
            return curr, ok
        return curr, False

    yel = _yellow_mask(curr)
    diff = np.mean(np.abs(curr.astype(np.int32) - prev_rgb.astype(np.int32)), axis=2)
    yel_prev = _yellow_mask(prev_rgb)
    novel = yel & ~yel_prev

    rows_novel = _row_sums(novel)
    rows_yel = _row_sums(yel)
    rows_yel_prev = _row_sums(yel_prev)
    rows_spike = _row_sums(diff > 18)

    combined = rows_novel + rows_spike * 0.45
    mean_diff = float(diff.mean())

    if ev.trigger == "audio_peak":
        rows_yel_grew = float(rows_yel.max()) > float(rows_yel_prev.max()) + 12.0
        should_try = (
            mean_diff >= 3.2
            or float(rows_novel.max()) >= 6.0
            or rows_yel_grew
            or float(combined.max()) >= 18.0
        )
    else:
        should_try = (
            mean_diff >= 6.5
            or float(rows_novel.max()) >= 14.0
            or float(combined.max()) >= 35.0
        )

    if not should_try:
        return curr, False

    if ev.trigger == "audio_peak":
        log("未读切换：差分触发，走两次黄条扫描…")
        curr2, ok, _ = _two_pass_yellow_switch(cap, rect, ev, log, sd, curr)
        if ok:
            return curr2, True
        return _try_red_badge_switch(cap, rect, ev, log, sd, curr, silent_if_miss=True)

    max_row = _top_row_limit(curr.shape[0], sd, for_audio=False)
    if max_row is not None:
        combined = combined.copy()
        combined[max_row + 1 :] = 0.0

    row = int(np.argmax(combined))
    if combined[row] < 10.0:
        return curr, False

    ok = _click_session_row(
        rect=rect,
        pixel_row=row,
        rgb=curr,
        mask=(diff > 14) | novel | yel,
        log=log,
        sd=sd,
        trigger=ev.trigger or "",
        detail=f"差分命中 pixelRow={row} meanDiff={mean_diff:.1f}",
    )
    return curr, ok


# ---------- 批量安抚：扫描所有未读行 ----------


def scan_remaining_unread_rows(
    rgb: np.ndarray,
    sd: object,
    max_y_ratio: float = 0.75,
) -> list[int]:
    """扫描会话列表 ROI，返回所有红色未读角标所在的像素行列表（按 Y 升序）。

    每个「会话行」约 44~78 px 高；如果连续多行都有红标像素，
    只取每个行高区间内的第一行，避免同一会话被重复计入。

    max_y_ratio: 只扫描 ROI 顶部该比例的像素行（默认 0.75），
    避免将千牛工具栏底部区域误识别为未读角标。
    """
    h, _w = rgb.shape[:2]
    max_y = int(h * max_y_ratio)
    roi = rgb[:max_y]
    rows_red = _row_sums(_red_badge_mask(roi))
    threshold = float(getattr(sd, "red_badge_min_pixels", 18.0))
    hits = np.flatnonzero(rows_red >= threshold)
    if hits.size == 0:
        return []

    row_h = _estimate_session_row_height_px(h)
    deduped: list[int] = []
    last_row = -row_h
    for r in hits:
        r_int = int(r)
        if r_int - last_row >= row_h // 2:
            deduped.append(r_int)
            last_row = r_int
    return deduped


def has_unread_badge(shop: "ShopConfig", log: LogFn | None = None) -> bool:
    """v1.6.13 Fix A：会话列表是否存在未读红标/黄条（真实新消息信号）。

    用于时间戳判旧（stale）时的安全网：只要列表有未读，就说明确有新消息，
    不能因时间戳误判而丢弃本轮。只读截图，不点击、不改任何状态。
    任何异常都吞掉返回 False（保守：检测失败时不强行放行）。
    """
    try:
        qn = shop.qianniu
        if qn is None or not qn.unread_session_switch:
            return False
        rect = qn.session_list_rect
        if rect is None or rect.width() < 8:
            return False
        from apps.core.ai.input_quality_gate import load_session_detection_settings

        sd = load_session_detection_settings()
        cap = ScreenCapture()
        try:
            rgb = cap.grab_rgb(rect)
        except Exception as e:
            if log:
                log(f"未读检测截图失败（按无未读处理）：{e!r}")
            return False
        # 红标（未读角标，如截图里的红色"51秒"）
        red_rows = scan_remaining_unread_rows(rgb, sd)
        if red_rows:
            if log:
                log(f"未读检测：发现 {len(red_rows)} 处红标 → 判定有未读")
            return True
        # 黄条（当前闪烁未读行）兜底
        try:
            yel_rows = _row_sums(_yellow_mask(rgb))
            yel_thr = float(getattr(sd, "yellow_min_pixels", 15.0))
            if float(yel_rows.max()) >= yel_thr:
                if log:
                    log("未读检测：发现黄条 → 判定有未读")
                return True
        except Exception:
            pass
        return False
    except Exception as e:
        if log:
            log(f"未读检测异常（按无未读处理）：{e!r}")
        return False


def batch_soothe_remaining_unread(
    shop: "ShopConfig",
    soothe_text: str,
    log: LogFn,
    *,
    max_sessions: int = 6,
) -> int:
    """点击每个未读行 → 发送安抚话术，返回实际发送数。

    不对「当前已选中」的会话重复发送——它刚被 Brain 处理过。
    """
    qn = shop.qianniu
    if qn is None or not qn.unread_session_switch:
        return 0
    rect = qn.session_list_rect
    if rect is None or rect.width() < 8:
        return 0

    from apps.core.ai.input_quality_gate import load_session_detection_settings

    sd = load_session_detection_settings()
    cap = ScreenCapture()

    sent = 0
    # v1.3.93 关键修复：已点过的 pixel_row 集合，避免反复对同一买家发"稍等"
    # （千牛红标点击后不会立即消失，scan_remaining_unread_rows 会反复返回同一行，
    # 之前出现过对同一买家连发 6 次"稍等"被淘宝风控的事故）
    clicked_rows: list[int] = []
    SAME_ROW_TOL = 8  # ±8 像素以内视为"同一会话行"（千牛会话行高约 50-60px）

    for _round in range(max_sessions):
        # 每次点击后列表高亮会变化，需要重新截图
        try:
            rgb = cap.grab_rgb(rect)
        except Exception as e:
            log(f"批量安抚截图失败：{e!r}")
            break

        unread_rows = scan_remaining_unread_rows(rgb, sd)
        if not unread_rows:
            log(f"批量安抚：未发现更多未读会话 (已发送 {sent} 条)")
            break

        # 跳过已点击过的 row（防止千牛未清除红标导致反复对同一买家发送）
        fresh_rows = [
            r for r in unread_rows
            if not any(abs(r - cr) <= SAME_ROW_TOL for cr in clicked_rows)
        ]
        if not fresh_rows:
            log(
                f"批量安抚：剩余未读行 {unread_rows} 均已点击过（容差±{SAME_ROW_TOL}px），"
                f"千牛红标未清除视为已处理，提前结束（已发送 {sent} 条）"
            )
            break

        target_row = fresh_rows[0]
        ok = _click_session_row(
            rect=rect,
            pixel_row=target_row,
            rgb=rgb,
            mask=_red_badge_mask(rgb),
            log=log,
            sd=sd,
            trigger="batch_soothe",
            detail=f"批量安抚第{sent + 1}个 pixelRow={target_row}",
        )
        if not ok:
            log(f"批量安抚：点击第{sent + 1}个会话失败")
            break
        clicked_rows.append(target_row)

        # 等待聊天区切换完成
        time.sleep(max(0.5, float(getattr(sd, "post_switch_extra_delay_s", 0.7))))

        # 发送安抚话术
        try:
            from apps.core.channels.qianniu.driver import QianniuDriver

            drv = QianniuDriver(shop.qianniu)
            drv.paste_text(soothe_text)
            drv.press_enter()
            sent += 1
            log(f"批量安抚：已向第{sent}个未读会话发送「{soothe_text}」")
        except Exception as e:
            log(f"批量安抚发送失败：{e!r}")
            break

        # 短暂等待，让列表状态更新
        time.sleep(0.4)

    return sent
