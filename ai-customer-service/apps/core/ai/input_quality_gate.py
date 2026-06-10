"""
买家输入质量门控：噪声/商品元数据/时间戳拼接不进 query rewrite 与 RAG。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from apps.core.runtime_paths import configs_dir

GateAction = Literal["pass", "quick_reply", "discard_log"]


@dataclass(frozen=True, slots=True)
class InputGateResult:
    action: GateAction
    reply: str = ""
    rule_name: str = ""


_RE_PRICE_ONLY = re.compile(r"^￥[\d\.]+.*$")
_RE_TIMESTAMP_GLUE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}\d{1,2}:\d{2}")
_RE_TIMESTAMP_LINE = re.compile(
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[\sT]?\d{1,2}:\d{2}",
)
_RE_INVENTORY = re.compile(r"库存\d+|销量\d+")
_RE_SHORT_ALPHA = re.compile(r"^[a-zA-Z]{1,2}$")
_RE_SORT_NOISE = re.compile(r"^排序$", re.I)
_RE_PRODUCT_CARD_PRICE = re.compile(r"[￥¥]\s*[\d,]+\.?\d*")
_QUESTION_HINTS = ("吗", "呢", "怎么", "为什么", "多少", "可以", "?", "？", "么", "嘛")
_RE_DATETIME_ONLY_LINE = re.compile(
    r"^\d{4}[-/年]\d{1,2}[-/月]?\d{1,2}(?:日)?[\sT]+\d{1,2}:\d{2}(?::\d{2})?\s*$"
)
# 千牛 UI 元素噪声（商品卡片操作按钮 / 订单状态标签 / 系统消息行）
_RE_QIANNIU_UI_NOISE = re.compile(
    r"^(规格|库存|发货|已下单|确认收货|退款|评价|申请退款|查看物流|投诉|举报)\s*\d*$"
)
# OCR 将千牛底部工具栏图标误识别为的常见单字/符号（田、口、回、日 等方块状图标）
_TOOLBAR_ICON_CHARS = frozenset("田口回日目曰囗囚")
_RE_SYSTEM_MSG_NOISE = re.compile(
    r"^(系统消息|服务评价|消息记录|查看订单|客服接入|会话结束|自动回复|消息已撤回)\s*$"
)


def is_product_card_noise(text: str) -> bool:
    """千牛 OCR 常把商品卡片识别成「商品名+价格」一行，无疑问词时不宜进 RAG。"""
    t = (text or "").strip()
    if not t or len(t) >= 60:
        return False
    if not _RE_PRODUCT_CARD_PRICE.search(t):
        return False
    if any(k in t for k in _QUESTION_HINTS):
        return False
    return True


def is_datetime_only_noise(text: str) -> bool:
    """整行仅为聊天时间串时，不应作为买家提问。"""
    t = (text or "").strip()
    if not t or len(t) >= 40:
        return False
    return bool(_RE_DATETIME_ONLY_LINE.match(t))


def is_metadata_noise(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _RE_PRICE_ONLY.match(t):
        return True
    if _RE_TIMESTAMP_GLUE.search(t):
        return True
    if _RE_TIMESTAMP_LINE.search(t):
        return True
    if _RE_INVENTORY.search(t):
        return True
    if "￥" in t and re.search(r"库存|销量", t):
        return True
    return False


def _load_gate_config() -> dict:
    path = configs_dir() / "query_rewrite.yaml"
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def gate_settings() -> tuple[int, str, bool]:
    raw = _load_gate_config()
    qr = raw.get("query_rewrite") if isinstance(raw.get("query_rewrite"), dict) else {}
    pf = qr.get("pre_filter") if isinstance(qr.get("pre_filter"), dict) else {}
    iq = qr.get("input_quality_gate") if isinstance(qr.get("input_quality_gate"), dict) else {}
    min_len = int(pf.get("min_length") or iq.get("min_length") or 3)
    tpl = str(
        iq.get("quick_reply_template")
        or pf.get("quick_reply_template")
        or "您好，请问有什么可以帮到您？"
    ).strip()
    enabled = bool(iq.get("enabled", True))
    return min_len, tpl, enabled


def check_buyer_input(buyer_text: str) -> InputGateResult:
    """
    在进入 rewrite / RAG 前调用。
    - quick_reply：返回引导话术，不 takeover
    - discard_log：静默丢弃（记录规则名），不 takeover
    - pass：正常流程
    """
    min_len, quick_tpl, enabled = gate_settings()
    if not enabled:
        return InputGateResult(action="pass")

    t = (buyer_text or "").strip()
    if not t:
        return InputGateResult(action="discard_log", rule_name="empty")

    # v1.5.8 修复触发淘宝风控：单字 OCR 几乎都是噪声（漏读、工具栏图标、商品名碎片），
    # 之前会发 "请问有什么可以帮到您？"——这正是淘宝"重复无意义话术"风控关键字。
    # 长度 <= 1 一律静默丢弃；长度 == 2 保留 quick_reply（真寒暄概率高）。
    if len(t) <= 1:
        return InputGateResult(
            action="discard_log", rule_name="single_char_ocr_noise"
        )

    if len(t) <= min_len - 1:
        return InputGateResult(
            action="quick_reply", reply=quick_tpl, rule_name="too_short"
        )

    if _RE_SHORT_ALPHA.match(t):
        return InputGateResult(
            action="quick_reply", reply=quick_tpl, rule_name="short_alpha"
        )

    # 千牛底部工具栏图标被 OCR 误读为单字（"田""口""回"等），直接丢弃
    if len(t) == 1 and t in _TOOLBAR_ICON_CHARS:
        return InputGateResult(action="discard_log", rule_name="toolbar_icon_noise")

    if _RE_SORT_NOISE.match(t):
        return InputGateResult(
            action="quick_reply", reply=quick_tpl, rule_name="sort_noise"
        )

    if is_metadata_noise(t):
        return InputGateResult(action="discard_log", rule_name="metadata_noise")

    if is_datetime_only_noise(t):
        return InputGateResult(action="discard_log", rule_name="datetime_only_noise")

    if is_product_card_noise(t):
        return InputGateResult(action="discard_log", rule_name="product_card_noise")

    if _RE_QIANNIU_UI_NOISE.match(t):
        return InputGateResult(action="discard_log", rule_name="qianniu_ui_noise")

    if _RE_SYSTEM_MSG_NOISE.match(t):
        return InputGateResult(action="discard_log", rule_name="system_msg_noise")

    if (
        _RE_PRODUCT_CARD_PRICE.search(t)
        and any(k in t for k in _QUESTION_HINTS)
        and len(t) < 120
    ):
        raw = _load_gate_config()
        qr = raw.get("query_rewrite") if isinstance(raw.get("query_rewrite"), dict) else {}
        iq = qr.get("input_quality_gate") if isinstance(qr.get("input_quality_gate"), dict) else {}
        product_tpl = str(
            iq.get("product_card_inquiry_template")
            or "您好，请问关于这款商品有什么想了解的？"
        ).strip()
        return InputGateResult(
            action="quick_reply",
            reply=product_tpl,
            rule_name="product_card_inquiry",
        )

    if re.match(r"^Foryou$", t, re.I):
        return InputGateResult(
            action="quick_reply", reply=quick_tpl, rule_name="noise_token"
        )

    return InputGateResult(action="pass")


def sanitize_context_for_rewrite(context_block: str) -> str:
    """仅保留可读的「客户」轮次，剔除元数据/噪声行。"""
    lines_out: list[str] = []
    for line in (context_block or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("客户：") or s.startswith("客户:"):
            body = s.split("：", 1)[-1].split(":", 1)[-1].strip()
            if is_metadata_noise(body):
                continue
            if len(body) <= 2 and _RE_SHORT_ALPHA.match(body):
                continue
        lines_out.append(s)
    return "\n".join(lines_out) if lines_out else "（暂无近期上文）"


def load_executor_busy_timeout_s() -> float:
    raw = _load_gate_config()
    ex = raw.get("executor") if isinstance(raw.get("executor"), dict) else {}
    return max(5.0, float(ex.get("busy_timeout_seconds") or 30))


def load_inquiry_templates() -> tuple[str, str]:
    raw = _load_gate_config()
    ia = raw.get("inquiry_auto_reply") if isinstance(raw.get("inquiry_auto_reply"), dict) else {}
    price = str(ia.get("price_quote_template") or "").strip()
    order = str(ia.get("order_placed_template") or "").strip()
    if not price:
        price = "您好，感谢您的咨询！该商品当前售价请以页面展示为准，如有疑问欢迎继续提问～"
    if not order:
        order = "您好，感谢您的支持！订单提交后我们会尽快安排发货，有任何问题随时告知～"
    return price, order


def load_jim_min_buyer_len() -> int:
    raw = _load_gate_config()
    jim = raw.get("jim") if isinstance(raw.get("jim"), dict) else {}
    return max(1, int(jim.get("min_buyer_len_for_price_handoff") or 4))


@dataclass(frozen=True, slots=True)
class BringToFrontStep:
    method: str
    wait_after_ms: float = 0.0
    coords: tuple[int, int] | None = None


# slots=True 时不能从类上读 BringToFrontSettings.xxx 当默认序列（会得到 member_descriptor）
_DEFAULT_RESTORE_SEQUENCE: tuple[BringToFrontStep, ...] = (
    BringToFrontStep("taskbar_click", 600.0),
    BringToFrontStep("win32_show_normal", 400.0),
)
_DEFAULT_FOREGROUND_SEQUENCE: tuple[BringToFrontStep, ...] = (
    BringToFrontStep("win32_setforeground", 80.0),
    BringToFrontStep("alt_nudge", 120.0),
)


@dataclass(frozen=True, slots=True)
class BringToFrontSettings:
    minimized_check: bool = True
    restore_if_minimized: bool = True
    sw_restore_enabled: bool = True
    wait_after_restore_ms: float = 0.6
    timeout_s: float = 2.5
    poll_s: float = 0.05
    verify_hwnd_match: bool = True
    verify_not_minimized: bool = True
    verify_window_visible: bool = True
    verify_fail_action: str = "retry_restore"
    verify_retry_limit: int = 2
    restore_sequence: tuple[BringToFrontStep, ...] = _DEFAULT_RESTORE_SEQUENCE
    foreground_sequence: tuple[BringToFrontStep, ...] = _DEFAULT_FOREGROUND_SEQUENCE


@dataclass(frozen=True, slots=True)
class SessionDetectionSettings:
    highlight_method: str = "rgb"  # rgb | hsv
    hsv_lower: tuple[int, int, int] = (35, 100, 100)
    hsv_upper: tuple[int, int, int] = (55, 255, 255)
    session_highlight_threshold: float = 20.0
    yel_max_first_frame_audio: float = 15.0
    yel_max_first_frame_visual: float = 55.0
    yel_max_log_skip_below: float = 8.0
    session_list_settle_wait_s: float = 0.8
    yellow_bar_confirm_frames: int = 2
    yellow_bar_confirm_interval_s: float = 0.12
    yellow_row_pick: str = "topmost"  # topmost | max
    enable_red_badge_switch: bool = True
    # v1.6.18：实测纯红角标每行仅 8~9px（旧默认 18 漏掉真角标）；
    # 角标可能在第 1~3 行会话（y≈77 起），旧 max_row=56 只扫到第 1 行顶部就截断。
    red_badge_min_pixels: float = 6.0
    red_badge_max_pixel_row: int = 180
    audio_yellow_top_row_ratio: float = 0.28
    # v1.6.19：黄条 0~4s 闪烁(黄白交替)、5s 后恒定黄。多帧轮询跨过闪烁期：
    # 最多 12 帧 × 0.5s ≈ 6s 窗口（须 >5s 才能等到恒定黄），任一帧捕到即用、不必等满。
    yellow_poll_rounds: int = 12
    yellow_poll_interval_s: float = 0.5
    post_switch_extra_delay_s: float = 0.7
    post_switch_click_sleep_s: float = 0.45
    # 定时 OCR 重扫当前聊天区（秒）
    chat_rescan_interval_s: float = 60.0
    # 多客户安抚：检测到多个未读时逐个发「稍等」
    batch_soothe_enabled: bool = True
    batch_soothe_max_sessions: int = 6


@dataclass(frozen=True, slots=True)
class DebugSnapshotSettings:
    save_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class TimingSettings:
    capture_delay_s: float | None = None


@dataclass(frozen=True, slots=True)
class TimeAlignmentSettings:
    enabled: bool = True
    max_skew_minutes: float = 5.0
    warn_skew_minutes: float = 3.0
    stale_discard_minutes: float = 15.0
    capture_skew_discard_minutes: float = 15.0
    on_missing_timestamp: str = "allow"  # allow | block


@dataclass(frozen=True, slots=True)
class DBListenerYaml:
    """configs/query_rewrite.yaml 中 db_listener 节（实验）。"""

    poll_interval_seconds: float = 1.0
    db_path: str = ""
    table: str = ""
    col_map: dict[str, str] = field(default_factory=dict)


def _parse_bring_to_front_steps(
    raw_list: object,
    *,
    defaults: tuple[BringToFrontStep, ...],
) -> tuple[BringToFrontStep, ...]:
    if not isinstance(raw_list, list) or not raw_list:
        return defaults
    out: list[BringToFrontStep] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").strip()
        if not method:
            continue
        wait_ms = float(item.get("wait_after_ms") or 0)
        coords = None
        c = item.get("coords")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            coords = (int(c[0]), int(c[1]))
        out.append(BringToFrontStep(method=method, wait_after_ms=wait_ms, coords=coords))
    return tuple(out) if out else defaults


def load_bring_to_front_settings() -> BringToFrontSettings:
    raw = _load_gate_config()
    bf = raw.get("bring_to_front") if isinstance(raw.get("bring_to_front"), dict) else {}
    vc = bf.get("verify_conditions") if isinstance(bf.get("verify_conditions"), dict) else {}
    default_restore = _DEFAULT_RESTORE_SEQUENCE
    default_fg = _DEFAULT_FOREGROUND_SEQUENCE
    wait_restore = bf.get("wait_after_restore_ms")
    if wait_restore is None:
        wait_restore_s = 0.6
    else:
        wait_restore_s = max(0.0, float(wait_restore) / 1000.0)
    return BringToFrontSettings(
        minimized_check=bool(bf.get("minimized_check", True)),
        restore_if_minimized=bool(bf.get("restore_if_minimized", True)),
        sw_restore_enabled=bool(bf.get("sw_restore_enabled", True)),
        wait_after_restore_ms=wait_restore_s,
        timeout_s=max(0.5, float(bf.get("window_ready_timeout_ms", 2500)) / 1000.0),
        poll_s=max(0.02, float(bf.get("window_ready_poll_ms", 50)) / 1000.0),
        verify_hwnd_match=bool(vc.get("hwnd_match", True)),
        verify_not_minimized=bool(vc.get("not_minimized", True)),
        verify_window_visible=bool(vc.get("window_visible", True)),
        verify_fail_action=str(bf.get("verify_fail_action") or "retry_restore").strip(),
        verify_retry_limit=max(0, int(bf.get("verify_retry_limit") or 2)),
        restore_sequence=_parse_bring_to_front_steps(
            bf.get("restore_sequence_when_minimized"), defaults=default_restore
        ),
        foreground_sequence=_parse_bring_to_front_steps(
            bf.get("foreground_sequence_when_visible"), defaults=default_fg
        ),
    )


def load_session_detection_settings() -> SessionDetectionSettings:
    raw = _load_gate_config()
    sd = raw.get("session_detection") if isinstance(raw.get("session_detection"), dict) else {}
    lower = sd.get("hsv_lower") or [35, 100, 100]
    upper = sd.get("hsv_upper") or [55, 255, 255]
    return SessionDetectionSettings(
        highlight_method=str(sd.get("highlight_method") or "rgb").lower(),
        hsv_lower=(int(lower[0]), int(lower[1]), int(lower[2])),
        hsv_upper=(int(upper[0]), int(upper[1]), int(upper[2])),
        session_highlight_threshold=float(sd.get("session_highlight_threshold") or 20),
        yel_max_first_frame_audio=float(sd.get("yel_max_first_frame_audio") or 15),
        yel_max_first_frame_visual=float(sd.get("yel_max_first_frame_visual") or 55),
        yel_max_log_skip_below=float(sd.get("yel_max_log_skip_below") or 8),
        session_list_settle_wait_s=float(sd.get("session_list_settle_wait_s") or 0.8),
        yellow_bar_confirm_frames=max(1, int(sd.get("yellow_bar_confirm_frames") or 2)),
        yellow_bar_confirm_interval_s=float(sd.get("yellow_bar_confirm_interval_s") or 0.12),
        yellow_row_pick=str(sd.get("yellow_row_pick") or "topmost").strip().lower(),
        enable_red_badge_switch=bool(sd.get("enable_red_badge_switch", True)),
        red_badge_min_pixels=float(sd.get("red_badge_min_pixels") or 6),
        red_badge_max_pixel_row=int(sd.get("red_badge_max_pixel_row") or 180),
        audio_yellow_top_row_ratio=float(sd.get("audio_yellow_top_row_ratio") or 0.28),
        yellow_poll_rounds=max(1, int(sd.get("yellow_poll_rounds") or 12)),
        yellow_poll_interval_s=float(sd.get("yellow_poll_interval_s") or 0.5),
        post_switch_extra_delay_s=float(sd.get("post_switch_extra_delay_s") or 0.7),
        post_switch_click_sleep_s=float(sd.get("post_switch_click_sleep_s") or 0.45),
        chat_rescan_interval_s=max(10.0, float(sd.get("chat_rescan_interval_s") or 60)),
        batch_soothe_enabled=bool(sd.get("batch_soothe_enabled", True)),
        batch_soothe_max_sessions=max(1, int(sd.get("batch_soothe_max_sessions") or 6)),
    )


def load_debug_snapshot_settings() -> DebugSnapshotSettings:
    raw = _load_gate_config()
    dbg = raw.get("debug") if isinstance(raw.get("debug"), dict) else {}
    return DebugSnapshotSettings(save_snapshot=bool(dbg.get("save_snapshot", False)))


def load_timing_settings() -> TimingSettings:
    raw = _load_gate_config()
    t = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}
    delay = t.get("capture_delay_s")
    if delay is None:
        delay = t.get("screenshot_delay")
    if delay is None:
        return TimingSettings(capture_delay_s=None)
    return TimingSettings(capture_delay_s=max(0.0, float(delay)))


def resolve_capture_delay_s(base_settings_delay: float) -> float:
    """合并 base_settings 与 query_rewrite timing，取较大值（更保守）。"""
    ts = load_timing_settings()
    if ts.capture_delay_s is not None:
        return max(float(base_settings_delay), ts.capture_delay_s)
    return float(base_settings_delay)


def load_audio_cooldown_fallback_s() -> float | None:
    raw = _load_gate_config()
    at = raw.get("audio_trigger") if isinstance(raw.get("audio_trigger"), dict) else {}
    val = at.get("cooldown_seconds")
    if val is None:
        return None
    return max(0.1, float(val))


def load_min_audio_peak() -> float:
    raw = _load_gate_config()
    at = raw.get("audio_trigger") if isinstance(raw.get("audio_trigger"), dict) else {}
    return max(0.005, float(at.get("min_peak_threshold") or 0.02))


def load_greeting_cooldown_minutes() -> float:
    raw = _load_gate_config()
    sess = raw.get("session") if isinstance(raw.get("session"), dict) else {}
    return max(0.0, float(sess.get("greeting_cooldown_minutes") or 5))


def load_time_alignment_settings() -> TimeAlignmentSettings:
    raw = _load_gate_config()
    ta = raw.get("time_alignment") if isinstance(raw.get("time_alignment"), dict) else {}
    on_missing = str(ta.get("on_missing_timestamp") or "allow").strip().lower()
    if on_missing not in ("allow", "block"):
        on_missing = "allow"
    return TimeAlignmentSettings(
        enabled=bool(ta.get("enabled", True)),
        max_skew_minutes=max(0.0, float(ta.get("max_skew_minutes") or 5)),
        warn_skew_minutes=max(0.0, float(ta.get("warn_skew_minutes") or 3)),
        stale_discard_minutes=max(0.0, float(ta.get("stale_discard_minutes") or 15)),
        capture_skew_discard_minutes=max(
            0.0, float(ta.get("capture_skew_discard_minutes") or 15)
        ),
        on_missing_timestamp=on_missing,
    )


def load_db_listener_yaml() -> DBListenerYaml:
    raw = _load_gate_config()
    dl = raw.get("db_listener") if isinstance(raw.get("db_listener"), dict) else {}
    cm = dl.get("col_map") if isinstance(dl.get("col_map"), dict) else {}
    col_map = {str(k): str(v) for k, v in cm.items() if str(v).strip()}
    return DBListenerYaml(
        poll_interval_seconds=max(0.2, float(dl.get("poll_interval_seconds") or 1.0)),
        db_path=str(dl.get("db_path") or "").strip(),
        table=str(dl.get("table") or "").strip(),
        col_map=col_map,
    )


def explain_db_listener_not_ready(cfg: DBListenerYaml | None = None) -> str | None:
    """
    检查本地 DB 消息源是否可启动。
    返回 None 表示可以启动；否则返回给主理人看的说明（可多行）。
    """
    c = cfg or load_db_listener_yaml()
    missing_labels: list[str] = []

    if not c.db_path:
        missing_labels.append("千牛聊天记录所在的数据库文件路径")
    if not c.table:
        missing_labels.append("存放聊天内容的数据表名称")
    cm = c.col_map or {}
    if not str(cm.get("id") or "").strip():
        missing_labels.append("每条消息的唯一编号字段")
    if not str(cm.get("content") or "").strip():
        missing_labels.append("买家说话内容的文字字段")
    if not str(cm.get("time") or "").strip():
        missing_labels.append("消息发送时间字段")

    if missing_labels:
        lines = [
            "【本地数据库消息源】还没配好，暂时不能从千牛直接读聊天记录。",
            "",
            "您勾选了「本地 DB 消息源」，但下面这些信息还没写全：",
        ]
        for label in missing_labels:
            lines.append(f"  · {label}")
        lines.extend(
            [
                "",
                "【最简单的配法】只需运行一个向导（全程中文提示）：",
                "  在本程序文件夹打开命令行，输入：",
                "  python scripts/setup_db_listener_wizard.py",
                "  按提示让买家发一条「你好」，回车，选「是」自动保存即可。",
                "",
                "在配好之前：请取消勾选「本地 DB 消息源」，会继续用原来的截图识别。",
            ]
        )
        return "\n".join(lines)

    db_file = Path(c.db_path)
    if not db_file.is_file():
        return "\n".join(
            [
                "【本地数据库消息源】找不到千牛的数据库文件。",
                "",
                f"配置里写的位置是：{c.db_path}",
                "",
                "常见原因：路径抄错、千牛还没在本机登录过、或千牛升级后文件换了地方。",
                "请重新运行 python scripts/setup_db_listener_wizard.py 重新检测，",
                "或取消勾选 DB、继续用截图识别。",
                "",
                "在此之前请取消勾选「本地 DB 消息源」，改用截图识别。",
            ]
        )

    return None
