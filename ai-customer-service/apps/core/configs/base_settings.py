from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from apps.core.runtime_paths import configs_dir


def default_base_settings_path(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root / "configs" / "base_settings.yaml"
    return configs_dir() / "base_settings.yaml"


DEFAULT_MODEL_FRONT = "openai/gpt-4o-mini"
DEFAULT_MODEL_DEEP = "openai/claude-sonnet-4-6-thinking"

# 文档/占位说明：将 OpenAI 官方 https://api.openai.com 换为中转时，通常填 https://ai.t8star.cn/v1
SUGGESTED_LLM_API_BASE = "https://ai.t8star.cn/v1"


@dataclass
class BaseSettings:
    # --- 全局 API 密钥（LiteLLM 按所选模型自动选用）---
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # 中转网关（留空则各厂商默认官方 endpoint；填写则 LiteLLM 对该次请求使用 api_base）
    llm_api_base: str = ""

    # openai/gemini-* 走 OpenAI 兼容时，部分网关要求 body 含 tools（如 googleSearch 预设），见 Apifox「Gemini 预设 tools」
    llm_gemini_attach_search_tool: bool = True

    # --- 双线模型（LiteLLM model id）---
    model_front_desk: str = DEFAULT_MODEL_FRONT
    model_deep_analysis: str = DEFAULT_MODEL_DEEP

    # --- 兼容旧版 configs（load 时迁移到 model_front_desk / 密钥）---
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    push_serverchan_sendkey: str = ""
    push_pushplus_token: str = ""
    push_wecom_webhook: str = ""
    # 强提醒等场景：额外 POST 到宿主机 HTTP（如 scripts/host_strong_alert_listener.py）
    push_host_alert_url: str = ""
    audio_target_exe: str = "AliWorkbench.exe"
    #: 有系统音量时，是否要求千牛进程在跑才真正触发接待（防误触）。False=任意系统声音都跑一轮视觉+接待。
    audio_gate_fire_only_when_qianniu_running: bool = True
    #: 全系统混音峰值高于该值则视为有声响（0..1）。0.02 偏高时可下调到 0.005~0.01。
    audio_peak_threshold: float = 0.02
    #: 峰值轮询间隔（秒）。叮咚很短时调小（0.04）以减少漏采；CPU 占用很低。
    audio_poll_interval_s: float = 0.08
    #: 两次触发的最短间隔（秒）。避免一次叮咚被反复触发。
    audio_cooldown_s: float = 4.0
    sweep_interval_minutes: int = 2
    #: 视觉哨兵：每 N 秒主动扫一次左侧会话列表 ROI（独立于声音，强烈推荐保持开启）。
    visual_sentry_enabled: bool = True
    visual_sentry_interval_s: int = 4
    #: 窗口切到前台后等多少秒再截图（让聊天区有时间渲染）。默认 0.8s，可按实际延迟调高。
    capture_delay_s: float = 1.5
    vm_host_program: str = ""
    vm_host_args: str = ""

    #: 话术贴近度（50–100%）。100% 严格复述知识库原文，50% 允许更多自由组句。
    kb_adherence_pct: int = 90

    # --- 畔色专属向量 / RAG 流水线（满血混合检索 + 重排）---
    panse_exclusive_embed_enabled: bool = False
    panse_embed_model_dir: str = "./models/panse_custom_embed/"
    panse_rerank_model_id: str = "BAAI/bge-reranker-base"
    panse_rrf_k: int = 60
    panse_rerank_min_score: float = 0.35
    panse_rag_pool_limit: int = 400
    #: v1.6.7 话术库向量召回的 embedding 模型（经中转，仅 OpenAI 系可用）。
    #: text-embedding-3-large(3072维,中文更准) / text-embedding-3-small(1536维,省)。
    #: 换模型必须重建全库向量（维度/语义都变）。
    embedding_model: str = "text-embedding-3-large"

    #: v1.6.14 商品卡片→咨询宝贝悬停读编码→查产品库答尺寸。默认关；
    #: 需先在设置中心标定咨询宝贝点/悬停点/浮层OCR区坐标后再开启。
    card_consult_lookup_enabled: bool = False

    # ───── v1.5.x 追加：窗口锁定（apps/core/channels/qianniu/window_pin.py）─────
    pin_window_enabled: bool = False
    pin_window_x: int = 100
    pin_window_y: int = 100
    pin_window_width: int = 1280
    pin_window_height: int = 800
    pin_window_drift_tolerance_px: int = 10
    pin_window_dpi_warn_only: bool = True

    # ───── v1.5.x 追加：文本抽取模式 ─────
    #: "ocr" = 现有 PaddleOCR 路径（默认，0 改动）
    #: "clipboard" = Ctrl+A + Ctrl+C 剪贴板路径（chat_text_clipboard.py）
    #: "hybrid" = 优先 clipboard，失败回退 ocr
    text_extract_mode: str = "ocr"

    # ───── v1.5.x 追加：回复时间拟人化（human_like/reply_timing.py）─────
    humanize_reply_timing_enabled: bool = False
    humanize_reply_delay_min_s: float = 8.0
    humanize_reply_delay_max_s: float = 20.0
    humanize_typing_extra_s_per_chars: float = 30.0
    humanize_typing_extra_chars_unit: int = 200
    humanize_gaussian_jitter_ratio: float = 0.15
    humanize_quiet_hours_enabled: bool = True
    humanize_quiet_hours_start: int = 1
    humanize_quiet_hours_end: int = 7

    # ───── v1.5.x 追加：真实打字模拟（human_like/typing_real.py）─────
    humanize_real_typing_enabled: bool = False
    humanize_typing_inter_char_min_s: float = 0.04
    humanize_typing_inter_char_max_s: float = 0.22
    humanize_typing_typo_rate: float = 0.025
    humanize_typing_backspace_pause_min_s: float = 0.25
    humanize_typing_backspace_pause_max_s: float = 0.7

    # ───── v1.5.x 追加：鼠标点击抖动（human_like/mouse_jitter.py）─────
    humanize_mouse_jitter_enabled: bool = True
    humanize_mouse_jitter_px: int = 3
    humanize_mouse_jitter_sigma_divisor: float = 2.5
    humanize_mouse_curved_motion: bool = False
    humanize_mouse_motion_steps: int = 24

    # ───── v1.5.x 追加：闲时无意义动作（human_like/idle_actions.py）─────
    humanize_idle_action_enabled: bool = False
    humanize_idle_expected_per_hour: float = 1.0
    humanize_idle_min_interval_s: float = 600.0
    humanize_idle_dwell_min_s: float = 3.0
    humanize_idle_dwell_max_s: float = 8.0


def load_base_settings(path: Path | None = None) -> BaseSettings:
    p = path or default_base_settings_path()
    if not p.is_file():
        return BaseSettings()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return BaseSettings()

    mf = str(raw.get("model_front_desk") or "").strip()
    md = str(raw.get("model_deep_analysis") or "").strip()

    # 迁移：仅有 anthropic_model / anthropic_api_key 的旧配置
    if not mf:
        legacy_m = str(raw.get("anthropic_model") or "").strip()
        if legacy_m:
            mf = legacy_m if "/" in legacy_m else f"anthropic/{legacy_m}"
        else:
            mf = DEFAULT_MODEL_FRONT
    if not md:
        md = DEFAULT_MODEL_DEEP

    if "llm_api_base" in raw:
        base_url = str(raw.get("llm_api_base") or "").strip()
    else:
        # 旧版 yaml 无此字段：保持「直连官方」，避免静默改路由
        base_url = ""

    _gem_tools = True
    if "llm_gemini_attach_search_tool" in raw:
        _gem_tools = bool(raw.get("llm_gemini_attach_search_tool"))

    return BaseSettings(
        deepseek_api_key=str(raw.get("deepseek_api_key") or ""),
        dashscope_api_key=str(raw.get("dashscope_api_key") or ""),
        openai_api_key=str(raw.get("openai_api_key") or ""),
        anthropic_api_key=str(raw.get("anthropic_api_key") or ""),
        gemini_api_key=str(raw.get("gemini_api_key") or ""),
        llm_api_base=base_url,
        llm_gemini_attach_search_tool=_gem_tools,
        model_front_desk=mf,
        model_deep_analysis=md,
        anthropic_model=str(raw.get("anthropic_model") or BaseSettings.anthropic_model),
        push_serverchan_sendkey=str(raw.get("push_serverchan_sendkey") or ""),
        push_pushplus_token=str(raw.get("push_pushplus_token") or ""),
        push_wecom_webhook=str(raw.get("push_wecom_webhook") or ""),
        push_host_alert_url=str(raw.get("push_host_alert_url") or ""),
        audio_target_exe=str(raw.get("audio_target_exe") or "AliWorkbench.exe"),
        audio_gate_fire_only_when_qianniu_running=bool(
            raw.get("audio_gate_fire_only_when_qianniu_running", True)
        ),
        audio_peak_threshold=float(raw.get("audio_peak_threshold") or BaseSettings.audio_peak_threshold),
        audio_poll_interval_s=float(raw.get("audio_poll_interval_s") or BaseSettings.audio_poll_interval_s),
        audio_cooldown_s=float(raw.get("audio_cooldown_s") or BaseSettings.audio_cooldown_s),
        sweep_interval_minutes=int(raw.get("sweep_interval_minutes") or BaseSettings.sweep_interval_minutes),
        visual_sentry_enabled=bool(raw.get("visual_sentry_enabled", True)),
        visual_sentry_interval_s=int(raw.get("visual_sentry_interval_s") or BaseSettings.visual_sentry_interval_s),
        capture_delay_s=float(raw.get("capture_delay_s") if raw.get("capture_delay_s") is not None else BaseSettings.capture_delay_s),
        vm_host_program=str(raw.get("vm_host_program") or ""),
        vm_host_args=str(raw.get("vm_host_args") or ""),
        kb_adherence_pct=int(raw.get("kb_adherence_pct") or 90),
        panse_exclusive_embed_enabled=bool(raw.get("panse_exclusive_embed_enabled", False)),
        panse_embed_model_dir=str(
            raw.get("panse_embed_model_dir") or "./models/panse_custom_embed/"
        ),
        panse_rerank_model_id=str(raw.get("panse_rerank_model_id") or "BAAI/bge-reranker-base"),
        panse_rrf_k=int(raw.get("panse_rrf_k") or 60),
        panse_rerank_min_score=float(raw.get("panse_rerank_min_score") or 0.35),
        panse_rag_pool_limit=int(raw.get("panse_rag_pool_limit") or 400),
        embedding_model=str(raw.get("embedding_model") or "text-embedding-3-large"),
        card_consult_lookup_enabled=bool(raw.get("card_consult_lookup_enabled", False)),

        # ── v1.5.x 追加 ──────────────────────────────────────────────
        pin_window_enabled=bool(raw.get("pin_window_enabled", False)),
        pin_window_x=int(raw.get("pin_window_x") if raw.get("pin_window_x") is not None else 100),
        pin_window_y=int(raw.get("pin_window_y") if raw.get("pin_window_y") is not None else 100),
        pin_window_width=int(raw.get("pin_window_width") if raw.get("pin_window_width") is not None else 1280),
        pin_window_height=int(raw.get("pin_window_height") if raw.get("pin_window_height") is not None else 800),
        pin_window_drift_tolerance_px=int(raw.get("pin_window_drift_tolerance_px") or 10),
        pin_window_dpi_warn_only=bool(raw.get("pin_window_dpi_warn_only", True)),

        text_extract_mode=str(raw.get("text_extract_mode") or "ocr").strip().lower(),

        humanize_reply_timing_enabled=bool(raw.get("humanize_reply_timing_enabled", False)),
        humanize_reply_delay_min_s=float(raw.get("humanize_reply_delay_min_s") or 8.0),
        humanize_reply_delay_max_s=float(raw.get("humanize_reply_delay_max_s") or 20.0),
        humanize_typing_extra_s_per_chars=float(raw.get("humanize_typing_extra_s_per_chars") or 30.0),
        humanize_typing_extra_chars_unit=int(raw.get("humanize_typing_extra_chars_unit") or 200),
        humanize_gaussian_jitter_ratio=float(raw.get("humanize_gaussian_jitter_ratio") or 0.15),
        humanize_quiet_hours_enabled=bool(raw.get("humanize_quiet_hours_enabled", True)),
        humanize_quiet_hours_start=int(raw.get("humanize_quiet_hours_start") if raw.get("humanize_quiet_hours_start") is not None else 1),
        humanize_quiet_hours_end=int(raw.get("humanize_quiet_hours_end") if raw.get("humanize_quiet_hours_end") is not None else 7),

        humanize_real_typing_enabled=bool(raw.get("humanize_real_typing_enabled", False)),
        humanize_typing_inter_char_min_s=float(raw.get("humanize_typing_inter_char_min_s") or 0.04),
        humanize_typing_inter_char_max_s=float(raw.get("humanize_typing_inter_char_max_s") or 0.22),
        humanize_typing_typo_rate=float(raw.get("humanize_typing_typo_rate") or 0.025),
        humanize_typing_backspace_pause_min_s=float(raw.get("humanize_typing_backspace_pause_min_s") or 0.25),
        humanize_typing_backspace_pause_max_s=float(raw.get("humanize_typing_backspace_pause_max_s") or 0.7),

        humanize_mouse_jitter_enabled=bool(raw.get("humanize_mouse_jitter_enabled", True)),
        humanize_mouse_jitter_px=int(raw.get("humanize_mouse_jitter_px") if raw.get("humanize_mouse_jitter_px") is not None else 3),
        humanize_mouse_jitter_sigma_divisor=float(raw.get("humanize_mouse_jitter_sigma_divisor") or 2.5),
        humanize_mouse_curved_motion=bool(raw.get("humanize_mouse_curved_motion", False)),
        humanize_mouse_motion_steps=int(raw.get("humanize_mouse_motion_steps") or 24),

        humanize_idle_action_enabled=bool(raw.get("humanize_idle_action_enabled", False)),
        humanize_idle_expected_per_hour=float(raw.get("humanize_idle_expected_per_hour") or 1.0),
        humanize_idle_min_interval_s=float(raw.get("humanize_idle_min_interval_s") or 600.0),
        humanize_idle_dwell_min_s=float(raw.get("humanize_idle_dwell_min_s") or 3.0),
        humanize_idle_dwell_max_s=float(raw.get("humanize_idle_dwell_max_s") or 8.0),
    )


def save_base_settings(settings: BaseSettings, path: Path | None = None) -> None:
    p = path or default_base_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "deepseek_api_key": settings.deepseek_api_key,
        "dashscope_api_key": settings.dashscope_api_key,
        "openai_api_key": settings.openai_api_key,
        "anthropic_api_key": settings.anthropic_api_key,
        "gemini_api_key": settings.gemini_api_key,
        "llm_api_base": settings.llm_api_base,
        "llm_gemini_attach_search_tool": settings.llm_gemini_attach_search_tool,
        "model_front_desk": settings.model_front_desk,
        "model_deep_analysis": settings.model_deep_analysis,
        "anthropic_model": settings.anthropic_model,
        "push_serverchan_sendkey": settings.push_serverchan_sendkey,
        "push_pushplus_token": settings.push_pushplus_token,
        "push_wecom_webhook": settings.push_wecom_webhook,
        "push_host_alert_url": settings.push_host_alert_url,
        "audio_target_exe": settings.audio_target_exe,
        "audio_gate_fire_only_when_qianniu_running": settings.audio_gate_fire_only_when_qianniu_running,
        "audio_peak_threshold": settings.audio_peak_threshold,
        "audio_poll_interval_s": settings.audio_poll_interval_s,
        "audio_cooldown_s": settings.audio_cooldown_s,
        "sweep_interval_minutes": settings.sweep_interval_minutes,
        "visual_sentry_enabled": settings.visual_sentry_enabled,
        "visual_sentry_interval_s": settings.visual_sentry_interval_s,
        "capture_delay_s": settings.capture_delay_s,
        "vm_host_program": settings.vm_host_program,
        "vm_host_args": settings.vm_host_args,
        "kb_adherence_pct": settings.kb_adherence_pct,
        "panse_exclusive_embed_enabled": settings.panse_exclusive_embed_enabled,
        "panse_embed_model_dir": settings.panse_embed_model_dir,
        "panse_rerank_model_id": settings.panse_rerank_model_id,
        "panse_rrf_k": settings.panse_rrf_k,
        "panse_rerank_min_score": settings.panse_rerank_min_score,
        "panse_rag_pool_limit": settings.panse_rag_pool_limit,
        "embedding_model": settings.embedding_model,
        "card_consult_lookup_enabled": settings.card_consult_lookup_enabled,

        # ── v1.5.x 追加 ──────────────────────────────────────────────
        "pin_window_enabled": settings.pin_window_enabled,
        "pin_window_x": settings.pin_window_x,
        "pin_window_y": settings.pin_window_y,
        "pin_window_width": settings.pin_window_width,
        "pin_window_height": settings.pin_window_height,
        "pin_window_drift_tolerance_px": settings.pin_window_drift_tolerance_px,
        "pin_window_dpi_warn_only": settings.pin_window_dpi_warn_only,

        "text_extract_mode": settings.text_extract_mode,

        "humanize_reply_timing_enabled": settings.humanize_reply_timing_enabled,
        "humanize_reply_delay_min_s": settings.humanize_reply_delay_min_s,
        "humanize_reply_delay_max_s": settings.humanize_reply_delay_max_s,
        "humanize_typing_extra_s_per_chars": settings.humanize_typing_extra_s_per_chars,
        "humanize_typing_extra_chars_unit": settings.humanize_typing_extra_chars_unit,
        "humanize_gaussian_jitter_ratio": settings.humanize_gaussian_jitter_ratio,
        "humanize_quiet_hours_enabled": settings.humanize_quiet_hours_enabled,
        "humanize_quiet_hours_start": settings.humanize_quiet_hours_start,
        "humanize_quiet_hours_end": settings.humanize_quiet_hours_end,

        "humanize_real_typing_enabled": settings.humanize_real_typing_enabled,
        "humanize_typing_inter_char_min_s": settings.humanize_typing_inter_char_min_s,
        "humanize_typing_inter_char_max_s": settings.humanize_typing_inter_char_max_s,
        "humanize_typing_typo_rate": settings.humanize_typing_typo_rate,
        "humanize_typing_backspace_pause_min_s": settings.humanize_typing_backspace_pause_min_s,
        "humanize_typing_backspace_pause_max_s": settings.humanize_typing_backspace_pause_max_s,

        "humanize_mouse_jitter_enabled": settings.humanize_mouse_jitter_enabled,
        "humanize_mouse_jitter_px": settings.humanize_mouse_jitter_px,
        "humanize_mouse_jitter_sigma_divisor": settings.humanize_mouse_jitter_sigma_divisor,
        "humanize_mouse_curved_motion": settings.humanize_mouse_curved_motion,
        "humanize_mouse_motion_steps": settings.humanize_mouse_motion_steps,

        "humanize_idle_action_enabled": settings.humanize_idle_action_enabled,
        "humanize_idle_expected_per_hour": settings.humanize_idle_expected_per_hour,
        "humanize_idle_min_interval_s": settings.humanize_idle_min_interval_s,
        "humanize_idle_dwell_min_s": settings.humanize_idle_dwell_min_s,
        "humanize_idle_dwell_max_s": settings.humanize_idle_dwell_max_s,
    }
    p.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")


