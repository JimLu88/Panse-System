from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from apps.core.audio.process_audio_listener import (
    AudioPeakListenerConfig,
    ProcessAudioPeakListener,
    SweepFallbackTimer,
)
from apps.core.automation.popup_worker import PopupDismissLoop
from apps.core.automation.risk_warning_revise import RiskWarningReviseLoop
from apps.core.automation.actions.driver import ActiveWindowSendInputDriver, DryRunDriver
from apps.core.channels.qianniu.driver import QianniuDriver
from apps.core.channels.qianniu.visual_sentry import VisualSentryLoop
from apps.core.channels.qianniu.chat_area_sentry import ChatAreaSentry
from apps.core.channels.qianniu.file_sentinel import QianniuFileSentinel
from apps.core.configs.base_settings import BaseSettings, load_base_settings
from apps.core.runtime_paths import default_sqlite_db_path
from apps.core.configs.loader import load_shop_config
from apps.core.context.memory import MemoryStore
from apps.core.crm.db import connect, init_db
from apps.core.crm.policy_repo import ensure_policy_row, get_policy
from apps.core.orchestrator.action_queue import ActionQueue
from apps.core.orchestrator.event_pipeline import EventPipeline, PipelineOrchestrator
from apps.core.orchestrator.models import ActionKind, NewMessageEvent
from apps.core.orchestrator.sequential_executor import SequentialExecutor
from apps.core.workbench.action_handlers import build_executor_handler

LogFn = Callable[[str], None]


@dataclass
class RunningCore:
    q: ActionQueue
    pipeline: EventPipeline
    executor: SequentialExecutor
    driver: object
    shop: object | None


