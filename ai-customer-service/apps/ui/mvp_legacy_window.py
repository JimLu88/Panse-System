from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from apps.core.orchestrator.action_queue import ActionQueue
from apps.core.orchestrator.event_pipeline import EventPipeline
from apps.core.orchestrator.models import ActionItem, ActionKind
from apps.core.orchestrator.sequential_executor import SequentialExecutor
from apps.core.automation.actions.driver import PhysicalDriver
from apps.core.automation.actions.send_image import execute_send_image
from apps.core.automation.actions.send_text import execute_send_text
from apps.core.env_patches.dpi_assert import assert_dpi_100
from apps.core.configs.loader import load_shop_config
from apps.core.channels.qianniu.driver import QianniuDriver
from apps.core.capture.screen import ScreenCapture
from apps.core.ocr.dual_engine import get_dual_ocr_engine
from apps.core.automation.actions.driver import ActiveWindowSendInputDriver, DryRunDriver
from apps.core.signals.right_panel_hash import image_sig
from apps.core.crm.db import connect, init_db
from apps.core.crm.events import (
    SessionEvent,
    bump_priority,
    ensure_session_row,
    insert_session_event,
    set_manual_hold,
)
from apps.core.context.memory import MemoryStore
from apps.core.orchestrator.reacquire import run_reacquire_physical
from apps.core.risk_guard.guard import check_outbound_text
from apps.ui.rules_dialog import RulesEditorDialog


@dataclass
class CoreRuntime:
    q: ActionQueue
    pipeline: EventPipeline
    executor: SequentialExecutor
    driver: object
    shop: object | None = None


class LegacyMvpWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AI 客服工作台（MVP）")
        self.resize(900, 600)

        self._runtime: CoreRuntime | None = None
        self._source_id = "qianniu/account1/demo"
        self._session_id = "demo-session-" + str(uuid.uuid4())[:8]

        root = QWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout()
        root.setLayout(layout)

        top = QHBoxLayout()
        layout.addLayout(top)

        self.status_label = QLabel("状态：未启动")
        top.addWidget(self.status_label)
        top.addStretch(1)

        self.btn_start = QPushButton("启动 Core")
        self.btn_start.clicked.connect(self._on_start)
        top.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止 Core")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        top.addWidget(self.btn_stop)

        actions = QHBoxLayout()
        layout.addLayout(actions)

        self.btn_pick_shop = QPushButton("选择店铺配置（YAML）")
        self.btn_pick_shop.clicked.connect(self._on_pick_shop)
        actions.addWidget(self.btn_pick_shop)

        self.btn_rules = QPushButton("规则设置（表格）…")
        self.btn_rules.setToolTip("用表格编辑分支规则（含使用说明），保存到 configs/rules/reply_rules.yaml")
        self.btn_rules.clicked.connect(self._on_open_rules)
        actions.addWidget(self.btn_rules)

        self.shop_label = QLabel("店铺：未选择（默认 dry-run）")
        actions.addWidget(self.shop_label)

        self.cb_real_send = QCheckBox("真实发送（前台窗口）")
        self.cb_real_send.setChecked(False)
        self.cb_real_send.setEnabled(False)
        actions.addWidget(self.cb_real_send)

        self.btn_send = QPushButton("发送测试话术（SendText）")
        self.btn_send.clicked.connect(self._on_send)
        self.btn_send.setEnabled(False)
        actions.addWidget(self.btn_send)

        self.btn_soothe = QPushButton("排队安抚（SootheWait，高优先级）")
        self.btn_soothe.clicked.connect(self._on_soothe)
        self.btn_soothe.setEnabled(False)
        actions.addWidget(self.btn_soothe)

        self.btn_noop = QPushButton("NOOP")
        self.btn_noop.clicked.connect(self._on_noop)
        self.btn_noop.setEnabled(False)
        actions.addWidget(self.btn_noop)

        self.btn_ocr = QPushButton("测试 OCR（聊天ROI）")
        self.btn_ocr.clicked.connect(self._on_test_ocr)
        self.btn_ocr.setEnabled(False)
        actions.addWidget(self.btn_ocr)

        self.btn_right = QPushButton("测试右侧变化 -> 写事件")
        self.btn_right.clicked.connect(self._on_test_right_change)
        self.btn_right.setEnabled(False)
        actions.addWidget(self.btn_right)

        self.btn_order = QPushButton("测试下单弹窗命中 -> 写事件")
        self.btn_order.clicked.connect(self._on_test_order_popup)
        self.btn_order.setEnabled(False)
        actions.addWidget(self.btn_order)

        self.btn_events = QPushButton("查看最近事件")
        self.btn_events.clicked.connect(self._on_view_recent_events)
        self.btn_events.setEnabled(False)
        actions.addWidget(self.btn_events)

        self.btn_resume = QPushButton("恢复 AI 托管（回读上下文）")
        self.btn_resume.clicked.connect(self._on_resume_ai)
        self.btn_resume.setEnabled(False)
        actions.addWidget(self.btn_resume)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._append(f"会话：source_id={self._source_id} session_id={self._session_id}")
        self._shop_cfg_path: Path | None = None
        self._qianniu_driver_enabled = False
        self._last_right_sig: str | None = None
        self._memory = MemoryStore()

    def _append(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _on_start(self) -> None:
        if self._runtime is not None:
            return

        # Hard safety gate for coordinate automation.
        assert_dpi_100()

        q = ActionQueue()
        pipeline = EventPipeline(q)
        driver: object
        shop = None
        if self._shop_cfg_path is not None:
            shop = load_shop_config(self._shop_cfg_path)
            if shop.qianniu is None:
                raise RuntimeError("该店铺配置未包含 qianniu 配置")
            driver = QianniuDriver(shop.qianniu)
            self._qianniu_driver_enabled = True
            self.shop_label.setText(f"店铺：{shop.shop_display_name}（QianniuDriver）")
        else:
            driver = ActiveWindowSendInputDriver() if self.cb_real_send.isChecked() else DryRunDriver()
            self._qianniu_driver_enabled = False

        def _log_async(msg: str) -> None:
            QTimer.singleShot(0, lambda m=msg: self._append(m))

        def handle(item: ActionItem) -> None:
            if item.kind == ActionKind.SEND_TEXT:
                text = str(item.payload.get("text") or "")
                chk = check_outbound_text(text)
                if not chk.allowed:
                    _log_async(f"BLOCK SEND_TEXT action_id={item.action_id} reason={chk.reason}")
                    return
                _log_async(f"EXEC SEND_TEXT action_id={item.action_id} text_len={len(text)}")
                plan = execute_send_text(driver, text)  # type: ignore[arg-type]
                anchor = plan.segments[-1] if plan.segments else text[:200]
                self._memory.set_last_ai_snippet(item.session_id, anchor)
                _log_async(f"SEND_TEXT segments={len(plan.segments)}")
                return

            if item.kind == ActionKind.SOOTHE_WAIT:
                text = str(item.payload.get("text") or "")
                chk = check_outbound_text(text)
                if not chk.allowed:
                    _log_async(f"BLOCK SOOTHE_WAIT action_id={item.action_id} reason={chk.reason}")
                    return
                _log_async(f"EXEC SOOTHE_WAIT action_id={item.action_id} text_len={len(text)}")
                plan = execute_send_text(driver, text)  # type: ignore[arg-type]
                anchor = plan.segments[-1] if plan.segments else text[:200]
                self._memory.set_last_ai_snippet(item.session_id, anchor)
                _log_async(f"SOOTHE_WAIT segments={len(plan.segments)}")
                return

            if item.kind == ActionKind.SEND_IMAGE:
                path_str = str(item.payload.get("image_path") or "").strip()
                if not path_str or not Path(path_str).is_file():
                    _log_async(f"SKIP SEND_IMAGE action_id={item.action_id}")
                    return
                if not isinstance(driver, PhysicalDriver):
                    _log_async("SKIP SEND_IMAGE（非 PhysicalDriver）")
                    return
                _log_async(f"EXEC SEND_IMAGE action_id={item.action_id}")
                try:
                    execute_send_image(driver, Path(path_str))
                except Exception as e:
                    _log_async(f"SEND_IMAGE 失败: {e!r}")
                return

            if item.kind == ActionKind.REACQUIRE_CONTEXT:
                if not isinstance(driver, QianniuDriver):
                    _log_async("EXEC REACQUIRE_CONTEXT skipped（需要千牛店铺配置启动 QianniuDriver）")
                    return
                path_str = str(item.payload.get("shop_cfg_path") or "").strip()
                if not path_str:
                    _log_async("EXEC REACQUIRE_CONTEXT shop_cfg_path 为空")
                    return
                try:
                    shop_loaded = load_shop_config(Path(path_str))
                    snippet = self._memory.get_last_ai_snippet(item.session_id)
                    scroll_times = int(item.payload.get("scroll_times") or 8)
                    _log_async(
                        f"EXEC REACQUIRE_CONTEXT action_id={item.action_id} scroll={scroll_times} anchor_len={len(snippet)}"
                    )
                    _log_async("（首次 OCR 若加载 Paddle 可能需数十秒，状态栏 queue/busy 会保持直至完成）")
                    res = run_reacquire_physical(
                        shop_loaded,
                        driver,
                        scroll_times=scroll_times,
                        last_ai_snippet=snippet or None,
                    )
                    self._memory.set_patch(item.session_id, res.patch_text)
                    try:
                        conn = self._db_conn()
                        ensure_session_row(
                            conn,
                            session_id=item.session_id,
                            brand_id=getattr(shop_loaded, "brand_id"),
                            shop_id=(
                                getattr(shop_loaded, "shop_id")
                                or (getattr(shop_loaded, "brand_id") + ":" + getattr(shop_loaded, "shop_code"))
                            ),
                            source_id=item.source_id,
                            shop_code=getattr(shop_loaded, "shop_code"),
                            shop_display_name=getattr(shop_loaded, "shop_display_name"),
                        )
                        set_manual_hold(conn, item.session_id, manual_hold=False)
                        insert_session_event(
                            conn,
                            SessionEvent(
                                event_id="",
                                brand_id=getattr(shop_loaded, "brand_id"),
                                shop_id=(
                                    getattr(shop_loaded, "shop_id")
                                    or (getattr(shop_loaded, "brand_id") + ":" + getattr(shop_loaded, "shop_code"))
                                ),
                                session_id=item.session_id,
                                source_id=item.source_id,
                                event_type="reacquire_completed",
                                payload={
                                    "engine": res.engine,
                                    "avg_conf": res.avg_conf,
                                    "anchor_matched": res.anchor_matched,
                                    "patch_chars": len(res.patch_text or ""),
                                },
                                evidence_confidence=float(res.avg_conf),
                            ),
                        )
                    except Exception as e:
                        _log_async(f"REACQUIRE DB 更新失败: {e!r}")
                    _log_async(
                        f"REACQUIRE ok engine={res.engine} anchor={res.anchor_matched} patch_chars={len(res.patch_text)}"
                    )
                except Exception as e:
                    _log_async(f"REACQUIRE 失败: {e!r}")
                return

            text = str(item.payload.get("text") or "")
            _log_async(f"EXEC {item.kind} action_id={item.action_id} text={text!r}")
            time.sleep(0.12)

        executor = SequentialExecutor(
            action_queue=q,
            handlers={
                ActionKind.NOOP: handle,
                ActionKind.SEND_TEXT: handle,
                ActionKind.SOOTHE_WAIT: handle,
                ActionKind.SEND_IMAGE: handle,
                ActionKind.REACQUIRE_CONTEXT: handle,
            },
        )
        executor.start()
        self._runtime = CoreRuntime(q=q, pipeline=pipeline, executor=executor, driver=driver, shop=shop if self._shop_cfg_path is not None else None)

        self.status_label.setText("状态：运行中（MVP Core）")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.btn_soothe.setEnabled(True)
        self.btn_noop.setEnabled(True)
        self.cb_real_send.setEnabled(True)
        self.btn_ocr.setEnabled(True)
        self.btn_right.setEnabled(True)
        self.btn_order.setEnabled(True)
        self.btn_events.setEnabled(True)
        self.btn_resume.setEnabled(True)
        self._append("Core 已启动（SequentialExecutor 在线）")

    def _on_stop(self) -> None:
        rt = self._runtime
        if rt is None:
            return
        rt.executor.stop()
        self._runtime = None
        self.status_label.setText("状态：已停止")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.btn_soothe.setEnabled(False)
        self.btn_noop.setEnabled(False)
        self.cb_real_send.setEnabled(False)
        self.btn_ocr.setEnabled(False)
        self.btn_right.setEnabled(False)
        self.btn_order.setEnabled(False)
        self.btn_events.setEnabled(False)
        self.btn_resume.setEnabled(False)
        self._append("Core 已停止")

    def _on_pick_shop(self) -> None:
        from apps.core.runtime_paths import configs_dir

        path, _ = QFileDialog.getOpenFileName(
            self, "选择店铺配置 YAML", str(configs_dir()), "YAML (*.yml *.yaml)"
        )
        if not path:
            return
        self._shop_cfg_path = Path(path)
        self.shop_label.setText(f"店铺：{self._shop_cfg_path.name}（待启动加载）")
        self._append(f"已选择店铺配置：{self._shop_cfg_path}")

    def _on_open_rules(self) -> None:
        dlg = RulesEditorDialog(self)
        dlg.exec()

    def _on_send(self) -> None:
        rt = self._runtime
        if rt is None:
            return
        rt.pipeline.enqueue_send_text(self._source_id, self._session_id, text="您好，在的呢～")
        self._append("ENQ SendText")

    def _on_soothe(self) -> None:
        rt = self._runtime
        if rt is None:
            return
        rt.pipeline.enqueue_soothe_wait(self._source_id, self._session_id)
        self._append("ENQ SootheWait (priority)")

    def _on_noop(self) -> None:
        rt = self._runtime
        if rt is None:
            return
        rt.pipeline.enqueue_noop(self._source_id, self._session_id)
        self._append("ENQ NOOP")

    def _on_test_ocr(self) -> None:
        rt = self._runtime
        if rt is None or rt.shop is None:
            self._append("请先选择店铺配置（包含 ocr_chat_rect），再启动 Core")
            return
        shop = rt.shop
        rect = getattr(shop, "ocr_chat_rect", None)
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            self._append("ocr_chat_rect 未配置或无效")
            return
        cap = ScreenCapture()
        img = cap.grab_rgb(rect)
        result = get_dual_ocr_engine().recognize(img)
        self._append(f"OCR engine={result.engine} avg_conf={result.avg_confidence:.2f} spans={len(result.spans)}")
        preview = " | ".join(s.text for s in result.spans[:12])
        if preview:
            self._append(f"OCR preview: {preview}")

    def _db_conn(self):
        from apps.core.runtime_paths import default_sqlite_db_path

        db_path = default_sqlite_db_path()
        conn = connect(db_path)
        init_db(conn)
        return conn

    def _on_test_right_change(self) -> None:
        rt = self._runtime
        if rt is None or rt.shop is None:
            self._append("请先选择店铺配置（包含 ocr_right_rect），再启动 Core")
            return
        shop = rt.shop
        rect = getattr(shop, "ocr_right_rect", None)
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            self._append("ocr_right_rect 未配置或无效")
            return
        cap = ScreenCapture()
        img = cap.grab_rgb(rect)
        sig = image_sig(img)
        changed = self._last_right_sig is not None and sig != self._last_right_sig
        self._last_right_sig = sig
        self._append(f"RIGHT sig={sig[:10]} changed={changed}")
        if changed:
            try:
                conn = self._db_conn()
                ensure_session_row(
                    conn,
                    session_id=self._session_id,
                    brand_id=getattr(shop, "brand_id"),
                    shop_id=(getattr(shop, "shop_id") or (getattr(shop, "brand_id") + ":" + getattr(shop, "shop_code"))),
                    source_id=self._source_id,
                    shop_code=getattr(shop, "shop_code"),
                    shop_display_name=getattr(shop, "shop_display_name"),
                )
                insert_session_event(
                    conn,
                    SessionEvent(
                        event_id="",
                        brand_id=getattr(shop, "brand_id"),
                        shop_id=(getattr(shop, "shop_id") or (getattr(shop, "brand_id") + ":" + getattr(shop, "shop_code"))),
                        session_id=self._session_id,
                        source_id=self._source_id,
                        event_type="right_panel_changed",
                        payload={"sig": sig},
                        evidence_confidence=1.0,
                    ),
                )
                self._append("已写入事件：right_panel_changed")
            except Exception as e:
                self._append(f"写事件失败: {e!r}")

    def _on_test_order_popup(self) -> None:
        rt = self._runtime
        if rt is None or rt.shop is None:
            self._append("请先选择店铺配置（包含 ocr_chat_rect），再启动 Core")
            return
        shop = rt.shop
        rect = getattr(shop, "ocr_chat_rect", None)
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            self._append("ocr_chat_rect 未配置或无效")
            return
        cap = ScreenCapture()
        img = cap.grab_rgb(rect)
        result = get_dual_ocr_engine().recognize(img)
        joined = " ".join(s.text for s in result.spans)
        hit = any(k in joined for k in ["已下单", "已拍下", "拍下了", "下单成功"])
        self._append(f"ORDER_POPUP hit={hit} engine={result.engine}")
        if hit:
            try:
                conn = self._db_conn()
                ensure_session_row(
                    conn,
                    session_id=self._session_id,
                    brand_id=getattr(shop, "brand_id"),
                    shop_id=(getattr(shop, "shop_id") or (getattr(shop, "brand_id") + ":" + getattr(shop, "shop_code"))),
                    source_id=self._source_id,
                    shop_code=getattr(shop, "shop_code"),
                    shop_display_name=getattr(shop, "shop_display_name"),
                )
                insert_session_event(
                    conn,
                    SessionEvent(
                        event_id="",
                        brand_id=getattr(shop, "brand_id"),
                        shop_id=(getattr(shop, "shop_id") or (getattr(shop, "brand_id") + ":" + getattr(shop, "shop_code"))),
                        session_id=self._session_id,
                        source_id=self._source_id,
                        event_type="order_placed",
                        payload={"keywords_hit": True, "engine": result.engine},
                        evidence_confidence=float(result.avg_confidence),
                    ),
                )
                self._append("已写入事件：order_placed")
                set_manual_hold(conn, self._session_id, manual_hold=True)
                self._append("已自动置 ManualHold（manual_hold=1）")
                bump_priority(conn, self._session_id, priority=100)
                self._append("已提升会话优先级 priority=100")
            except Exception as e:
                self._append(f"写事件失败: {e!r}")

    def _on_view_recent_events(self) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            conn = self._db_conn()
            cur = conn.execute(
                """
                SELECT event_type, created_at, payload_json
                FROM session_events
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (self._session_id,),
            )
            rows = cur.fetchall()
            if not rows:
                self._append("最近事件：空")
                return
            self._append("最近事件：")
            for r in rows:
                self._append(f"- {r['created_at']}  {r['event_type']}  {str(r['payload_json'])[:120]}")
        except Exception as e:
            self._append(f"读取事件失败: {e!r}")

    def _on_resume_ai(self) -> None:
        rt = self._runtime
        if rt is None or rt.shop is None:
            self._append("请先选择店铺配置再启动 Core")
            return
        if self._shop_cfg_path is None:
            self._append("shop_cfg_path 为空")
            return
        rt.pipeline.enqueue_reacquire_context(
            self._source_id, self._session_id, shop_cfg_path=str(self._shop_cfg_path.resolve())
        )
        self._append("已排队：回读上下文（REACQUIRE_CONTEXT，执行器内滚动+OCR）")

    def _tick(self) -> None:
        rt = self._runtime
        if rt is None:
            return
        stats = rt.executor.stats()
        qn = rt.q.qsize()
        self.status_label.setText(
            f"状态：运行中  queue={qn}  busy={rt.executor.is_busy()}  last={stats.last_kind}:{stats.last_action_id}"
        )


def main() -> None:
    app = QApplication(sys.argv)
    w = LegacyMvpWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