# ───── v1.5.x 追加：工厂函数（从 BaseSettings → 子模块的 *Settings dataclass）─────
# 这一层让 event_pipeline / driver 不需要传 33 个独立参数，
# 只传 BaseSettings 即可，并按需取子配置。
# 子模块的 dataclass 在各自文件里独立定义，这里只是组合。

def to_pin_settings(bs: "BaseSettings"):
    """BaseSettings → window_pin.PinSettings。"""
    from apps.core.channels.qianniu.window_pin import PinSettings
    return PinSettings(
        enabled=bs.pin_window_enabled,
        x=bs.pin_window_x,
        y=bs.pin_window_y,
        width=bs.pin_window_width,
        height=bs.pin_window_height,
        drift_tolerance_px=bs.pin_window_drift_tolerance_px,
        dpi_warn_only=bs.pin_window_dpi_warn_only,
    )


def to_reply_timing_settings(bs: "BaseSettings"):
    """BaseSettings → reply_timing.ReplyTimingSettings。"""
    from apps.core.automation.human_like.reply_timing import ReplyTimingSettings
    return ReplyTimingSettings(
        enabled=bs.humanize_reply_timing_enabled,
        base_delay_min_s=bs.humanize_reply_delay_min_s,
        base_delay_max_s=bs.humanize_reply_delay_max_s,
        typing_extra_s_per_chars=bs.humanize_typing_extra_s_per_chars,
        typing_extra_chars_unit=bs.humanize_typing_extra_chars_unit,
        gaussian_jitter_ratio=bs.humanize_gaussian_jitter_ratio,
        quiet_hours_enabled=bs.humanize_quiet_hours_enabled,
        quiet_hours_start=bs.humanize_quiet_hours_start,
        quiet_hours_end=bs.humanize_quiet_hours_end,
    )


