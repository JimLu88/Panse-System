from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from PyQt6.QtCore import QDate, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QWidget,
)

from apps.core.configs.base_settings import load_base_settings
from apps.core.configs.loader import load_shop_config
from apps.core.crm.db import connect, init_db
from apps.core.crm.policy_repo import ensure_policy_row, get_policy, update_policy_fields
from apps.core.orchestrator import health as companion_health
from apps.core.orchestrator.companion_reports import pick_report_for_schedule
from apps.core.runtime_paths import (
    bundle_root,
    default_sqlite_db_path,
    is_frozen_onefile,
    profile_name,
    project_root,
    ui_prefs_path,
)
from apps.ui.business_log import humanize_log_line
from apps.ui.shop_presets import list_workbench_shop_picks
from apps.core.shadow.observer import ShadowObserver
from apps.ui.dialogs.image_gallery_dialog import ImageGalleryDialog
from apps.ui.views.dashboard_view import DashboardView
from apps.ui.views.kb_view import KBManagementView
from apps.ui.views.settings_view import SettingsView
from apps.ui.workbench_session import AutomationSession


class WorkbenchShell(QMainWindow):
    """左侧导航 + 堆叠页；控制台只展示业务向文案。"""

    # 线程安全的日志信号：任何线程 emit → 自动 post 到主线程追加
    _log_signal: pyqtSignal = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._log_signal.connect(self._append_log_line, Qt.ConnectionType.QueuedConnection)
        self._companion_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="companion_report"
        )
        self._companion_report_running = False
        from apps.__version__ import __version__
        pn = profile_name()
        _ver_str = f"v{__version__}"
        self.setWindowTitle(
            f"智能客服中控台 {_ver_str} — 实例：{pn}" if pn else f"智能客服中控台 {_ver_str}"
        )
        self.resize(1100, 760)

        self._session = AutomationSession()
        self._shadow_observer: ShadowObserver | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        self.nav = QListWidget()
        self.nav.setFixedWidth(208)
        for text in ("工作台", "话术库", "设置中心", "手机接待"):
            QListWidgetItem(text, self.nav)
        self.nav.currentRowChanged.connect(self._on_nav)

        from apps.mobile.ui.mobile_tab import MobileTab

        self.stack = QStackedWidget()
        self.page_dashboard = DashboardView(self)
        self.page_kb = KBManagementView(self)
        self.page_settings = SettingsView(self)
        self.page_mobile = MobileTab(self)
        self.stack.addWidget(self.page_dashboard)
        self.stack.addWidget(self.page_kb)
        self.stack.addWidget(self.page_settings)
        self.stack.addWidget(self.page_mobile)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.nav)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        root.addWidget(split)

        self.nav.setCurrentRow(0)

        self._fill_shop_combo()
        self._refresh_pause_ui()
        self.page_kb.shops_changed.connect(self._on_kb_shops_changed)
        self.page_dashboard.combo_shop.currentIndexChanged.connect(self._on_shop_changed)
        self.page_dashboard.btn_master.clicked.connect(self._toggle_master)
        self.page_dashboard.btn_pause_auto.clicked.connect(self._toggle_pause_auto)
        self.page_dashboard.btn_handoff.clicked.connect(self._handoff_back)
        self.page_dashboard.btn_save_settings.clicked.connect(self._save_settings)
        self.page_dashboard.request_open_product_gallery.connect(
            lambda: self._open_image_gallery("product")
        )
        self.page_dashboard.request_open_tutorial_gallery.connect(
            lambda: self._open_image_gallery("tutorial")
        )
        self.page_dashboard.request_shop_calibration.connect(self._open_shop_calibration)
        self.page_dashboard.shadow_observation_toggled.connect(self._on_shadow_observation)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(600)
        self._status_timer.timeout.connect(self._tick_status)
        self._status_timer.start()

        self._companion_schedule_timer = QTimer(self)
        self._companion_schedule_timer.setInterval(60_000)
        self._companion_schedule_timer.timeout.connect(self._companion_scheduler_tick)
        self._companion_schedule_timer.start()

        self.page_dashboard.cb_companion.toggled.connect(self._on_companion_toggled)
        self.page_dashboard.btn_companion_fix.clicked.connect(
            lambda: self._open_companion_chat("light_fix")
        )
        self.page_dashboard.btn_companion_deep.clicked.connect(
            lambda: self._open_companion_chat("deep_check")
        )
        self.page_dashboard.btn_companion_opt.clicked.connect(
            lambda: self._open_companion_chat("optimization")
        )
        try:
            en = companion_health.sync_companion_enabled_from_db()
            self.page_dashboard.cb_companion.blockSignals(True)
            self.page_dashboard.cb_companion.setChecked(en)
            self.page_dashboard.cb_companion.blockSignals(False)
        except Exception:
            pass

        self._apply_shop_selection()
        self._refresh_master_ui()

    @pyqtSlot()
    def _slot_run_sensitive_review(self) -> None:
        """Brain 后台线程经 BlockingQueuedConnection 调度；展示敏感回复预览对话框。"""
        from apps.ui.dialogs.outbound_review_dialog import OutboundReviewDialog

        pl = getattr(self._session, "_sensitive_review_payload", None)
        if not isinstance(pl, dict):
            self._session._sensitive_review_result[0] = False
            return
        on_commit = pl.get("on_commit")
        on_abort = pl.get("on_abort_hold")
        if not callable(on_commit) or not callable(on_abort):
            self._session._sensitive_review_result[0] = False
            return
        dlg = OutboundReviewDialog(
            self,
            buyer_preview=str(pl.get("buyer_preview") or ""),
            segments=list(pl.get("segments") or []),
            image_items=list(pl.get("image_items") or []),
            default_delay_s=int(pl.get("delay_seconds") or 8),
            on_commit=on_commit,
            on_abort_hold=on_abort,
        )
        dlg.exec()
        self._session._sensitive_review_result[0] = bool(dlg.was_sent)

    # ── UI 偏好持久化 ──────────────────────────────────────────────────────
    def _load_ui_pref(self, key: str, default=None):
        try:
            p = ui_prefs_path()
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get(key, default)
        except Exception:
            pass
        return default

    def _save_ui_pref(self, key: str, value) -> None:
        try:
            p = ui_prefs_path()
            data: dict = {}
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[key] = value
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _fill_shop_combo(self) -> None:
        self.page_dashboard.combo_shop.blockSignals(True)
        self.page_dashboard.combo_shop.clear()
        conn = connect(default_sqlite_db_path())
        init_db(conn)
        try:
            picks = list_workbench_shop_picks(conn)
        finally:
            conn.close()
        for label, path, sid in picks:
            yaml_str = str(path.resolve()) if path is not None else ""
            self.page_dashboard.combo_shop.addItem(label, (yaml_str, sid))

        # 恢复上次选择的店铺
        last_sid = self._load_ui_pref("last_shop_id", "")
        if last_sid:
            for i in range(self.page_dashboard.combo_shop.count()):
                d = self.page_dashboard.combo_shop.itemData(i)
                if isinstance(d, tuple) and str(d[1] or "") == last_sid:
                    self.page_dashboard.combo_shop.setCurrentIndex(i)
                    break

        self.page_dashboard.combo_shop.blockSignals(False)

    @pyqtSlot()
    def _on_kb_shops_changed(self) -> None:
        """话术库店铺增删改后，与工作台「当前店铺」下拉同步。"""
        prev = self.page_dashboard.combo_shop.currentData()
        prev_sid = ""
        if isinstance(prev, tuple) and len(prev) >= 2:
            prev_sid = str(prev[1] or "")
        self._fill_shop_combo()
        if prev_sid:
            for i in range(self.page_dashboard.combo_shop.count()):
                d = self.page_dashboard.combo_shop.itemData(i)
                if isinstance(d, tuple) and str(d[1] or "") == prev_sid:
                    self.page_dashboard.combo_shop.setCurrentIndex(i)
                    self._apply_shop_selection()
                    return
        if self.page_dashboard.combo_shop.count() > 0:
            self.page_dashboard.combo_shop.setCurrentIndex(0)
        self._apply_shop_selection()

    def _on_shop_changed(self) -> None:
        raw = self.page_dashboard.combo_shop.currentData()
        if isinstance(raw, tuple) and len(raw) >= 2:
            sid = str(raw[1] or "")
            if sid:
                self._save_ui_pref("last_shop_id", sid)
        self._apply_shop_selection()

    def _apply_shop_selection(self) -> None:
        idx = self.page_dashboard.combo_shop.currentIndex()
        if idx < 0:
            return
        raw = self.page_dashboard.combo_shop.currentData()
        yaml_str = ""
        if isinstance(raw, tuple):
            yaml_str = str(raw[0] or "")
        elif raw is not None:
            yaml_str = str(raw)
        if not yaml_str:
            self._session.shop_cfg_path = None
            return
        p = Path(yaml_str)
        if not p.is_file():
            self._session.shop_cfg_path = None
            return
        self._session.shop_cfg_path = p
        self._load_policy_from_db()

    def _resolve_shop_yaml_for_calibration(self) -> Path | None:
        """确保当前下拉选中店铺有可写的 yaml；必要时从数据库生成。"""
        idx = self.page_dashboard.combo_shop.currentIndex()
        if idx < 0:
            QMessageBox.warning(
                self,
                "取点校准",
                "请先在「当前店铺」里选择一个店铺。",
            )
            return None

        raw = self.page_dashboard.combo_shop.currentData()
        yaml_str = ""
        sid = ""
        if isinstance(raw, tuple):
            yaml_str = str(raw[0] or "")
            sid = str(raw[1] or "")

        if yaml_str:
            p = Path(yaml_str)
            if p.is_file():
                return p

        if not sid:
            QMessageBox.warning(
                self,
                "取点校准",
                "当前选项没有可用的店铺配置文件，请刷新列表或先在话术库登记店铺。",
            )
            return None

        conn = connect(default_sqlite_db_path())
        init_db(conn)
        try:
            row = conn.execute(
                "SELECT brand_id, shop_code, display_name FROM shops WHERE shop_id = ?",
                (sid,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            QMessageBox.warning(
                self,
                "取点校准",
                "数据库中找不到该店铺记录；请先在话术库登记店铺后再试。",
            )
            return None

        bid, code, dn = str(row[0] or ""), str(row[1] or ""), str(row[2] or "")
        try:
            from apps.core.configs.shop_yaml_bootstrap import ensure_shop_config_yaml

            p = ensure_shop_config_yaml(
                brand_id=bid,
                shop_code=code,
                display_name=(dn or code).strip() or code,
                shop_id=sid,
            )
        except Exception as e:
            QMessageBox.critical(self, "配置文件生成失败", str(e))
            return None

        self._fill_shop_combo()
        for i in range(self.page_dashboard.combo_shop.count()):
            data = self.page_dashboard.combo_shop.itemData(i)
            if isinstance(data, tuple) and str(data[1]) == sid:
                self.page_dashboard.combo_shop.setCurrentIndex(i)
                break
        self._apply_shop_selection()
        return p

    @pyqtSlot()
    def _open_shop_calibration(self) -> None:
        path = self._resolve_shop_yaml_for_calibration()
        if path is None:
            return
        self._session.shop_cfg_path = path
        from apps.ui.dialogs.shop_calibration_dialog import ShopCalibrationDialog

        dlg = ShopCalibrationDialog(self, path)
        dlg.exec()

    @pyqtSlot(str)
    def _append_log_line(self, line: str) -> None:
        """槽函数：只在主线程执行，安全地追加日志到 UI。"""
        self.page_dashboard.activity_log.appendPlainText(line)
        # v1.6.1 修复档案：全自动记录已知问题指纹（用户零操作）。全 try/except，绝不影响显示。
        try:
            from apps.core.diagnostics.fix_archive import record_log_line
            record_log_line(line)
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        friendly = humanize_log_line(msg)
        if not (friendly or "").strip():
            return
        ts = time.strftime("%H:%M")
        line = f"[{ts}] {friendly}"
        # 使用信号跨线程安全传递：无论从主线程还是后台线程调用都正确
        self._log_signal.emit(line)

    def _toggle_master(self) -> None:
        running = self._session.is_core_running() and self._session.is_brain_running()
        if running:
            self._exit_shadow_observation_silent()
            self._session.stop_core(self._log)
            self._refresh_master_ui()
            self._log("已停止全自动托管。")
            return

        path = self._session.shop_cfg_path
        if path is None or not path.is_file():
            QMessageBox.warning(
                self,
                "无法启动",
                "请先在「当前店铺」里选择可托管的店铺；若刚新增店铺，请点一次「刷新」或切换选项后再试。",
            )
            return
        try:
            # 先把当前界面上的勾选写入数据库，否则后台仍按库里旧的 0 运行，且原先成功后还会 reload 把勾选刷掉。
            self._persist_dashboard_policy_to_db()
        except Exception as e:
            QMessageBox.critical(self, "策略未能写入数据库", str(e))
            return
        self._session.set_message_input_mode("ocr")
        try:
            self._session.start_core(
                shop_yaml=path,
                real_foreground=False,
                log=self._log,
            )
            st = load_base_settings()
            self._session.start_brain(self._log, settings=st, qt_shell=self)
        except Exception as e:
            QMessageBox.critical(self, "启动失败", str(e))
            try:
                if self._session.is_brain_running():
                    self._session.stop_brain(self._log)
                if self._session.is_core_running():
                    self._session.stop_core(self._log)
            except Exception:
                pass
            self._refresh_master_ui()
            return

        self._refresh_master_ui()

    def _toggle_pause_auto(self) -> None:
        if self.page_dashboard.btn_shadow_observe.isChecked():
            QMessageBox.information(
                self,
                "人工观测模式",
                "当前已开启「人工观测模式」，自动回复已暂停。\n请先退出观测模式，再使用本按钮。",
            )
            return
        if not self._session.is_brain_running():
            return
        try:
            if self._session.is_brain_paused():
                self._session.resume_brain()
                self._log("已恢复自动回复。")
            else:
                self._session.pause_brain()
                self._log("已暂停自动回复（可手动在千牛打字；点「继续自动回复」恢复）。")
        except Exception as e:
            QMessageBox.warning(self, "操作失败", str(e))
            return
        self._refresh_pause_ui()

    def _refresh_pause_ui(self) -> None:
        brain = self._session.is_brain_running()
        paused = self._session.is_brain_paused() if brain else False
        shadow = self.page_dashboard.btn_shadow_observe.isChecked()
        self.page_dashboard.set_pause_controls(
            brain_running=brain,
            auto_paused=paused,
            shadow_on=shadow,
        )

    def _refresh_master_ui(self) -> None:
        running = self._session.is_core_running() and self._session.is_brain_running()
        self.page_dashboard.set_master_running(running)
        self.page_dashboard.combo_shop.setEnabled(not running)
        if not running:
            self._exit_shadow_observation_silent()
        self._refresh_pause_ui()

    def _handoff_back(self) -> None:
        try:
            self._session.enqueue_resume_ai(self._log)
        except Exception as e:
            QMessageBox.warning(
                self,
                "暂时无法交回",
                str(e),
            )

    def _activity_log_excerpt(self, max_chars: int) -> str:
        t = self.page_dashboard.activity_log.toPlainText()
        if not t:
            return ""
        s = t.strip()
        return s[-max_chars:] if len(s) > max_chars else s

    def _exit_shadow_observation_silent(self) -> None:
        if self._shadow_observer is None:
            return
        excerpt = self._activity_log_excerpt(1200)
        try:
            self._shadow_observer.exit_and_evolve(
                resume_brain=lambda: self._session.resume_brain(),
                settings=load_base_settings(),
                customer_scene_excerpt=excerpt,
            )
        finally:
            self._shadow_observer = None
        QTimer.singleShot(0, lambda: self.page_dashboard.set_shadow_observation(False))
        QTimer.singleShot(0, self._refresh_pause_ui)

    def _on_shadow_observation(self, enabled: bool) -> None:
        if enabled:
            if not self._session.is_brain_running():
                QMessageBox.warning(
                    self,
                    "无法进入观测模式",
                    "请先启动「全自动客服系统」，再进入人工观测模式。",
                )
                QTimer.singleShot(0, lambda: self.page_dashboard.set_shadow_observation(False))
                return
            try:
                self._shadow_observer = ShadowObserver(
                    session_id=self._session.session_id,
                    log=self._log,
                )
                self._shadow_observer.enter(pause_brain=lambda: self._session.pause_brain())
                QTimer.singleShot(0, self._refresh_pause_ui)
            except Exception as e:
                self._shadow_observer = None
                QMessageBox.critical(self, "观测模式失败", str(e))
                QTimer.singleShot(0, lambda: self.page_dashboard.set_shadow_observation(False))
            return
        self._exit_shadow_observation_silent()
        self._log("已退出人工观测模式。")
        self._refresh_pause_ui()

    def _open_image_gallery(self, category: str) -> None:
        path = self._session.shop_cfg_path
        if path is None or not path.is_file():
            QMessageBox.warning(
                self,
                "未选择店铺",
                "请先在「当前店铺」里选择店铺配置，再打开图库。",
            )
            return

        def do_send(abs_path: str, meta: dict) -> None:
            self._session.enqueue_send_image(image_path=Path(abs_path), chat_log_meta=meta)

        on_send = do_send if self._session.is_core_running() else None
        dlg = ImageGalleryDialog(
            parent=self,
            shop_yaml=path,
            category=category,
            on_send_image=on_send,
        )
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setWindowFlag(Qt.WindowType.Window, True)
        dlg.show()

    def _tick_status(self) -> None:
        self.page_dashboard.status_strip.setText(self._session.status_for_owner())

    def _on_nav(self, row: int) -> None:
        if row >= 0:
            self.stack.setCurrentIndex(row)
            # 切换堆叠页后补一次滚动区内宽（否则首次进入「设置」等页视口宽度未就绪会锁成窄条）
            QTimer.singleShot(0, lambda r=row: self._sync_visible_stack_scroll_width(r))
            QTimer.singleShot(40, lambda r=row: self._sync_visible_stack_scroll_width(r))

    def _sync_visible_stack_scroll_width(self, row: int) -> None:
        w = self.stack.widget(row)
        if w is not None and hasattr(w, "_sync_inner_scroll_width"):
            w._sync_inner_scroll_width()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        try:
            self._companion_pool.shutdown(wait=False)
        except Exception:
            pass
        super().closeEvent(event)

    def _on_companion_toggled(self, checked: bool) -> None:
        try:
            companion_health.save_companion_enabled(checked)
            self._log("AI 陪伴监控已开启。" if checked else "AI 陪伴监控已关闭。")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            self.page_dashboard.cb_companion.blockSignals(True)
            self.page_dashboard.cb_companion.setChecked(not checked)
            self.page_dashboard.cb_companion.blockSignals(False)

    def _companion_scheduler_tick(self) -> None:
        if not companion_health.is_companion_ui_enabled():
            return
        m = self._session.executor_metrics()
        if m is not None:
            qd, busy = m
            companion_health.record_executor_snapshot(
                queue_depth=qd, executor_busy=busy
            )
        self._maybe_auto_companion_report()

    def _maybe_auto_companion_report(self) -> None:
        if self._companion_report_running:
            return
        row = companion_health.load_companion_settings()
        if not row.enabled:
            return
        db_path = default_sqlite_db_path()
        today = date.today().isoformat()
        ds = companion_health.days_since_anchor(row.anchor_started_at)

        need = False
        if ds is not None and ds < 3:
            if row.last_bug_report_date != today:
                need = True
        else:
            if row.last_optimization_report_date is None:
                need = True
            else:
                try:
                    last = datetime.strptime(
                        row.last_optimization_report_date[:10], "%Y-%m-%d"
                    ).date()
                    if (date.today() - last).days >= 7:
                        need = True
                except Exception:
                    need = True
        if not need:
            return

        self._companion_report_running = True

        def work() -> str:
            row2 = companion_health.load_companion_settings()
            ds2 = companion_health.days_since_anchor(row2.anchor_started_at)
            kind, md = pick_report_for_schedule(
                db_path=db_path,
                anchor_started_at=row2.anchor_started_at,
            )
            companion_health.insert_companion_report(kind, md)
            day_s = date.today().isoformat()
            if ds2 is not None and ds2 < 3:
                companion_health.update_report_dates(bug_report_date=day_s)
            else:
                companion_health.update_report_dates(optimization_date=day_s)
            return kind

        fut = self._companion_pool.submit(work)

        def _done(f) -> None:  # noqa: ANN001
            self._companion_report_running = False
            try:
                kind = f.result()
                QTimer.singleShot(
                    0,
                    lambda k=kind: self._log(f"AI 陪伴：已自动生成报表（{k}）"),
                )
            except Exception as e:
                QTimer.singleShot(
                    0,
                    lambda err=str(e): self._log(f"AI 陪伴报表生成失败：{err}"),
                )

        fut.add_done_callback(_done)

    def _recent_console_excerpt(self) -> str:
        te = self.page_dashboard.activity_log
        return te.toPlainText()[-14000:]

    def _open_companion_chat(self, mode: str) -> None:
        """打开 AI 陪伴对话（light_fix / deep_check / optimization）。"""
        from apps.core.ai.llm_client import deep_analysis_api_configured
        from apps.core.orchestrator.companion_analysis import CompanionMode
        from apps.ui.dialogs.companion_chat_dialog import CompanionChatDialog

        cm: CompanionMode = mode  # type: ignore[assignment]
        existing = getattr(self, "_companion_chat_dlg", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        if not companion_health.is_companion_ui_enabled():
            try:
                companion_health.save_companion_enabled(True)
                self.page_dashboard.cb_companion.blockSignals(True)
                self.page_dashboard.cb_companion.setChecked(True)
                self.page_dashboard.cb_companion.blockSignals(False)
                self._log("AI 陪伴监控已自动开启，以便记录运行异常。")
            except Exception as e:
                self._log(f"AI 陪伴监控自动开启失败：{e!r}")

        st = load_base_settings()
        if not deep_analysis_api_configured(st):
            QMessageBox.information(
                self,
                "深度模型未配置",
                "未配置「AI 陪伴与深度分析模型」或 API 密钥。\n\n"
                "对话窗口仍会打开；配置完成后可获得大模型分析与摘要。",
            )

        dlg = CompanionChatDialog(
            self,
            mode=cm,
            pool=self._companion_pool,
            console_excerpt=self._recent_console_excerpt(),
            on_log=self._log,
        )
        self._companion_chat_dlg = dlg
        dlg.finished.connect(lambda: setattr(self, "_companion_chat_dlg", None))
        dlg.show()
        labels = {
            "light_fix": "问题修复",
            "deep_check": "深度检查",
            "optimization": "功能优化",
        }
        self._log(f"AI 陪伴：已打开「{labels.get(mode, mode)}」对话。")

    def _companion_manual_report(self) -> None:
        """兼容旧入口：等同「问题修复」。"""
        self._open_companion_chat("light_fix")

    def _load_policy_from_db(self) -> None:
        try:
            path = self._session.shop_cfg_path
            if path is None or not path.is_file():
                return
            shop = load_shop_config(path)
            bid = shop.brand_id
            sid = shop.shop_id or (shop.brand_id + ":" + shop.shop_code)
            conn = connect(default_sqlite_db_path())
            init_db(conn)
            ensure_policy_row(conn, brand_id=bid, shop_id=sid)
            pol = get_policy(conn, brand_id=bid, shop_id=sid)
            conn.close()
            thr = int(pol.anger_hit_threshold)
            thr = max(1, min(4, thr))
            self.page_dashboard.combo_anger.setCurrentIndex(thr - 1)
            self.page_dashboard.cb_strong_reminder.setChecked(bool(pol.strong_reminder))
            self.page_dashboard.cb_popup_dismiss.setChecked(bool(pol.popup_auto_dismiss))
            self.page_dashboard.cb_price_protect.setChecked(bool(pol.price_sensitive_handoff))
            self.page_dashboard.cb_jim_price_full.setChecked(bool(pol.jim_price_full_takeover))
            self.page_dashboard.cb_real_photo_jim.setChecked(bool(pol.real_photo_jim_intercept))
            self.page_dashboard.cb_jim_photo_full.setChecked(bool(pol.jim_photo_full_takeover))
            self.page_dashboard.cb_outbound_preview.setChecked(bool(pol.outbound_preview_enabled))
            self.page_dashboard.spin_outbound_delay.setValue(
                max(5, min(20, int(pol.outbound_preview_delay_seconds or 8)))
            )
            self.page_dashboard._sync_jim_sub_controls()
        except Exception:
            pass

    def _persist_dashboard_policy_to_db(self) -> None:
        """把工作台策略控件写入 policy_settings（无弹窗）。启动托管前调用，保证后台与界面一致。"""
        path = self._session.shop_cfg_path
        if path is None or not path.is_file():
            raise RuntimeError("未选择店铺配置文件")
        shop = load_shop_config(path)
        bid = shop.brand_id
        sid = shop.shop_id or (shop.brand_id + ":" + shop.shop_code)
        sr = 1 if self.page_dashboard.cb_strong_reminder.isChecked() else 0
        until = QDate.currentDate().addDays(30).toString("yyyy-MM-dd") if sr else None
        conn = connect(default_sqlite_db_path())
        init_db(conn)
        ensure_policy_row(conn, brand_id=bid, shop_id=sid)
        update_policy_fields(
            conn,
            brand_id=bid,
            shop_id=sid,
            anger_hit_threshold=int(self.page_dashboard.combo_anger.currentData()),
            strong_reminder=sr,
            strong_reminder_until=until,
            popup_auto_dismiss=1 if self.page_dashboard.cb_popup_dismiss.isChecked() else 0,
            price_sensitive_handoff=1 if self.page_dashboard.cb_price_protect.isChecked() else 0,
            jim_price_full_takeover=1 if self.page_dashboard.cb_jim_price_full.isChecked() else 0,
            real_photo_jim_intercept=1 if self.page_dashboard.cb_real_photo_jim.isChecked() else 0,
            jim_photo_full_takeover=1 if self.page_dashboard.cb_jim_photo_full.isChecked() else 0,
            outbound_preview_enabled=1 if self.page_dashboard.cb_outbound_preview.isChecked() else 0,
            outbound_preview_delay_seconds=int(self.page_dashboard.spin_outbound_delay.value()),
        )
        conn.close()

    def _save_settings(self) -> None:
        path = self._session.shop_cfg_path
        if path is None or not path.is_file():
            QMessageBox.warning(self, "无法保存", "请先选择当前店铺。")
            return
        try:
            self._persist_dashboard_policy_to_db()
            self._log("本页设置已保存。")
            QMessageBox.information(self, "已保存", "策略已保存到本机。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))


def main() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    w = WorkbenchShell()
    w.show()
    sys.exit(app.exec())
