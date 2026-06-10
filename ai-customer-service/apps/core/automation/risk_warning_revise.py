"""
v1.6.0 风控弹窗自救 + LLM 重新生成回复。

触发场景：千牛/淘宝弹出"服务态度提醒/请勿重复提问"等风控弹窗时。

  L1：UIA 找"返回修改"按钮（扩展关键词 + 包含匹配）→ 点击
  L2：L1 失败 → visual_button_locator（Claude 视觉）找按钮坐标 → 点击
  L3：L2 失败 → 暂停所有自动接待 + 弹通知给主理人 + 写 jsonl 留证

LLM 重新生成（点中"返回修改"之后）：
  1. 调用方传入买家最近 3 条消息 + 原回复 + 风控警告文本
  2. 调 LLM with 风控规则提示词（禁用"请问有什么可以帮到您"等关键词）
  3. 本地后处理：含禁用词 → 重写 1 次 → 再含直接放弃
  4. 入队 with bypass_dedup=True（不计 SessionReplyBudget 配额，但受总上限）

冲突 G：同会话连续 2 次未自救 → 强制锁会话直到人工解锁

日志前缀：[risk_warn]
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("apps.core.automation.risk_warning_revise")

DISABLED_PHRASES: tuple[str, ...] = (
    "请问有什么可以帮到您",
    "请问有什么可以帮您",
    "有什么可以帮您",
    "有什么可以帮到您",
    "您好在的呢",
    "您好我在的",
)


def _unresolved_jsonl_path() -> Path:
    """优先 dist/data/debug，回退当前工作目录下 data/debug。"""
    try:
        from apps.core.runtime_paths import data_dir
        return Path(data_dir()) / "debug" / "risk_warning_unresolved.jsonl"
    except Exception:
        return Path.cwd() / "data" / "debug" / "risk_warning_unresolved.jsonl"


def _append_jsonl(rec: dict) -> None:
    try:
        p = _unresolved_jsonl_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
    except Exception as e:
        _log.warning("[risk_warn] jsonl 写失败：%r", e)


# 同会话连续触发计数（plan 冲突 G）
_session_consecutive_count: dict[str, int] = defaultdict(int)
_session_lock_until: dict[str, float] = {}
_state_lock = threading.Lock()

CONSECUTIVE_THRESHOLD = 2
LOCK_DURATION_S = 3600.0


def _is_session_locked(session_key: str) -> bool:
    with _state_lock:
        until = _session_lock_until.get(session_key, 0.0)
        return until > time.monotonic()


def _lock_session(session_key: str, reason: str = "") -> None:
    with _state_lock:
        _session_lock_until[session_key] = time.monotonic() + LOCK_DURATION_S
    _log.error(
        "[risk_warn] 会话锁定 %s 时长 %.0fs；原因：%s",
        session_key, LOCK_DURATION_S, reason,
    )


def unlock_session_manually(session_key: str) -> None:
    """UI 提供手动解锁接口。"""
    with _state_lock:
        _session_lock_until.pop(session_key, None)
        _session_consecutive_count[session_key] = 0
    _log.info("[risk_warn] 会话已手动解锁：%s", session_key)


# v1.6.26：截图+OCR 兜底所需的风控上下文关键词。
# 必须**同时**看到其一 +「返回修改」才点击，确保是真风控弹窗（防全屏误触）。
_RISK_CTX_KEYWORDS: tuple[str, ...] = (
    "服务态度提醒",
    "重复消息",
    "重复提问",
    "建议修改",
    "消费者反感",
    "引起消费者",
    "修改后发送",
)


def _full_screen_ocr_spans():
    """全屏截图 → OCR spans（屏幕绝对坐标）；失败返回 []。"""
    try:
        import ctypes

        from apps.core.capture.screen import Rect, ScreenCapture
        from apps.core.ocr.dual_engine import get_dual_ocr_engine

        u32 = ctypes.windll.user32
        sw = int(u32.GetSystemMetrics(0))
        sh = int(u32.GetSystemMetrics(1))
        img = ScreenCapture().grab_rgb(Rect(left=0, top=0, right=sw, bottom=sh))
        if img is None:
            return []
        return get_dual_ocr_engine().recognize(img).spans
    except Exception as e:
        _log.warning("[risk_warn] 全屏OCR异常：%r", e)
        return []


def _ocr_risk_present() -> bool:
    """全屏 OCR 仅检测是否存在风控提示上下文（不点击），供 loop 兜底门控用。"""
    spans = _full_screen_ocr_spans()
    if not spans:
        return False
    joined = " ".join((getattr(s, "text", "") or "") for s in spans)
    return any(k in joined for k in _RISK_CTX_KEYWORDS)


# L0：全屏截图 + OCR 点"返回修改"（比 UIA 可靠，千牛 Electron/CEF 弹窗 UIA 常枚举不到）
def _try_l0_screenshot_ocr_click() -> bool:
    """
    v1.6.26 L0：全屏截图 → OCR。**必须同时**命中风控上下文关键词 +「返回修改」才点击，
    否则一律不点（这是用户要的"先看截图再点、只命中这四个字"的可靠路径，且不误触）。
    """
    spans = _full_screen_ocr_spans()
    if not spans:
        return False
    joined = " ".join((getattr(s, "text", "") or "") for s in spans)
    if not any(k in joined for k in _RISK_CTX_KEYWORDS):
        # 没看到风控提示上下文 → 绝不点（防把别处的"返回修改"字样误点）
        return False
    target = None
    for s in spans:
        t = (getattr(s, "text", "") or "").strip().replace(" ", "")
        if "返回修改" in t:
            target = s
            break
    if target is None:
        _log.info("[risk_warn] L0：看到风控提示但未OCR到「返回修改」按钮 → 转 UIA/视觉")
        return False
    bbox = getattr(target, "bbox", None)
    if not bbox or len(bbox) < 4:
        return False
    cx = int((bbox[0] + bbox[2]) / 2)
    cy = int((bbox[1] + bbox[3]) / 2)
    try:
        import ctypes

        u32 = ctypes.windll.user32
        _log.info(
            "[risk_warn] L0：截图OCR命中风控提示+「返回修改」→ 点击屏幕(%d,%d)", cx, cy
        )
        u32.SetCursorPos(cx, cy)
        time.sleep(0.08)
        u32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.03)
        u32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(0.3)
        return True
    except Exception as e:
        _log.warning("[risk_warn] L0 点击异常：%r", e)
        return False


# L1：UIA 点"返回修改"
def _try_l1_uia_click() -> bool:
    try:
        from apps.core.automation.popup_dismiss import (
            find_risk_warning_popups,
            _enumerate_buttons,
            _click_button_by_names,
            _RETURN_TO_EDIT_NAMES,
        )
    except ImportError:
        return False

    popups = find_risk_warning_popups()
    if not popups:
        return False
    info = popups[0]
    buttons = _enumerate_buttons(info.window_ctrl) if info.window_ctrl else []
    name = _click_button_by_names(buttons, _RETURN_TO_EDIT_NAMES)
    if name:
        _log.info("[risk_warn] L1 UIA 等值点中 %r", name)
        return True
    for ctrl, btn_name in buttons:
        if "返回修改" in btn_name:
            try:
                ctrl.Click(simulateMove=False)
                _log.info("[risk_warn] L1 UIA 包含匹配点中 %r", btn_name)
                return True
            except Exception:
                continue
    return False


# L2：视觉 LLM 找"返回修改"
def _try_l2_visual_click() -> bool:
    try:
        from apps.core.automation.visual_button_locator import locate_and_click_button
    except ImportError:
        _log.warning("[risk_warn] L2 跳过：visual_button_locator 不可用")
        return False
    return bool(locate_and_click_button("返回修改"))


# L3：暂停接待 + 通知人工
def _trigger_l3_human_escalation(
    info: dict,
    *,
    on_pause_callback: Callable[[str], None] | None = None,
) -> None:
    _append_jsonl({
        "ts": int(time.time()),
        "type": "L3_human_escalation",
        **info,
    })
    if on_pause_callback is not None:
        try:
            on_pause_callback(
                f"⚠ 风控弹窗 60 秒未自救：{info.get('title', '')[:60]}\n"
                "已暂停自动接待，请人工点'返回修改'，然后在 UI 解锁"
            )
        except Exception as e:
            _log.warning("[risk_warn] L3 通知回调异常：%r", e)
    _log.error(
        "[risk_warn] L3 兜底：风控弹窗未自救，已暂停接待 title=%r",
        info.get("title", "")[:80],
    )


@dataclass(frozen=True, slots=True)
class ReviseContext:
    original_reply: str
    risk_warning_text: str
    buyer_recent_messages: list[str]
    shop_display_name: str = ""


def _contains_disabled(text: str) -> str | None:
    for phrase in DISABLED_PHRASES:
        if phrase in text:
            return phrase
    return None


def regenerate_reply_with_risk_prompt(ctx: ReviseContext) -> str | None:
    """调 LLM 重新生成回复；2 次仍含禁用词 → None。"""
    buyer_block = "\n".join(
        f"  · {m}" for m in (ctx.buyer_recent_messages or []) if m
    ) or "  · （暂无近期买家消息）"
    base_prompt = (
        f"你是淘宝店铺「{ctx.shop_display_name or '本店'}」的客服。\n"
        f"你刚才生成的回复\"{ctx.original_reply[:120]}\"被淘宝以"
        f"\"{ctx.risk_warning_text[:120]}\"拦截。\n"
        f"买家最近的消息：\n{buyer_block}\n\n"
        "请重新生成一条**直接、具体、针对买家最后一条问题**的回复，要求：\n"
        "1. 严禁使用以下话术：\n"
        + "\n".join(f"   - {p}" for p in DISABLED_PHRASES) + "\n"
        "2. 不要再问\"有什么可以帮您\"这类敷衍话术，必须正面回答买家问题。\n"
        "3. 30 字以内。简短、自然、像真人客服。\n"
        "4. 直接输出回复内容本身，不要任何解释、引号、Markdown。\n"
    )

    try:
        from apps.core.ai.llm_client import deep_analysis_completion
        from apps.core.configs.base_settings import load_base_settings
    except ImportError:
        _log.warning("[risk_warn] LLM 重新生成跳过：llm_client 不可用")
        return None

    try:
        _bs = load_base_settings()
    except Exception as e:
        _log.warning("[risk_warn] 读取设置失败，跳过重新生成：%r", e)
        return None

    for attempt in (1, 2):
        try:
            reply = (deep_analysis_completion(
                settings=_bs,
                system="你是淘宝店铺资深客服。",
                user=base_prompt,
            ) or "").strip()
        except Exception as e:
            _log.warning("[risk_warn] LLM 调用异常 attempt=%d: %r", attempt, e)
            return None
        if not reply:
            _log.info("[risk_warn] LLM 返回空 attempt=%d", attempt)
            continue
        hit = _contains_disabled(reply)
        if hit is None:
            _log.info("[risk_warn] LLM 重新生成 attempt=%d 通过：%r",
                      attempt, reply[:60])
            return reply
        _log.warning("[risk_warn] LLM attempt=%d 仍含禁用词 %r，重写", attempt, hit)
        base_prompt += (
            f"\n你上次的回复\"{reply}\"含禁用词「{hit}」，再写一个完全不含它的版本。"
        )

    _log.error("[risk_warn] LLM 重新生成 2 次仍含禁用词，放弃")
    return None


def handle_risk_warning_once(
    *,
    session_key: str,
    revise_ctx: ReviseContext | None = None,
    on_pause_callback: Callable[[str], None] | None = None,
    on_send_callback: Callable[[str], bool] | None = None,
) -> bool:
    """一次完整自救（被 worker loop 调用）。返回 True=自救完成；False=L3。"""
    if _is_session_locked(session_key):
        _log.info("[risk_warn] 会话 %s 已锁，跳过本次自救", session_key)
        return False

    # v1.6.26：L0 全屏截图+OCR 点「返回修改」优先（最可靠）→ L1 UIA → L2 视觉 → L3 兜底
    if _try_l0_screenshot_ocr_click():
        _log.info("[risk_warn] L0 截图OCR点击「返回修改」成功")
    elif _try_l1_uia_click():
        _log.info("[risk_warn] L1 UIA 点击成功")
    elif _try_l2_visual_click():
        _log.info("[risk_warn] L0/L1 失败 → L2 视觉点击成功")
    else:
        _trigger_l3_human_escalation(
            {
                "session_key": session_key,
                "title": "风控弹窗未自救",
                "original_reply": revise_ctx.original_reply if revise_ctx else "",
            },
            on_pause_callback=on_pause_callback,
        )
        with _state_lock:
            _session_consecutive_count[session_key] += 1
            if _session_consecutive_count[session_key] >= CONSECUTIVE_THRESHOLD:
                _lock_session(session_key, "连续 2 次未自救")
        return False

    time.sleep(0.7)

    if revise_ctx is not None and on_send_callback is not None:
        new_reply = regenerate_reply_with_risk_prompt(revise_ctx)
        if new_reply:
            ok = on_send_callback(new_reply)
            if ok:
                _log.info("[risk_warn] 新回复已入队：%r", new_reply[:60])
                with _state_lock:
                    _session_consecutive_count[session_key] = 0
                _append_jsonl({
                    "ts": int(time.time()),
                    "type": "self_recover_ok",
                    "session_key": session_key,
                    "original_reply": revise_ctx.original_reply[:200],
                    "regenerated_reply": new_reply[:200],
                })
                return True
            _log.warning("[risk_warn] 新回复入队失败")
        else:
            _log.warning("[risk_warn] LLM 重新生成失败，跳过入队（对话框已清空）")
    else:
        _log.info("[risk_warn] 无 revise_ctx，仅清空对话框（L1/L2 成功）")
        return True

    with _state_lock:
        _session_consecutive_count[session_key] = 0
    return True


class RiskWarningReviseLoop:
    """后台 loop：周期 4s 扫描风控弹窗 → handle_risk_warning_once。"""

    def __init__(
        self,
        *,
        session_key_getter: Callable[[], str],
        on_pause_callback: Callable[[str], None] | None = None,
        on_send_callback: Callable[[str], bool] | None = None,
        revise_ctx_getter: Callable[[], ReviseContext | None] | None = None,
        interval_s: float = 4.0,
    ) -> None:
        self._session_key_getter = session_key_getter
        self._on_pause_callback = on_pause_callback
        self._on_send_callback = on_send_callback
        self._revise_ctx_getter = revise_ctx_getter
        self._interval_s = float(interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="RiskWarningReviseLoop", daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=float(join_timeout_s))

    def _run(self) -> None:
        from apps.core.automation.popup_dismiss import find_risk_warning_popups
        # v1.6.17：本线程要用 UIA(comtypes)，先初始化 COM（防 0x80040155 并发崩溃）
        try:
            from apps.core.automation.uia_guard import init_com_for_thread
            init_com_for_thread()
        except Exception:
            pass
        # v1.6.26：千牛是 Electron/CEF，UIA 常枚举不到"服务态度提醒"弹窗 → 旧逻辑(只靠 UIA)
        # 根本不触发自救。新增全屏 OCR 兜底门控：UIA 没命中时，每 30s 全屏 OCR 一次看是否
        # 有风控提示（节流避免频繁全屏 OCR，也避免误触）。命中即走 handle（内部 L0 截图OCR点击）。
        _last_ocr_gate = 0.0
        _OCR_GATE_INTERVAL_S = 30.0
        while not self._stop.wait(self._interval_s):
            try:
                hit = bool(find_risk_warning_popups())
                if not hit:
                    _now = time.monotonic()
                    if (_now - _last_ocr_gate) >= _OCR_GATE_INTERVAL_S:
                        _last_ocr_gate = _now
                        if _ocr_risk_present():
                            hit = True
                            _log.info("[risk_warn] 全屏OCR兜底检测到风控提示（UIA未命中）")
                if not hit:
                    continue
                _log.info("[risk_warn] 检测到风控弹窗，启动自救")
                session_key = self._session_key_getter() or "unknown"
                revise_ctx = (
                    self._revise_ctx_getter() if self._revise_ctx_getter else None
                )
                handle_risk_warning_once(
                    session_key=session_key,
                    revise_ctx=revise_ctx,
                    on_pause_callback=self._on_pause_callback,
                    on_send_callback=self._on_send_callback,
                )
            except Exception as e:
                _log.exception("[risk_warn] loop 异常：%r", e)