def to_typing_settings(bs: "BaseSettings"):
    """BaseSettings → typing_real.TypingSettings。"""
    from apps.core.automation.human_like.typing_real import TypingSettings
    return TypingSettings(
        enabled=bs.humanize_real_typing_enabled,
        inter_char_min_s=bs.humanize_typing_inter_char_min_s,
        inter_char_max_s=bs.humanize_typing_inter_char_max_s,
        typo_rate=bs.humanize_typing_typo_rate,
        backspace_pause_min_s=bs.humanize_typing_backspace_pause_min_s,
        backspace_pause_max_s=bs.humanize_typing_backspace_pause_max_s,
    )


def to_mouse_jitter_settings(bs: "BaseSettings"):
    """BaseSettings → mouse_jitter.MouseJitterSettings。"""
    from apps.core.automation.human_like.mouse_jitter import MouseJitterSettings
    return MouseJitterSettings(
        enabled=bs.humanize_mouse_jitter_enabled,
        jitter_px=bs.humanize_mouse_jitter_px,
        sigma_divisor=bs.humanize_mouse_jitter_sigma_divisor,
        use_curved_motion=bs.humanize_mouse_curved_motion,
        motion_steps=bs.humanize_mouse_motion_steps,
    )


def to_idle_action_settings(bs: "BaseSettings"):
    """BaseSettings → idle_actions.IdleActionSettings。"""
    from apps.core.automation.human_like.idle_actions import IdleActionSettings
    return IdleActionSettings(
        enabled=bs.humanize_idle_action_enabled,
        expected_per_hour=bs.humanize_idle_expected_per_hour,
        min_interval_s=bs.humanize_idle_min_interval_s,
        dwell_min_s=bs.humanize_idle_dwell_min_s,
        dwell_max_s=bs.humanize_idle_dwell_max_s,
    )