@dataclass
class AutomationSession:
    """工作台背后：Core（执行器）+ Brain（听觉/兜底 → PipelineOrchestrator）。"""

    source_id: str = field(default_factory=lambda: "qianniu/account1/workbench")
    session_id: str = field(default_factory=lambda: "sess-" + str(uuid.uuid4())[:10])
    memory: MemoryStore = field(default_factory=MemoryStore)
    shop_cfg_path: Path | None = None

    _core: RunningCore | None = None
    _orchestrator: PipelineOrchestrator | None = None
    _audio: ProcessAudioPeakListener | None = None
    _sweep: SweepFallbackTimer | None = None
    _sentry: VisualSentryLoop | None = None
    _chat_sentry: ChatAreaSentry | None = None
    _file_sentinel: QianniuFileSentinel | None = None
    _popup_loop: PopupDismissLoop | None = None
    _risk_warn_loop: RiskWarningReviseLoop | None = None
    _db_listener: object | None = None
    _message_input_mode: str = "ocr"

    def get_message_input_mode(self) -> str:
        return self._message_input_mode

    def set_message_input_mode(self, mode: str, log: LogFn | None = None) -> None:
        m = (mode or "ocr").strip().lower()
        self._message_input_mode = "db" if m == "db" else "ocr"
        if self._orchestrator is not None:
            self._orchestrator.set_message_input_mode(self._message_input_mode)
        if self.is_brain_running() and log is not None:
            self._apply_message_input_sources(log)

    def db_conn_factory(self):
        db_path = default_sqlite_db_path()
        conn = connect(db_path)
        init_db(conn)
        return conn

    def is_core_running(self) -> bool:
        return self._core is not None

    def is_brain_running(self) -> bool:
        return self._orchestrator is not None

    def start_core(self, *, shop_yaml: Path | None, real_foreground: bool, log: LogFn) -> None:
        if self._core is not None:
            log("Core 已在运行")
            return
        q = ActionQueue()
        pipeline = EventPipeline(q)
        shop = None
        if shop_yaml is not None:
            shop = load_shop_config(shop_yaml)
            if shop.qianniu is None:
                raise RuntimeError("店铺 YAML 缺少 qianniu 配置")
            driver: object = QianniuDriver(shop.qianniu)
        else:
            driver = ActiveWindowSendInputDriver() if real_foreground else DryRunDriver()

        def log_async(msg: str) -> None:
            log(msg)

        handle = build_executor_handler(
            driver=driver,
            memory=self.memory,
            log_async=log_async,
            db_conn_factory=self.db_conn_factory,
        )
        executor = SequentialExecutor(
            action_queue=q,
            handlers={
                ActionKind.NOOP: handle,
                ActionKind.SEND_TEXT: handle,
                ActionKind.SOOTHE_WAIT: handle,
                ActionKind.SEND_IMAGE: handle,
                ActionKind.REACQUIRE_CONTEXT: handle,
            },
            on_error=log_async,
        )
        executor.start()
        self.shop_cfg_path = shop_yaml
        self._core = RunningCore(q=q, pipeline=pipeline, executor=executor, driver=driver, shop=shop)
        log("Core 已启动：SequentialExecutor 在线")

    def stop_core(self, log: LogFn) -> None:
        self.stop_brain(log)
        if self._core is None:
            return
        self._core.executor.stop()
        self._core = None
        log("Core 已停止")

    def start_brain(self, log: LogFn, settings: BaseSettings | None = None, qt_shell: object | None = None) -> None:
        if self._core is None:
            raise RuntimeError("请先启动 Core")
        if self.shop_cfg_path is None:
            raise RuntimeError("全自动需要店铺 YAML（含 ocr_chat_rect），请先选择店铺并启动 Core")
        self.stop_brain(log)
        st = settings or load_base_settings()

        def brain_log(msg: str) -> None:
            log(f"[Brain] {msg}")

        self._qt_shell = qt_shell
        self._sensitive_review_payload: dict | None = None
        self._sensitive_review_result: list[bool] = [False]

        def sensitive_review_fn(payload: dict) -> bool:
            if self._qt_shell is None:
                return False
            from PyQt6.QtCore import QMetaObject, Qt

            self._sensitive_review_payload = payload
            self._sensitive_review_result[0] = False
            ok_invoke = QMetaObject.invokeMethod(
                self._qt_shell,
                "_slot_run_sensitive_review",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
            if not ok_invoke:
                brain_log("敏感预览：无法调度到主线程，已拦截发送")
                return False
            return bool(self._sensitive_review_result[0])

        review_fn = sensitive_review_fn if qt_shell is not None else None

        orch = PipelineOrchestrator(
            shop_cfg_path=self.shop_cfg_path,
            source_id=self.source_id,
            session_id=self.session_id,
            pipeline=self._core.pipeline,
            settings=st,
            log=brain_log,
            sensitive_review_fn=review_fn,
        )
        self._orchestrator = orch
        orch.set_message_input_mode(self._message_input_mode)

        def fire(ev: NewMessageEvent) -> None:
            brain_log(f"触发器收到事件 trigger={ev.trigger}")
            orch.handle_new_message_event(ev)

        self._fire_new_message = fire
        self._apply_message_input_sources(brain_log)

        self._popup_loop = None
        try:
            shop_o = load_shop_config(self.shop_cfg_path)
            bid = shop_o.brand_id
            sid_o = shop_o.shop_id or (shop_o.brand_id + ":" + shop_o.shop_code)
            conn_p = connect(default_sqlite_db_path())
            init_db(conn_p)
            ensure_policy_row(conn_p, brand_id=bid, shop_id=sid_o)
            pol = get_policy(conn_p, brand_id=bid, shop_id=sid_o)
            conn_p.close()
            # v1.6.0：无条件启用弹窗自动清理（旧 DB 行 popup_auto_dismiss=0
            # 不再阻挡）；周期 52s → 5s，更快响应风控弹窗 / 优惠券推荐；
            # 风控弹窗本 loop 不处理，由 RiskWarningReviseLoop 单独跑
            self._popup_loop = PopupDismissLoop(interval_s=5.0)
            self._popup_loop.start()
            log("[popup] 弹窗清理已启用（5s 周期，分类器→分发）")
            _ = pol

            # v1.6.0：风控弹窗自救 loop（与 popup_loop 平行，周期 4s 更紧）
            # v1.6.14：接线 revise_ctx_getter——点中"返回修改"后从 messages 表读
            # 最近我方回复(原文) + 最近买家消息，交 LLM 重新生成不含禁用词的新话术。
            sid = self.source_id
            ssid = self.session_id
            pipeline_ref = self._core.pipeline
            db_path_ref = default_sqlite_db_path()
            shop_name_ref = ""
            try:
                shop_name_ref = getattr(load_shop_config(self.shop_cfg_path), "shop_display_name", "") or ""
            except Exception:
                shop_name_ref = ""
            def _send_via_pipeline(text: str) -> bool:
                try:
                    aid = pipeline_ref.enqueue_send_text(
                        sid, ssid, text=text, bypass_dedup=True,
                    )
                    return bool(aid)
                except Exception:
                    return False

            def _build_revise_ctx():
                """从 messages 表读最近原回复(out) + 最近买家消息(in)，构造 ReviseContext。
                读不到则返回 None（风控自救会退回仅清空对话框）。"""
                try:
                    from apps.core.automation.risk_warning_revise import ReviseContext
                    conn = connect(db_path_ref)
                    try:
                        out_row = conn.execute(
                            "SELECT text FROM messages WHERE session_id=? AND direction='out' "
                            "ORDER BY created_at DESC LIMIT 1",
                            (ssid,),
                        ).fetchone()
                        in_rows = conn.execute(
                            "SELECT text FROM messages WHERE session_id=? AND direction='in' "
                            "ORDER BY created_at DESC LIMIT 3",
                            (ssid,),
                        ).fetchall()
                    finally:
                        conn.close()
                    original_reply = (out_row[0] if out_row else "").strip()
                    buyer_recent = [r[0] for r in reversed(in_rows or []) if r and r[0]]
                    if not original_reply and not buyer_recent:
                        return None
                    return ReviseContext(
                        original_reply=original_reply,
                        risk_warning_text="淘宝风控：服务态度提醒/疑似重复发送，请修改后再发",
                        buyer_recent_messages=buyer_recent,
                        shop_display_name=shop_name_ref,
                    )
                except Exception:
                    return None

            self._risk_warn_loop = RiskWarningReviseLoop(
                session_key_getter=lambda: f"{sid}::{ssid}",
                on_pause_callback=lambda msg: log(f"[risk_warn] {msg}"),
                on_send_callback=_send_via_pipeline,
                revise_ctx_getter=_build_revise_ctx,
                interval_s=4.0,
            )
            self._risk_warn_loop.start()
            log("[risk_warn] 风控弹窗自救已启用（4s 周期，L1/L2/L3 三层兜底）")
        except Exception as e:
            log(f"[popup] 弹窗清理未启用：{e!r}")

        mode = self._message_input_mode
        if mode == "db":
            log(
                "当前消息来源：千牛本地聊天记录库（实验）。"
                "已关闭「听声音 / 扫屏幕」那套截图识别触发。"
            )
        else:
            log(
                "当前消息来源：截图识别（听提示音 + 看会话列表）。"
                f"千牛未打开时不误触发={'是' if st.audio_gate_fire_only_when_qianniu_running else '否'}"
            )

    def pause_brain(self) -> None:
        if self._orchestrator is None:
            raise RuntimeError("Brain 未启动，无法进入观测模式")
        self._orchestrator.pause_brain_cycle()

    def is_brain_paused(self) -> bool:
        if self._orchestrator is None:
            return False
        return self._orchestrator.is_brain_paused()

    def resume_brain(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.resume_brain_cycle()

    def _stop_ocr_triggers(self) -> None:
        if self._audio is not None:
            self._audio.stop()
            self._audio = None
        if self._sweep is not None:
            self._sweep.stop()
            self._sweep = None
        if self._sentry is not None:
            self._sentry.stop()
            self._sentry = None
        if self._chat_sentry is not None:
            self._chat_sentry.stop()
            self._chat_sentry = None
        if self._file_sentinel is not None:
            self._file_sentinel.stop()
            self._file_sentinel = None

    def _stop_db_listener(self) -> None:
        if self._db_listener is not None:
            try:
                self._db_listener.stop()
            except Exception:
                pass
            self._db_listener = None

    def _apply_message_input_sources(self, log: LogFn) -> None:
        """按当前模式启动/停止 OCR 触发器与 DB 监听（不改变 Orchestrator）。"""
        st = load_base_settings()
        fire = getattr(self, "_fire_new_message", None)
        if fire is None:
            return

        if self._message_input_mode == "db":
            self._stop_ocr_triggers()
            from apps.core.ai.input_quality_gate import load_db_listener_yaml
            from apps.core.channels.qianniu.db_listener import QianniuDBListenerThread

            cfg = load_db_listener_yaml()
            from apps.core.ai.input_quality_gate import explain_db_listener_not_ready

            hint = explain_db_listener_not_ready(cfg)
            if hint:
                for line in hint.splitlines():
                    log(line)
                self._stop_db_listener()
                return

            def on_row(row: dict) -> None:
                fire(
                    NewMessageEvent(
                        source_id=self.source_id,
                        session_id=self.session_id,
                        trigger="db_message",
                        payload={"buyer_text": row.get("buyer_text", "")},
                    )
                )

            self._stop_db_listener()
            self._db_listener = QianniuDBListenerThread(cfg, on_row)
            self._db_listener.start()
            log(
                "【本地数据库消息源】已接通：将直接从千牛聊天记录库读取买家新消息，"
                "不再依赖截图识别。"
            )
            log(f"正在监听文件：{cfg.db_path}")
            return

        self._stop_db_listener()
        try:
            from apps.core.ai.input_quality_gate import (
                load_audio_cooldown_fallback_s,
                load_min_audio_peak,
            )

            min_peak = load_min_audio_peak()
            yaml_cd = load_audio_cooldown_fallback_s()
        except Exception:
            min_peak = 0.02
            yaml_cd = None
        base_cd = float(getattr(st, "audio_cooldown_s", 4.0) or 4.0)
        cooldown_s = max(base_cd, yaml_cd) if yaml_cd is not None else base_cd
        cfg = AudioPeakListenerConfig(
            target_exe_name=st.audio_target_exe or "AliWorkbench.exe",
            peak_threshold=float(
                getattr(st, "audio_peak_threshold", 0.02) or 0.02
            ),
            min_peak_threshold=min_peak,
            poll_interval_s=float(getattr(st, "audio_poll_interval_s", 0.08) or 0.08),
            cooldown_s=cooldown_s,
            gate_fire_only_when_target_exe_running=st.audio_gate_fire_only_when_qianniu_running,
        )
        if self._audio is None:
            self._audio = ProcessAudioPeakListener(
                cfg,
                on_trigger=lambda: fire(
                    NewMessageEvent(
                        source_id=self.source_id,
                        session_id=self.session_id,
                        trigger="audio_peak",
                    )
                ),
                on_diag=log,
            )
            self._audio.start()
        sweep_s = max(60.0, float(st.sweep_interval_minutes) * 60.0)
        if self._sweep is None:
            self._sweep = SweepFallbackTimer(
                sweep_s,
                on_sweep=lambda: fire(
                    NewMessageEvent(
                        source_id=self.source_id,
                        session_id=self.session_id,
                        trigger="sweep_fallback",
                    )
                ),
            )
            self._sweep.start()
        sentry_enabled = bool(getattr(st, "visual_sentry_enabled", True))
        sentry_interval = max(2, int(getattr(st, "visual_sentry_interval_s", 4) or 4))
        if (
            sentry_enabled
            and self.shop_cfg_path is not None
            and self._sentry is None
        ):
            self._sentry = VisualSentryLoop(
                self.shop_cfg_path,
                interval_s=float(sentry_interval),
                on_trigger=lambda: fire(
                    NewMessageEvent(
                        source_id=self.source_id,
                        session_id=self.session_id,
                        trigger="visual_scan",
                    )
                ),
                log=log,
            )
            self._sentry.start()
        # 聊天区哨兵：监控 ocr_chat_rect 底部左侧，检测当前选中会话的新买家消息
        chat_sentry_enabled = bool(getattr(st, "chat_area_sentry_enabled", True))
        chat_sentry_interval = max(4, int(getattr(st, "chat_area_sentry_interval_s", 8) or 8))
        if (
            chat_sentry_enabled
            and self.shop_cfg_path is not None
            and self._chat_sentry is None
        ):
            self._chat_sentry = ChatAreaSentry(
                self.shop_cfg_path,
                interval_s=float(chat_sentry_interval),
                on_trigger=lambda: fire(
                    NewMessageEvent(
                        source_id=self.source_id,
                        session_id=self.session_id,
                        trigger="chat_area_diff",
                    )
                ),
                log=log,
            )
            self._chat_sentry.start()
        # 文件哨兵：监听 UnReplyedConversation.json，无需音量/屏幕，最可靠的触发源
        if self._file_sentinel is None:
            try:
                from apps.core.configs.loader import load_base_settings as _lbs
                _st2 = _lbs()
                _data_root = getattr(_st2, "qianniu_data_root", None) or None
            except Exception:
                _data_root = None
            self._file_sentinel = QianniuFileSentinel(
                data_root=_data_root,
                on_trigger=lambda: fire(
                    NewMessageEvent(
                        source_id=self.source_id,
                        session_id=self.session_id,
                        trigger="file_sentinel",
                    )
                ),
                log=log,
            )
            self._file_sentinel.start()
        log("消息输入已切回 OCR（听觉+视觉哨兵+聊天区哨兵+文件哨兵）")

    def stop_brain(self, log: LogFn | None) -> None:
        had_any = (
            self._orchestrator is not None
            or self._audio is not None
            or self._sweep is not None
            or self._sentry is not None
            or self._chat_sentry is not None
            or self._file_sentinel is not None
            or self._db_listener is not None
            or self._popup_loop is not None
        )
        if self._orchestrator is not None:
            try:
                # 直接解除锁定状态，不打 resume 日志（避免停止时产生误导性日志）
                self._orchestrator._brain_paused = False
            except Exception:
                pass
        if self._orchestrator is not None:
            try:
                self._orchestrator.shutdown()
            except Exception:
                pass
        if self._popup_loop is not None:
            self._popup_loop.stop()
            self._popup_loop = None
        if self._risk_warn_loop is not None:
            self._risk_warn_loop.stop()
            self._risk_warn_loop = None
        if self._audio is not None:
            self._audio.stop()
            self._audio = None
        if self._sweep is not None:
            self._sweep.stop()
            self._sweep = None
        if self._sentry is not None:
            self._sentry.stop()
            self._sentry = None
        self._stop_db_listener()
        self._orchestrator = None
        self._fire_new_message = None
        self._qt_shell = None
        # 避免 start_brain 里「先 stop 再 start」时，在从未启动过听觉的情况下误刷「已停止」
        if log and had_any:
            log("听觉流水线已停止")

    def status_line(self) -> str:
        """兼容旧调用；界面请用 status_for_owner。"""
        if self._core is None:
            return "未启动"
        if self.is_brain_running():
            return "运行中"
        return "已连接"

    def status_for_owner(self) -> str:
        """主界面状态条（不出现底层术语）。"""
        if self._core is None:
            return "当前状态：全自动接待未开启"
        if not self.is_brain_running():
            return "当前状态：已连接千牛，尚未监听新消息（请确认已点击启动）"
        busy = self._core.executor.is_busy()
        qn = self._core.q.qsize()
        if busy or qn > 0:
            return f"当前状态：全自动接待运行中（有一条回复正在处理，队列 {qn}）"
        return "当前状态：全自动接待运行中"

    def executor_metrics(self) -> tuple[int, bool] | None:
        """供 AI 陪伴旁路采样队列深度与忙碌状态；未启动 Core 时返回 None。"""
        if self._core is None:
            return None
        return (self._core.q.qsize(), self._core.executor.is_busy())

    def enqueue_resume_ai(self, log: LogFn) -> None:
        if self._core is None or self.shop_cfg_path is None:
            raise RuntimeError("请先启动 Core 并选择店铺")
        self._core.pipeline.enqueue_reacquire_context(
            self.source_id,
            self.session_id,
            shop_cfg_path=str(self.shop_cfg_path.resolve()),
        )
        log("已排队：恢复 AI 托管（REACQUIRE_CONTEXT）")

    def enqueue_send_image(
        self,
        *,
        image_path: Path,
        chat_log_meta: dict | None = None,
    ) -> None:
        if self._core is None:
            raise RuntimeError("请先启动全自动客服系统（Core 未运行）")
        self._core.pipeline.enqueue_send_image(
            self.source_id,
            self.session_id,
            image_path=str(Path(image_path).resolve()),
            chat_log_meta=chat_log_meta,
        )
