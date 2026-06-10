"""
智能客服中控台 — 面向主理人 / 店长；界面结构与「设置中心」页一致（原生控件 + QFormLayout，无全局样式表对抗系统主题）。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QDate, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from apps.core.ai.customer_reply_routing import open_panse_chat_log_in_excel
from apps.core.env_patches.dpi_assert import read_windows_dpi_percent
from apps.core.runtime_paths import profile_name
from apps.ui.jim_dark_theme import JIM_BTN_HANDOFF, JIM_BTN_START, JIM_BTN_STOP

class DashboardView(QWidget):
    """控制台首页：全局开关、人工交回、策略、业务动态。"""

    request_open_product_gallery = pyqtSignal()
    request_open_tutorial_gallery = pyqtSignal()
    shadow_observation_toggled = pyqtSignal(bool)
    request_shop_calibration = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # 富文本在获得明确宽度前会撑出过宽；优先靠内层宽度同步换行，必要时允许横向滚动
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._inner_page = QWidget()
        self._inner_page.setMinimumWidth(0)
        self._inner_page.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._scroll.setWidget(self._inner_page)
        layout = QVBoxLayout(self._inner_page)
        layout.setSpacing(12)

        outer.addWidget(self._scroll)

        from apps.__version__ import __version__, BUILD_DATE
        _ver_label = QLabel(f"<h2>工作台</h2><small style='color:gray'>版本 v{__version__}（{BUILD_DATE}）</small>")
        layout.addWidget(_ver_label)

        self.lbl_profile_tag = QLabel()
        self.lbl_profile_tag.setWordWrap(True)
        self.lbl_profile_tag.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._refresh_profile_label()
        layout.addWidget(self.lbl_profile_tag)

        self.lbl_startup_guide = QLabel()
        self.lbl_startup_guide.setWordWrap(True)
        self.lbl_startup_guide.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.lbl_startup_guide.setText(
            "<p style='margin-top:0;line-height:1.45'>"
            "<b>首次使用</b>请点右侧「使用指南」查看完整说明（含坐标校准、多店并排、启动步骤）。"
            "日常在本页选好店铺并启动即可；坐标取点后会<strong>自动保存</strong>到配置，一般无需手改文件。"
            "</p>"
        )
        row_top = QHBoxLayout()
        row_top.addWidget(self.lbl_startup_guide, 1)
        self.btn_user_guide = QPushButton("使用指南…")
        self.btn_user_guide.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_user_guide.setToolTip("打开完整说明（原「使用前必读」与多店铺并排等）。")
        self.btn_user_guide.clicked.connect(self._show_user_guide)
        row_top.addWidget(self.btn_user_guide, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row_top)

        self.lbl_dpi_status = QLabel()
        self.lbl_dpi_status.setWordWrap(True)
        self.lbl_dpi_status.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.lbl_dpi_status)

        self.status_strip = QLabel("当前状态：全自动接待未开启")
        self.status_strip.setWordWrap(True)
        self.status_strip.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.status_strip)

        # --- 模块 A：全局运行控制（同 SettingsView：QGroupBox + 表单）---
        box_a = QGroupBox("全局运行控制")
        la = QVBoxLayout(box_a)
        form_shop = QFormLayout()
        self.combo_shop = QComboBox()
        self.combo_shop.setMinimumWidth(200)
        self.combo_shop.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        form_shop.addRow("当前店铺", self.combo_shop)
        la.addLayout(form_shop)
        row_calib = QHBoxLayout()
        self.btn_shop_calibration = QPushButton("千牛屏幕坐标校准（弹窗内 10 秒倒计时取点）…")
        self.btn_shop_calibration.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shop_calibration.setToolTip(
            "步骤：① 在上方「当前店铺」选好要写的店；② 点本按钮打开校准窗口；③ 在窗口里下拉选择要写的坐标项；"
            "④ 点窗口里的「开始 10 秒倒计时并取点」，在倒计时内在目标位置左键单击一次。"
            "坐标会立刻写入该店 YAML，下次启动自动读取（改分辨率或任务栏排序后才需重录）。"
        )
        self.btn_shop_calibration.clicked.connect(self.request_shop_calibration.emit)
        row_calib.addWidget(self.btn_shop_calibration)
        row_calib.addStretch(1)
        la.addLayout(row_calib)
        _lab_calib_hint = QLabel(
            "<small><b>说明：</b>没有单独的「确认千牛窗口点击坐标」按钮；"
            "<b>10 秒倒计时</b>在校准<strong>弹窗里</strong>，点「开始…」后才会出现。</small>"
        )
        _lab_calib_hint.setWordWrap(True)
        _lab_calib_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        la.addWidget(_lab_calib_hint)
        _lab_shop_hint = QLabel(
            "<small>切换店铺后请保存下方策略；首次使用请在「设置中心」里填写接口信息。</small>"
        )
        _lab_shop_hint.setWordWrap(True)
        _lab_shop_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        la.addWidget(_lab_shop_hint)
        self.btn_master = QPushButton("启动全自动客服系统")
        self.btn_master.setMinimumHeight(52)
        self.btn_master.setCursor(Qt.CursorShape.PointingHandCursor)
        la.addWidget(self.btn_master)
        self.btn_pause_auto = QPushButton("暂停自动回复")
        self.btn_pause_auto.setMinimumHeight(44)
        self.btn_pause_auto.setEnabled(False)
        self.btn_pause_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause_auto.setToolTip(
            "全自动运行中可用：仅暂停 AI 自动发话，不关闭千牛与 Core。"
            "人工观测模式开启时此按钮会禁用（观测已自带暂停）。"
        )
        la.addWidget(self.btn_pause_auto)
        layout.addWidget(box_a)

        # --- 模块 B：人工协同 ---
        box_b = QGroupBox("人工协同（您亲自回复买家时）")
        lb = QVBoxLayout(box_b)
        self.btn_handoff = QPushButton("人工操作完毕，交还给系统自动接待")
        self.btn_handoff.setMinimumHeight(48)
        self.btn_handoff.setCursor(Qt.CursorShape.PointingHandCursor)
        lb.addWidget(self.btn_handoff)
        _lab_handoff = QLabel(
            "<small>当您已在千牛里手动聊完，点一下即可让系统重新读懂上下文并继续自动回复。</small>"
        )
        _lab_handoff.setWordWrap(True)
        _lab_handoff.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        lb.addWidget(_lab_handoff)
        row_chat_log = QHBoxLayout()
        self.btn_open_chat_log = QPushButton("用 Excel 打开客户对话日志（CSV）")
        self.btn_open_chat_log.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_chat_log.clicked.connect(self._on_open_chat_log_excel)
        row_chat_log.addWidget(self.btn_open_chat_log)
        row_chat_log.addStretch(1)
        lb.addLayout(row_chat_log)
        _lab_log = QLabel(
            "<small>日志文件：<code>data/logs/panse_customer_chats_log.csv</code>（按实例目录隔离）。</small>"
        )
        _lab_log.setWordWrap(True)
        _lab_log.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        lb.addWidget(_lab_log)
        layout.addWidget(box_b)

        box_gallery = QGroupBox("图库工具（产品 / 教程对比图）")
        lg = QVBoxLayout(box_gallery)
        _g_hint = QLabel(
            "<small>在此维护「问题标签 → 图片」；场景 B 话术库命中后，系统会按重写问句自动匹配发图。"
            "目录：<code>images/products</code> 与 <code>images/tutorials</code>（按实例隔离）。</small>"
        )
        _g_hint.setWordWrap(True)
        lg.addWidget(_g_hint)
        row_g = QHBoxLayout()
        self.btn_gallery_product = QPushButton("打开产品图库…")
        self.btn_gallery_product.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_gallery_product.clicked.connect(self.request_open_product_gallery.emit)
        self.btn_gallery_tutorial = QPushButton("打开教程 / 对比图库…")
        self.btn_gallery_tutorial.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_gallery_tutorial.clicked.connect(self.request_open_tutorial_gallery.emit)
        row_g.addWidget(self.btn_gallery_product)
        row_g.addWidget(self.btn_gallery_tutorial)
        row_g.addStretch(1)
        lg.addLayout(row_g)
        layout.addWidget(box_gallery)

        box_shadow = QGroupBox("人工观测（Shadow 影子学习）")
        lsh = QVBoxLayout(box_shadow)
        _sh_hint = QLabel(
            "<small>进入后<strong>暂停自动回复</strong>，仅旁路记录前台窗口切换与鼠标点击（可选 pynput）；"
            "不执行任何 UIAutomation 改价或后台订单操作。退出时可合并安全规则到 "
            "<code>configs/shadow/evolution_rules.json</code>（随后<strong>自动注入</strong>场景 B 前台组句提示，"
            "仍以知识库为准）。"
            "行为流水：<code>data/logs/shadow/human_action_sequence.jsonl</code>。</small>"
        )
        _sh_hint.setWordWrap(True)
        lsh.addWidget(_sh_hint)
        self.btn_shadow_observe = QPushButton("进入人工观测模式（暂停自动回复）")
        self.btn_shadow_observe.setCheckable(True)
        self.btn_shadow_observe.setEnabled(False)
        self.btn_shadow_observe.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shadow_observe.toggled.connect(self.shadow_observation_toggled.emit)
        self.btn_shadow_observe.toggled.connect(self._sync_shadow_observe_label)
        lsh.addWidget(self.btn_shadow_observe)
        layout.addWidget(box_shadow)
        self._sync_shadow_observe_label(False)

        # --- AI 陪伴（运行监控与报表；旁路写入，不占用自动点击队列）---
        box_companion = QGroupBox("AI 陪伴（运行洞察）")
        lcp = QVBoxLayout(box_companion)
        _lab_cp = QLabel(
            "<p style='line-height:1.45'><small>"
            "开启后记录运行与健康信号。<strong>三个入口分工不同：</strong>"
            "「问题修复」先问候、由你描述现象后给修复方案；"
            "「深度检查」扫描代码+全量日志；"
            "「功能优化」基于日志提出改进建议。"
            "均可多轮对话；每轮永久保存到 data/companion/conversation_full.jsonl，"
            "精简记忆合并到 ai_retrieval_context.md 供 AI 每次读取。"
            "需先在设置中心配置「AI 陪伴与深度分析模型」。"
            "</small></p>"
        )
        _lab_cp.setWordWrap(True)
        _lab_cp.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        lcp.addWidget(_lab_cp)
        row_cp = QHBoxLayout()
        self.cb_companion = QCheckBox("开启 AI 陪伴监控")
        self.cb_companion.setCursor(Qt.CursorShape.PointingHandCursor)
        row_cp.addWidget(self.cb_companion)
        self.btn_companion_fix = QPushButton("问题修复")
        self.btn_companion_fix.setToolTip(
            "轻度修复：先问候，你描述问题后结合日志给修复步骤（可多轮对话）"
        )
        self.btn_companion_fix.setCursor(Qt.CursorShape.PointingHandCursor)
        row_cp.addWidget(self.btn_companion_fix)
        self.btn_companion_deep = QPushButton("深度检查")
        self.btn_companion_deep.setToolTip(
            "扫描关键代码路径与近期全部运行日志，输出深度诊断报告"
        )
        self.btn_companion_deep.setCursor(Qt.CursorShape.PointingHandCursor)
        row_cp.addWidget(self.btn_companion_deep)
        self.btn_companion_opt = QPushButton("功能优化")
        self.btn_companion_opt.setToolTip(
            "基于近期运行数据与深度检查摘要，提出针对性优化建议"
        )
        self.btn_companion_opt.setCursor(Qt.CursorShape.PointingHandCursor)
        row_cp.addWidget(self.btn_companion_opt)
        # 兼容旧引用
        self.btn_companion_report = self.btn_companion_fix
        row_cp.addStretch(1)
        lcp.addLayout(row_cp)
        layout.addWidget(box_companion)

        # --- 模块 C：主理人业务策略 ---
        box_c = QGroupBox("主理人业务策略")
        lc = QFormLayout(box_c)

        self.combo_anger = QComboBox()
        self.combo_anger.addItem("客户连续发火 1 次后，停止自动回复并微信呼叫我", 1)
        self.combo_anger.addItem("客户连续发火 2 次后，停止自动回复并微信呼叫我", 2)
        self.combo_anger.addItem("客户连续发火 3 次后，停止自动回复并微信呼叫我", 3)
        self.combo_anger.addItem("客户连续发火 4 次后，停止自动回复并微信呼叫我", 4)
        self.combo_anger.setCurrentIndex(2)
        lc.addRow("激怒拦截", self.combo_anger)

        self.cb_strong_reminder = QCheckBox("开启强提醒（每一条新消息都推送到微信）")
        lc.addRow(self.cb_strong_reminder)

        self.cb_popup_dismiss = QCheckBox("自动关闭千牛系统弹窗与广告")
        lc.addRow(self.cb_popup_dismiss)

        self.cb_price_protect = QCheckBox(
            "涉及报价、发单、改价等敏感操作，一律转人工处理"
        )
        self.cb_price_protect.setChecked(True)
        lc.addRow(self.cb_price_protect)

        self.cb_jim_price_full = QCheckBox(
            "询价/拍下命中后走「完整 Jim」（发安抚话术 + ManualHold）；"
            "关闭则仅推送/记事件（不自动安抚、不锁会话，系统可继续自动接待）"
        )
        self.cb_jim_price_full.setChecked(True)
        lc.addRow(self.cb_jim_price_full)

        self.cb_real_photo_jim = QCheckBox(
            "客户要实拍/细节图时也立即转人工（关闭则优先走图库自动发图）"
        )
        self.cb_real_photo_jim.setChecked(True)
        lc.addRow(self.cb_real_photo_jim)

        self.cb_jim_photo_full = QCheckBox(
            "实拍/细节图命中后走「完整 Jim」；关闭则仅推送/记事件（不锁会话）"
        )
        self.cb_jim_photo_full.setChecked(True)
        lc.addRow(self.cb_jim_photo_full)

        self.cb_outbound_preview = QCheckBox(
            "敏感话术先发预览（倒计时，可立即发送或中断转人工）"
        )
        self.cb_outbound_preview.setChecked(True)
        lc.addRow(self.cb_outbound_preview)
        self.spin_outbound_delay = QSpinBox()
        self.spin_outbound_delay.setRange(5, 20)
        self.spin_outbound_delay.setSuffix(" 秒")
        self.spin_outbound_delay.setValue(8)
        lc.addRow("预览默认倒计时", self.spin_outbound_delay)

        self.btn_save_settings = QPushButton("保存以上策略")
        self.btn_save_settings.setMinimumHeight(36)
        lc.addRow(self.btn_save_settings)

        layout.addWidget(box_c)

        # --- 模块 C2：通用兜底话术 ---
        box_fb = QGroupBox("通用兜底话术")
        lfb = QVBoxLayout(box_fb)
        _lab_fb = QLabel(
            "<small>以下话术用于系统自动兜底场景（新客问候、转人工安抚、老客回购等）。"
            "点击「编辑兜底话术」可随时修改，保存后立即生效。其他话术（知识库 / 闲聊）不受影响。</small>"
        )
        _lab_fb.setWordWrap(True)
        _lab_fb.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        lfb.addWidget(_lab_fb)

        self._fb_preview = QLabel()
        self._fb_preview.setWordWrap(True)
        self._fb_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        lfb.addWidget(self._fb_preview)

        row_fb = QHBoxLayout()
        self.btn_edit_fallback = QPushButton("编辑兜底话术…")
        self.btn_edit_fallback.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_fallback.setToolTip("打开编辑面板，可修改欢迎语、转人工安抚、补单回复等兜底话术。")
        self.btn_edit_fallback.clicked.connect(self._open_fallback_editor)
        row_fb.addWidget(self.btn_edit_fallback)
        self.btn_edit_chitchat = QPushButton("编辑闲聊话术…")
        self.btn_edit_chitchat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_chitchat.setToolTip(
            "编辑客户寒暄回复（感谢、确认、问候、告别等场景），"
            "每个场景可设多条随机回复。"
        )
        self.btn_edit_chitchat.clicked.connect(self._open_chitchat_editor)
        row_fb.addWidget(self.btn_edit_chitchat)
        row_fb.addStretch(1)
        lfb.addLayout(row_fb)
        layout.addWidget(box_fb)
        self._refresh_fallback_preview()

        # --- 模块 D：业务动态 ---
        box_d = QGroupBox("今日接待动态（仅显示接待说明，不显示程序细节）")
        ld = QVBoxLayout(box_d)
        self.activity_log = QPlainTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setPlaceholderText(
            "稍后将在此显示：自动回复摘要、需要您介入的提醒等。"
        )
        self.activity_log.setMinimumHeight(220)
        ld.addWidget(self.activity_log)
        layout.addWidget(box_d)

        self._master_running = False
        # 仅主按钮使用强调色（与系统主题无关）；其余控件走原生绘制，与「设置中心」一致。
        self._style_handoff_button()
        self.refresh_startup_hints()
        self.set_master_running(False)

        self._dpi_timer = QTimer(self)
        self._dpi_timer.setInterval(4000)
        self._dpi_timer.timeout.connect(self.refresh_startup_hints)
        self._dpi_timer.start()

        QTimer.singleShot(0, self._sync_inner_scroll_width)

        self.cb_price_protect.toggled.connect(self._sync_jim_sub_controls)
        self.cb_real_photo_jim.toggled.connect(self._sync_jim_sub_controls)
        self._sync_jim_sub_controls()

    def _sync_jim_sub_controls(self) -> None:
        self.cb_jim_price_full.setEnabled(self.cb_price_protect.isChecked())
        self.cb_jim_photo_full.setEnabled(self.cb_real_photo_jim.isChecked())

    def _sync_inner_scroll_width(self) -> None:
        """让滚动区内层宽度等于视口；与自身宽度取 max，避免 QStacked 首次展示视口极窄。"""
        vp = self._scroll.viewport().width()
        w = max(vp, self._scroll.width(), self.width())
        if w > 0:
            self._inner_page.setFixedWidth(w)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._sync_inner_scroll_width()

    def _style_handoff_button(self) -> None:
        self.btn_handoff.setStyleSheet(JIM_BTN_HANDOFF)

    @property
    def log(self) -> QPlainTextEdit:
        return self.activity_log

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._refresh_profile_label()
        self.refresh_startup_hints()

    def _refresh_profile_label(self) -> None:
        pn = profile_name()
        if pn:
            self.lbl_profile_tag.setText(
                f"<p><b>当前运行实例：</b>{pn}（本实例目录下 configs / data 与其他窗口隔离）</p>"
            )
        else:
            self.lbl_profile_tag.setText(
                "<p><small>默认实例。需要三店同时托管时，请另开两个窗口并加 "
                "<code>--profile</code>，详见「使用指南」中的多店铺并排。</small></p>"
            )

    def _show_user_guide(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("使用指南")
        dlg.resize(640, 520)
        v = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(False)
        browser.setHtml(dashboard_user_guide_html())
        v.addWidget(browser)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_close)
        v.addLayout(row)
        dlg.exec()

    def _on_open_chat_log_excel(self) -> None:
        try:
            open_panse_chat_log_in_excel()
        except Exception as e:
            QMessageBox.warning(
                self,
                "无法打开日志",
                f"请确认本机已安装 Microsoft Excel 或已关联 .csv 默认程序。\n详情：{e!s}",
            )

    def refresh_startup_hints(self) -> None:
        """仅提示当前缩放比例，不阻止启动。"""
        pct = read_windows_dpi_percent()
        self.lbl_dpi_status.setText(
            "<p style='margin:4px 0'>"
            f"<b>当前显示缩放约 {pct}%</b>，可直接点下方「启动」。"
            "若发现点击错位，可将缩放改为 100% 或在店铺配置里重新取点。"
            "</p>"
        )
        self._sync_master_button_enabled()

    def _sync_master_button_enabled(self) -> None:
        """不因 DPI 禁用启动按钮（由运行状态等其它逻辑控制时可扩展）。"""
        self.btn_master.setEnabled(True)
        self.btn_master.setToolTip("")

    def set_master_running(self, running: bool) -> None:
        """更新主按钮文案与样式（运行中 = 红色停止托管）。"""
        self._master_running = running
        if running:
            self.btn_master.setText("停止托管")
            self.btn_master.setStyleSheet(JIM_BTN_STOP)
        else:
            self.btn_master.setText("启动全自动客服系统")
            self.btn_master.setStyleSheet(JIM_BTN_START)
        self._sync_master_button_enabled()
        self.btn_shadow_observe.setEnabled(running)
        if not running:
            self.set_shadow_observation(False)

    def set_pause_controls(
        self,
        *,
        brain_running: bool,
        auto_paused: bool,
        shadow_on: bool,
    ) -> None:
        """由 WorkbenchShell 根据会话状态刷新暂停/继续按钮。"""
        if shadow_on:
            self.btn_pause_auto.setEnabled(False)
            self.btn_pause_auto.setText("暂停自动回复")
            self.btn_pause_auto.setToolTip(
                "当前为人工观测模式（已暂停自动回复）。请先退出观测模式后再用此按钮。"
            )
            return
        if not brain_running:
            self.btn_pause_auto.setEnabled(False)
            self.btn_pause_auto.setText("暂停自动回复")
            self.btn_pause_auto.setToolTip("请先点击「启动全自动客服系统」。")
            return
        self.btn_pause_auto.setEnabled(True)
        if auto_paused:
            self.btn_pause_auto.setText("继续自动回复")
            self.btn_pause_auto.setToolTip(
                "恢复系统自动生成并发送回复（千牛保持登录，Core 不重启）。"
            )
        else:
            self.btn_pause_auto.setText("暂停自动回复")
            self.btn_pause_auto.setToolTip(
                "仅暂停 AI 自动发话与 Brain 监听周期，适合您临时在千牛手动回复。"
            )

    def set_shadow_observation(self, active: bool) -> None:
        self.btn_shadow_observe.blockSignals(True)
        self.btn_shadow_observe.setChecked(bool(active))
        self.btn_shadow_observe.blockSignals(False)
        self._sync_shadow_observe_label(bool(active))

    def _sync_shadow_observe_label(self, checked: bool) -> None:
        self.btn_shadow_observe.setText(
            "退出人工观测模式（恢复自动回复）"
            if checked
            else "进入人工观测模式（暂停自动回复）"
        )

    # --- 兜底话术 ---

    def _refresh_fallback_preview(self) -> None:
        """刷新兜底话术预览标签。"""
        try:
            from apps.core.strategy.copy import load_fallback_phrases
            fb = load_fallback_phrases()
        except Exception:
            self._fb_preview.setText("<i>（无法读取兜底话术配置）</i>")
            return
        self._fb_preview.setText(
            f"<p style='line-height:1.6'>"
            f"<b>欢迎语：</b>{_esc(fb.welcome_greeting)}<br>"
            f"<b>转人工安抚：</b>{_esc(fb.handoff_soothe)}<br>"
            f"<b>补单回复：</b>{_esc(fb.replenish_reply)}"
            f"</p>"
        )

    def _open_fallback_editor(self) -> None:
        """打开兜底话术编辑弹窗。"""
        dlg = FallbackPhrasesDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_fallback_preview()

    def _open_chitchat_editor(self) -> None:
        """打开闲聊话术编辑弹窗。"""
        dlg = ChitChatDialog(self)
        dlg.exec()


def _esc(text: str) -> str:
    """简易 HTML 转义（避免话术中的 < > & 破坏富文本）。"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class FallbackPhrasesDialog(QDialog):
    """通用兜底话术编辑弹窗。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑通用兜底话术")
        self.resize(560, 300)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "<small>修改后点「保存」立即生效（写入 configs/query_rewrite.yaml）。"
            "清空某项则恢复系统默认值。</small>"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()

        self.edit_welcome = QLineEdit()
        self.edit_welcome.setPlaceholderText("系统默认：您好，在的呢～")
        form.addRow("欢迎语（新客首句问候）", self.edit_welcome)

        self.edit_handoff = QLineEdit()
        self.edit_handoff.setPlaceholderText("系统默认：我帮您确认看看，您稍等呢~")
        form.addRow("转人工安抚（Jim 介入时发送）", self.edit_handoff)

        self.edit_replenish = QLineEdit()
        self.edit_replenish.setPlaceholderText("系统默认：老客户回购这边给您优先安排～…")
        form.addRow("补单回复（老客回购自动回复）", self.edit_replenish)

        # v1.3.94 新增：买家分享商品卡片时的追问话术
        self.edit_card_inquiry = QLineEdit()
        self.edit_card_inquiry.setPlaceholderText("系统默认：您是想什么要什么规格呢？")
        form.addRow("商品卡片追问（买家分享商品链接后追问）", self.edit_card_inquiry)

        # v1.3.94 新增：批量安抚话术
        self.edit_batch_soothe = QLineEdit()
        self.edit_batch_soothe.setPlaceholderText("系统默认：亲，稍等一下哦～")
        form.addRow("批量安抚（其他未读客户的稍等话术）", self.edit_batch_soothe)

        layout.addLayout(form)

        row_btn = QHBoxLayout()
        row_btn.addStretch(1)
        btn_save = QPushButton("保存")
        btn_save.setMinimumWidth(90)
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton("取消")
        btn_cancel.setMinimumWidth(90)
        btn_cancel.clicked.connect(self.reject)
        row_btn.addWidget(btn_save)
        row_btn.addWidget(btn_cancel)
        layout.addLayout(row_btn)

        self._load_current()

    def _load_current(self) -> None:
        try:
            from apps.core.strategy.copy import load_fallback_phrases
            fb = load_fallback_phrases()
            self.edit_welcome.setText(fb.welcome_greeting)
            self.edit_handoff.setText(fb.handoff_soothe)
            self.edit_replenish.setText(fb.replenish_reply)
            self.edit_card_inquiry.setText(fb.product_card_inquiry)
            self.edit_batch_soothe.setText(fb.batch_soothe)
        except Exception:
            pass

    def _on_save(self) -> None:
        try:
            from apps.core.strategy.copy import FallbackPhrases, save_fallback_phrases
            phrases = FallbackPhrases(
                welcome_greeting=self.edit_welcome.text().strip(),
                handoff_soothe=self.edit_handoff.text().strip(),
                replenish_reply=self.edit_replenish.text().strip(),
                product_card_inquiry=self.edit_card_inquiry.text().strip(),
                batch_soothe=self.edit_batch_soothe.text().strip(),
            )
            save_fallback_phrases(phrases)
            QMessageBox.information(self, "已保存", "兜底话术已保存，立即生效。")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入配置文件失败：{e!s}")


# 闲聊场景中文标签映射
_CHITCHAT_LABELS: dict[str, str] = {
    "thanks": "感谢",
    "ack": "确认/收到",
    "greeting": "问候/打招呼",
    "farewell": "告别",
    "polite_short": "客气短句",
    "emoji": "表情回复",
    "other": "其他闲聊",
}


class ChitChatDialog(QDialog):
    """闲聊话术编辑弹窗：每个场景一行，逗号分隔多条随机回复。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑闲聊话术")
        self.resize(600, 420)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "<small>每个场景可填多条回复，用<b>中文逗号「，」或英文逗号「,」</b>分隔，"
            "系统会随机选一条发送。清空则恢复系统默认值。</small>"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._editors: dict[str, QLineEdit] = {}
        for key, label in _CHITCHAT_LABELS.items():
            ed = QLineEdit()
            ed.setPlaceholderText(f"如：回复A，回复B，回复C")
            self._editors[key] = ed
            form.addRow(label, ed)
        layout.addLayout(form)

        row_btn = QHBoxLayout()
        row_btn.addStretch(1)
        btn_save = QPushButton("保存")
        btn_save.setMinimumWidth(90)
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton("取消")
        btn_cancel.setMinimumWidth(90)
        btn_cancel.clicked.connect(self.reject)
        row_btn.addWidget(btn_save)
        row_btn.addWidget(btn_cancel)
        layout.addLayout(row_btn)

        self._load_current()

    def _json_path(self) -> Path:
        from apps.core.runtime_paths import configs_dir
        return configs_dir() / "panse_chit_chat.json"

    def _load_current(self) -> None:
        import json
        try:
            data = json.loads(self._json_path().read_text(encoding="utf-8"))
        except Exception:
            return
        cats = data.get("categories", {})
        for key, ed in self._editors.items():
            replies = cats.get(key, {}).get("replies", [])
            ed.setText("，".join(replies))

    def _on_save(self) -> None:
        import json
        cats: dict = {}
        for key, ed in self._editors.items():
            raw = ed.text().strip()
            if raw:
                # 同时支持中英文逗号分隔
                parts = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
            else:
                parts = []
            if parts:
                cats[key] = {"replies": parts}
        # 空场景使用内置默认值
        defaults = {
            "thanks": ["老板客气啦，有需要随时喊我～", "应该的，您有问题随时问～"],
            "ack": ["嗯嗯好的～", "收到～"],
            "greeting": ["在的呢～", "您好，我在的～"],
            "farewell": ["好的，您慢走～", "嗯嗯，有需要再来～"],
            "polite_short": ["好哒～", "嗯嗯～"],
            "emoji": ["\U0001f60a", "\U0001f44c"],
            "other": ["嗯嗯～", "在的呢～"],
        }
        for key in _CHITCHAT_LABELS:
            if key not in cats:
                cats[key] = {"replies": defaults.get(key, ["嗯嗯～"])}
        out = {"categories": cats}
        try:
            self._json_path().write_text(
                json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            QMessageBox.information(self, "已保存", "闲聊话术已保存，立即生效。")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入配置文件失败：{e!s}")

