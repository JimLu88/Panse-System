from __future__ import annotations

import shlex
import sys
from PyQt6.QtCore import Qt, QProcess, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apps.core.ai.llm_client import litellm_completion_text, resolve_litellm_api_key
from apps.core.configs.base_settings import (
    BaseSettings,
    default_base_settings_path,
    load_base_settings,
    save_base_settings,
)


# LiteLLM：provider/模型名；走 OpenAI 兼容中转时，HTTP 里 model 一般为「斜杠后」一段。
# 同一中转若只提供 OpenAI 兼容 /v1/chat/completions，Claude 须用 openai/claude-…，
# 不要用 anthropic/claude-…（否则会走 Messages API，网关常返回 HTML 而非 JSON）。
_FRONTIER_MODELS = [
    "openai/gpt-4o-mini",
    "openai/deepseek-v4-flash",
    "openai/qwen3.6-plus",
    "openai/claude-sonnet-4-6-thinking",
]

_DEEP_MODELS = [
    "openai/claude-sonnet-4-6-thinking",
    "openai/gpt-4o",
    "openai/qwen3.6-plus",
    "openai/deepseek-v4-flash",
]

# 向量嵌入模型：3-large=3072 维（中文更准，默认）/ 3-small=1536 维（更省）。
# ⚠ 切换后必须重建向量库（维度变化），否则查询向量与库维度不一致会导致召回失效。
_EMBED_MODELS = [
    "text-embedding-3-large",
    "text-embedding-3-small",
]


def _all_presets_ordered_unique() -> list[str]:
    """前台 + 深度预设去重（顺序：先前排深度）。用于一键测全、对话里选模型。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _FRONTIER_MODELS + _DEEP_MODELS:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


class _LLMChatTestDialog(QDialog):
    """单次对话测试（非仅 ping），使用当前表单密钥与 LiteLLM_API_Base。"""

    def __init__(self, parent: QWidget | None, gather_settings) -> None:
        super().__init__(parent)
        self._gather_settings = gather_settings
        self.setWindowTitle("模型对话测试")
        self.resize(640, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("模型（可与下拉预设不一致，支持手改）"))
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.addItems(_all_presets_ordered_unique())
        lay.addWidget(self.combo_model)
        lay.addWidget(QLabel("用户消息"))
        self.ed_user = QPlainTextEdit()
        self.ed_user.setPlaceholderText("输入要向模型说的话…")
        self.ed_user.setMinimumHeight(100)
        lay.addWidget(self.ed_user)
        lay.addWidget(QLabel("助手回复"))
        self.ed_reply = QPlainTextEdit()
        self.ed_reply.setReadOnly(True)
        self.ed_reply.setMinimumHeight(140)
        lay.addWidget(self.ed_reply)
        row = QHBoxLayout()
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self._on_send)
        row.addWidget(self.btn_send)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_send(self) -> None:
        model = self.combo_model.currentText().strip()
        user_txt = self.ed_user.toPlainText().strip()
        if not model:
            QMessageBox.warning(self, "对话测试", "请选择或填写模型 ID。")
            return
        if not user_txt:
            QMessageBox.warning(self, "对话测试", "请输入用户消息。")
            return
        st = self._gather_settings()
        if not resolve_litellm_api_key(st, model):
            QMessageBox.warning(
                self,
                "对话测试",
                f"未配置与「{model}」对应的 API 密钥，请在设置中填写后重试。",
            )
            return
        self.ed_reply.setPlainText("请求中…")
        QApplication.processEvents()
        try:
            raw = litellm_completion_text(
                settings=st,
                model=model,
                system="你是助手，请直接回答用户问题，简明扼要。",
                user=user_txt,
                max_tokens=2048,
                temperature=0.3,
            )
            self.ed_reply.setPlainText(raw)
        except Exception as e:
            self.ed_reply.setPlainText(f"[错误]\n{e}")


class SettingsView(QWidget):
    """设置中心：全局密钥 + 前台/深度双线模型（LiteLLM）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
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

        layout.addWidget(QLabel("<h2>设置中心</h2>"))

        self.lbl_path = QLabel()
        self.lbl_path.setWordWrap(True)
        layout.addWidget(self.lbl_path)

        g_keys = QGroupBox("全局 API 密钥管理（多供应商 · 按需填写）")
        fk = QFormLayout(g_keys)
        hint_keys = QLabel(
            "<small>至少填写<strong>下方「业务配置」所选前台 / 深度模型</strong>对应供应商的密钥；"
            "同一中转钥可复制到多格。模型为 LiteLLM 的 <code>provider/名称</code>，"
            "须与中转站支持的模型 ID 一致（如 <code>openai/deepseek-v4-flash</code>，"
            "勿填网关不识别的通用名如 <code>deepseek-chat</code>）。"
            "<br/>将 OpenAI 官方 <code>https://api.openai.com</code> 换为 "
            "<code>https://ai.t8star.cn</code> 时，在「LiteLLM_API_Base」填 <code>https://ai.t8star.cn/v1</code>；"
            "留空则走各厂商默认官方地址。"
            "<br/>全部模型均走同一 OpenAI 兼容中转、且模型前缀均为 <code>openai/</code> 时，一般<strong>只填 OpenAI_API_Key</strong> 即可。"
            "</small>"
        )
        hint_keys.setWordWrap(True)
        fk.addRow(hint_keys)
        self.ed_deepseek = QLineEdit()
        self.ed_deepseek.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_deepseek.setPlaceholderText("DeepSeek API Key")
        self.ed_dashscope = QLineEdit()
        self.ed_dashscope.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_dashscope.setPlaceholderText("阿里云 DashScope（通义千问）API Key")
        self.ed_openai = QLineEdit()
        self.ed_openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_openai.setPlaceholderText("OpenAI API Key")
        self.ed_anthropic = QLineEdit()
        self.ed_anthropic.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_anthropic.setPlaceholderText("Anthropic API Key")
        self.ed_gemini = QLineEdit()
        self.ed_gemini.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_gemini.setPlaceholderText("Google Gemini API Key")
        self.ed_llm_api_base = QLineEdit()
        self.ed_llm_api_base.setPlaceholderText("例如 https://ai.t8star.cn/v1（与官方 OpenAI base 互换）")
        fk.addRow("DeepSeek_API_Key", self.ed_deepseek)
        fk.addRow("DashScope_API_Key（阿里通义）", self.ed_dashscope)
        fk.addRow("OpenAI_API_Key", self.ed_openai)
        fk.addRow("Anthropic_API_Key", self.ed_anthropic)
        fk.addRow("Gemini_API_Key", self.ed_gemini)
        fk.addRow("LiteLLM_API_Base（中转，可选）", self.ed_llm_api_base)
        layout.addWidget(g_keys)

        g_biz = QGroupBox("业务配置（双线解耦）")
        fb = QFormLayout(g_biz)
        self.combo_front = QComboBox()
        self.combo_front.setEditable(True)
        self.combo_front.addItems(_FRONTIER_MODELS)
        self.combo_deep = QComboBox()
        self.combo_deep.setEditable(True)
        self.combo_deep.addItems(_DEEP_MODELS)
        self.combo_embedding = QComboBox()
        self.combo_embedding.setEditable(False)
        self.combo_embedding.addItems(_EMBED_MODELS)
        fb.addRow("前台实时客服模型（性价比 · 仅格式化 RAG）", self.combo_front)
        fb.addRow("AI 陪伴与深度分析模型（推理 · 报表/话术整理）", self.combo_deep)
        fb.addRow("向量嵌入模型（3-large=3072维默认 · 切换后须重建向量库）", self.combo_embedding)
        self.chk_gemini_tools = QCheckBox(
            "openai/gemini-* 请求附带 googleSearch 工具（网关文档要求 tools 时勾选）"
        )
        self.chk_gemini_tools.setChecked(True)
        fb.addRow("", self.chk_gemini_tools)
        layout.addWidget(
            QLabel(
                "<small>"
                "前台模型：千牛自动回复专用，须输出 JSON 短句；不得脱离知识库编造。"
                "<br/>深度模型：异步任务专用（Excel 话术整理、陪伴报表等），与前台队列无关。"
                "<br/>模型 ID 使用 LiteLLM 格式；OpenAI 兼容中转下 HTTP 的 model 多为斜杠后一段（见预设）。"
                "</small>"
            )
        )
        layout.addWidget(g_biz)

        g_kb = QGroupBox("话术贴近度")
        fkb = QFormLayout(g_kb)
        self.slider_kb_adherence = QSlider(Qt.Orientation.Horizontal)
        self.slider_kb_adherence.setRange(50, 100)
        self.slider_kb_adherence.setValue(90)
        self.slider_kb_adherence.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_kb_adherence.setTickInterval(10)
        self.slider_kb_adherence.setSingleStep(5)
        self.lbl_kb_adherence_val = QLabel("90%")
        self.slider_kb_adherence.valueChanged.connect(
            lambda v: self.lbl_kb_adherence_val.setText(f"{v}%")
        )
        row_slider = QHBoxLayout()
        row_slider.addWidget(self.slider_kb_adherence, 1)
        row_slider.addWidget(self.lbl_kb_adherence_val)
        _wrap_slider = QWidget()
        _wrap_slider.setLayout(row_slider)
        fkb.addRow("贴近度", _wrap_slider)
        _hint_kb = QLabel(
            "<small>100% = 严格复述知识库原文，不允许自由发挥；"
            "50% = 可适度调整措辞，但仍不脱离知识库事实。"
            "<br/>建议 85–95%。数值越高，回复越贴近你录入的话术原文。</small>"
        )
        _hint_kb.setWordWrap(True)
        fkb.addRow(_hint_kb)
        layout.addWidget(g_kb)

        row_test = QHBoxLayout()
        btn_test_all = QPushButton("一键测试全部预设模型")
        btn_test_all.setToolTip(
            "依次对前台+深度预设中的每个模型跑连通性（含通用路径与深度路径）。"
        )
        btn_test_all.clicked.connect(self._test_all_preset_models)
        btn_chat = QPushButton("对话测试…")
        btn_chat.setToolTip("打开窗口，与当前密钥下任选模型进行一轮真实对话。")
        btn_chat.clicked.connect(self._open_chat_test_dialog)
        row_test.addWidget(btn_test_all)
        row_test.addWidget(btn_chat)
        row_test.addStretch(1)
        layout.addLayout(row_test)

        g_audio = QGroupBox("听觉流水线（与控制台「启动听觉流水线」共用）")
        fa = QFormLayout(g_audio)
        self.ed_audio_exe = QLineEdit()
        self.ed_audio_exe.setPlaceholderText("例如 AliWorkbench.exe")

        row_exe = QHBoxLayout()
        row_exe.addWidget(self.ed_audio_exe, 1)
        self.btn_search_qn = QPushButton("搜索千牛进程名…")
        self.btn_search_qn.setToolTip(
            "列出当前所有名字包含 workbench/qianniu/千牛 的进程，挑一个填回去。"
            "防止你的千牛实际叫 TaobaoWorkbench.exe / AliWorkbench64.exe 等。"
        )
        self.btn_search_qn.clicked.connect(self._search_qianniu_processes)
        row_exe.addWidget(self.btn_search_qn)
        _wrap_exe = QWidget()
        _wrap_exe.setLayout(row_exe)
        fa.addRow("监听进程名", _wrap_exe)

        self.chk_audio_gate = QCheckBox(
            "仅当千牛进程在运行时才响应音量触发（推荐；关闭则任意系统声音都会跑一轮会话检查）"
        )
        self.chk_audio_gate.setChecked(True)
        fa.addRow(self.chk_audio_gate)

        self.spin_audio_threshold = QDoubleSpinBox()
        self.spin_audio_threshold.setDecimals(3)
        self.spin_audio_threshold.setRange(0.001, 0.5)
        self.spin_audio_threshold.setSingleStep(0.005)
        self.spin_audio_threshold.setValue(0.02)
        self.spin_audio_threshold.setToolTip(
            "全系统混音峰值高于该值才触发接待；默认 0.02。"
            "若实时监视显示叮咚时峰值只有 0.005~0.01，把这里调到该数值的 0.6 倍。"
        )
        fa.addRow("叮咚触发灵敏度（peak ≥）", self.spin_audio_threshold)

        self.spin_audio_poll = QDoubleSpinBox()
        self.spin_audio_poll.setDecimals(2)
        self.spin_audio_poll.setRange(0.02, 1.0)
        self.spin_audio_poll.setSingleStep(0.02)
        self.spin_audio_poll.setValue(0.08)
        self.spin_audio_poll.setSuffix(" 秒")
        self.spin_audio_poll.setToolTip(
            "峰值轮询间隔；叮咚很短时调小到 0.04 秒以减少漏采。"
        )
        fa.addRow("峰值轮询间隔", self.spin_audio_poll)

        self.btn_audio_probe = QPushButton("实时峰值监视 10 秒（逐会话）…")
        self.btn_audio_probe.setToolTip(
            "点击后打开一个窗口，每 100ms 列出当前所有 WASAPI 会话的峰值与对应进程。"
            "让千牛响一次叮咚，就能直接看到走的是哪条会话、峰值多少。"
        )
        self.btn_audio_probe.clicked.connect(self._open_audio_probe_dialog)
        fa.addRow(self.btn_audio_probe)

        self.chk_visual_sentry = QCheckBox(
            "启用视觉哨兵：每 N 秒主动扫左侧会话列表 ROI（不依赖声音，强烈建议开启）"
        )
        self.chk_visual_sentry.setChecked(True)
        fa.addRow(self.chk_visual_sentry)
        self.spin_visual_interval = QSpinBox()
        self.spin_visual_interval.setRange(2, 60)
        self.spin_visual_interval.setSuffix(" 秒")
        self.spin_visual_interval.setValue(4)
        fa.addRow("视觉哨兵间隔", self.spin_visual_interval)

        self.spin_capture_delay = QDoubleSpinBox()
        self.spin_capture_delay.setDecimals(1)
        self.spin_capture_delay.setRange(0.0, 10.0)
        self.spin_capture_delay.setSingleStep(0.1)
        self.spin_capture_delay.setSuffix(" 秒")
        self.spin_capture_delay.setValue(1.5)
        self.spin_capture_delay.setToolTip(
            "窗口切到前台后等多少秒再截图（让聊天区有时间渲染）。"
            "切换会话后还会额外等待 query_rewrite 中的 post_switch_extra_delay_s。"
            "若 OCR 总是识别不到内容，建议 1.5~2.5 秒。"
        )
        fa.addRow("截图前等待时间", self.spin_capture_delay)

        self.spin_sweep = QSpinBox()
        self.spin_sweep.setRange(1, 1440)
        self.spin_sweep.setSuffix(" 分钟")
        self.spin_sweep.setValue(2)
        fa.addRow("兜底扫屏间隔", self.spin_sweep)
        layout.addWidget(g_audio)

        g_push = QGroupBox("微信 / 推送（Server酱 / PushPlus / 企微 / 宿主机 HTTP）")
        f2 = QFormLayout(g_push)
        self.ed_serverchan = QLineEdit()
        self.ed_serverchan.setPlaceholderText("Server酱 SendKey")
        self.ed_pushplus = QLineEdit()
        self.ed_pushplus.setPlaceholderText("PushPlus Token")
        self.ed_wecom = QLineEdit()
        self.ed_wecom.setPlaceholderText("企业微信机器人 Webhook URL")
        self.ed_push_host = QLineEdit()
        self.ed_push_host.setPlaceholderText(
            "可选：宿主机强提醒 HTTP，如 http://192.168.1.2:9777/ping（见 scripts/host_strong_alert_listener.py）"
        )
        btn_test_push = QPushButton("发送测试推送到手机")
        btn_test_push.clicked.connect(self._test_push_channels)
        f2.addRow("Server酱", self.ed_serverchan)
        f2.addRow("PushPlus", self.ed_pushplus)
        f2.addRow("企微 Webhook", self.ed_wecom)
        f2.addRow("宿主机 HTTP（强提醒等）", self.ed_push_host)
        f2.addRow(btn_test_push)
        layout.addWidget(g_push)

        g_vm = QGroupBox("虚拟机（仅在主机上启动虚拟机软件）")
        fv = QFormLayout(g_vm)
        self.ed_vm_program = QLineEdit()
        self.ed_vm_program.setPlaceholderText(
            r'例如 C:\Program Files\Oracle\VirtualBox\VBoxManage.exe 或 vmrun.exe 完整路径'
        )
        self.ed_vm_args = QLineEdit()
        self.ed_vm_args.setPlaceholderText(
            '参数示例：startvm "虚拟机名称" --type gui（按你所装软件填写）'
        )
        fv.addRow("主机程序", self.ed_vm_program)
        fv.addRow("命令行参数", self.ed_vm_args)
        row_vm_w = QWidget()
        row_vm = QHBoxLayout(row_vm_w)
        self.btn_vm_launch = QPushButton("一键启动虚拟机（主机侧）")
        self.btn_vm_launch.clicked.connect(self._launch_vm_host)
        self.btn_vm_copy_guest = QPushButton("复制客户机内多开示例")
        self.btn_vm_copy_guest.clicked.connect(self._copy_guest_launch_hint)
        row_vm.addWidget(self.btn_vm_launch)
        row_vm.addWidget(self.btn_vm_copy_guest)
        fv.addRow(row_vm_w)
        layout.addWidget(g_vm)
        _lab_vm = QLabel(
            "<small>说明：按钮只在<strong>你这台电脑（主机）</strong>上拉起虚拟机进程；"
            "请在<strong>虚拟机里面</strong>再安装本软件与千牛，并为每个虚拟机/店铺使用不同的 "
            "<code>--profile</code>，与工作台上「多店铺并排」说明一致。</small>"
        )
        _lab_vm.setWordWrap(True)
        _lab_vm.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(_lab_vm)

        # ───── v1.5.x：反检测 · 拟人化 ──────────────────────────────────
        g_humanize = QGroupBox("反检测 · 拟人化（v1.5.x，默认全部不启用以保持原行为）")
        fh = QFormLayout(g_humanize)

        self.chk_mouse_jitter = QCheckBox(
            "鼠标点击位置 ±3px 正态抖动（无副作用，建议默认开启）"
        )
        fh.addRow(self.chk_mouse_jitter)

        self.chk_real_typing = QCheckBox(
            "真实打字模拟：逐字 SendInput + 偶尔退格重打（替代 paste+Ctrl+V）"
        )
        self.chk_real_typing.setToolTip(
            "开启后发消息会逐字输入，看起来像真人手打。\n"
            "副作用：大段文字会变慢；错别字率 2.5% 由 yaml 调"
        )
        fh.addRow(self.chk_real_typing)

        self.chk_reply_timing = QCheckBox(
            "回复延迟拟人化：每条 8-20 秒 + 长消息加权（每 200 字加 30 秒）"
        )
        self.chk_reply_timing.setToolTip(
            "开启后生成回复 → sleep 拟人化时长 → 才发出；\n"
            "对方观感：你像真人一样思考几秒再回复"
        )
        fh.addRow(self.chk_reply_timing)

        self.chk_quiet_hours = QCheckBox(
            "深夜降级：凌晨 1-7 点自动跳过回复（需先勾选「回复延迟拟人化」）"
        )
        fh.addRow(self.chk_quiet_hours)

        self.chk_pin_window = QCheckBox(
            "窗口锁定：每次置前后把千牛拖回固定位置/尺寸（关键是「不变」，不是「大」）"
        )
        self.chk_pin_window.setToolTip(
            "原理：OCR 是按像素位置识字的，千牛尺寸一变，全部坐标就要重校。\n"
            "勾选 + 点「📐 自动取当前位置」即可——按你日常用的尺寸来，无需特意调大。"
        )
        self.chk_pin_window.toggled.connect(self._on_pin_toggle)
        fh.addRow(self.chk_pin_window)

        # ── 窗口锁定的 6 个参数（chk_pin_window 子控件，勾选才启用）──
        self.spin_pin_x = QSpinBox()
        self.spin_pin_x.setRange(0, 9999)
        self.spin_pin_x.setSuffix(" px")
        self.spin_pin_x.setToolTip("千牛主窗口左上角 X 坐标（屏幕绝对像素）")
        fh.addRow("  └─ 左上角 X", self.spin_pin_x)

        self.spin_pin_y = QSpinBox()
        self.spin_pin_y.setRange(0, 9999)
        self.spin_pin_y.setSuffix(" px")
        self.spin_pin_y.setToolTip("千牛主窗口左上角 Y 坐标（屏幕绝对像素）")
        fh.addRow("  └─ 左上角 Y", self.spin_pin_y)

        self.spin_pin_width = QSpinBox()
        self.spin_pin_width.setRange(200, 9999)
        self.spin_pin_width.setSuffix(" px")
        self.spin_pin_width.setToolTip(
            "千牛主窗口宽度。\n"
            "关键不是「大」，而是「稳定」——每次都是同一个值，OCR 才稳。\n"
            "你日常用什么尺寸就填什么；最低 500 px 即可。"
        )
        fh.addRow("  └─ 宽度", self.spin_pin_width)

        self.spin_pin_height = QSpinBox()
        self.spin_pin_height.setRange(200, 9999)
        self.spin_pin_height.setSuffix(" px")
        self.spin_pin_height.setToolTip(
            "千牛主窗口高度。同上：日常用多大就填多大，最低 350 px 即可。"
        )
        fh.addRow("  └─ 高度", self.spin_pin_height)

        self.spin_pin_drift = QSpinBox()
        self.spin_pin_drift.setRange(1, 200)
        self.spin_pin_drift.setSuffix(" px")
        self.spin_pin_drift.setToolTip(
            "当前位置/尺寸偏离配置 > 此值时才矫正；避免微小晃动也触发"
        )
        fh.addRow("  └─ 漂移容忍", self.spin_pin_drift)

        self.chk_pin_dpi_warn_only = QCheckBox(
            "DPI 非 100% 时仅警告（不放弃锁定，仍尝试拖窗）"
        )
        self.chk_pin_dpi_warn_only.setToolTip(
            "勾选 = 即便系统缩放是 125%/150%，依然按配置坐标拖（不一定准）\n"
            "不勾选 = DPI != 100% 时放弃锁定，避免坐标错位\n"
            "建议：先在系统设置→显示→缩放改 100%，再开本功能"
        )
        fh.addRow(self.chk_pin_dpi_warn_only)

        self.btn_pin_autodetect = QPushButton("📐 自动取当前千牛窗口位置（推荐）")
        self.btn_pin_autodetect.setToolTip(
            "前提：千牛 App 已经打开。\n"
            "点这个按钮后，会读取千牛主窗口当前的左上角坐标 + 宽高，\n"
            "自动填到上面 4 个输入框，免得手数像素。"
        )
        self.btn_pin_autodetect.clicked.connect(self._on_pin_autodetect)
        fh.addRow(self.btn_pin_autodetect)

        self.btn_pin_test_apply = QPushButton("📌 立即应用一次锁定（测试用，不依赖接待启动）")
        self.btn_pin_test_apply.setToolTip(
            "把千牛立即拖到上方 4 个输入框设置的位置/尺寸。\n"
            "用途：验证锁定是否生效（窗口跳到你设的坐标 = 生效）。\n"
            "注意：用的是当前输入框的值，不一定要先保存 yaml。"
        )
        self.btn_pin_test_apply.clicked.connect(self._on_pin_test_apply)
        fh.addRow(self.btn_pin_test_apply)

        self.chk_idle_action = QCheckBox(
            "闲时无意义动作：每小时随机切到订单页/商品页瞅一眼（需 YAML 配坐标）"
        )
        fh.addRow(self.chk_idle_action)

        self.combo_text_extract = QComboBox()
        self.combo_text_extract.addItems(["ocr", "clipboard", "hybrid"])
        self.combo_text_extract.setToolTip(
            "ocr=PaddleOCR（默认，0 改动）\n"
            "clipboard=Ctrl+A+Ctrl+C 抓剪贴板（更准更快，需聊天区可点击）\n"
            "hybrid=优先 clipboard 失败回退 ocr"
        )
        fh.addRow("文本抽取模式", self.combo_text_extract)

        layout.addWidget(g_humanize)

        _lab_humanize = QLabel(
            "<small>说明：以上是常用开关 + 窗口锁定的位置参数。<br>"
            "更细的参数（抖动半径、错别字率、回复延迟范围、深夜时段等）"
            "仍在 <code>configs/base_settings.yaml</code> 里调，按各 <code>humanize_*</code> 字段名查找即可。<br>"
            "改完点最下「保存到 base_settings.yaml」→ 下一次置前/发送即生效。</small>"
        )
        _lab_humanize.setWordWrap(True)
        layout.addWidget(_lab_humanize)

        # -- v1.6.1 修复档案（自动记录 + 一键导出复制给开发者）--
        g_archive = QGroupBox("修复档案（问题自动记录 · 复制给开发者对比）")
        fa_lay = QVBoxLayout(g_archive)
        fa_hint = QLabel(
            "程序运行时会<b>自动</b>记录每个已知问题出现的次数和版本（你无需手动操作）。"
            "点下面按钮把「问题出现历史 × 历次修复」对照表复制出来发给开发者，"
            "就能一眼看出同一个问题修了几次、为什么还没改好。"
        )
        fa_hint.setWordWrap(True)
        fa_lay.addWidget(fa_hint)
        fa_row = QHBoxLayout()
        self.btn_export_archive = QPushButton("bug历史+打开截图文件夹")
        self.btn_export_archive.setMinimumHeight(34)
        self.btn_export_archive.clicked.connect(self._export_fix_archive)
        fa_row.addWidget(self.btn_export_archive)
        self.btn_ai_rootcause = QPushButton("让 AI 分析根因")
        self.btn_ai_rootcause.setMinimumHeight(34)
        self.btn_ai_rootcause.clicked.connect(self._ai_root_cause)
        fa_row.addWidget(self.btn_ai_rootcause)
        fa_lay.addLayout(fa_row)
        layout.addWidget(g_archive)

        row_save = QHBoxLayout()
        self.btn_save = QPushButton("保存到 base_settings.yaml")
        self.btn_save.setMinimumHeight(36)
        self.btn_save.clicked.connect(self._save)
        row_save.addWidget(self.btn_save)
        row_save.addStretch(1)
        layout.addLayout(row_save)

        _lab_save_note = QLabel(
            "保存后下一轮 Brain 周期将使用新的前台模型与密钥；"
            "底层已默认启用 LiteLLM 缓存提示，无需额外勾选。"
        )
        _lab_save_note.setWordWrap(True)
        _lab_save_note.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(_lab_save_note)

        self._reload_path_label()
        self._load_from_disk()

        QTimer.singleShot(0, self._sync_inner_scroll_width)

    def _defer_sync_scroll_width(self) -> None:
        """首次显示后再算一次布局（避免仅依赖单次 resize）。"""
        self._sync_inner_scroll_width()
        QTimer.singleShot(0, self._sync_inner_scroll_width)
        QTimer.singleShot(50, self._sync_inner_scroll_width)

    def _sync_inner_scroll_width(self) -> None:
        """视口在 QStackedWidget 首次展示前常为 0 或极小；须与自身宽度取 max，否则会锁死成窄条。"""
        vp = self._scroll.viewport().width()
        w = max(vp, self._scroll.width(), self.width())
        if w > 0:
            self._inner_page.setFixedWidth(w)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._sync_inner_scroll_width()

    def _on_pin_toggle(self, checked: bool) -> None:
        """窗口锁定总开关切换 → 6 个子控件随之启用/灰掉。"""
        for w in (
            self.spin_pin_x, self.spin_pin_y,
            self.spin_pin_width, self.spin_pin_height,
            self.spin_pin_drift, self.chk_pin_dpi_warn_only,
            self.btn_pin_autodetect, self.btn_pin_test_apply,
        ):
            w.setEnabled(bool(checked))

    def _on_pin_autodetect(self) -> None:
        """读千牛主窗口当前位置/尺寸 → 合理性校验 → 自动填到 4 个 spin。

        合理性校验避免读到无效坐标：
          - 最小化 → GetWindowRect 返回 (-32000, -32000) 鬼坐标，先 SW_RESTORE 恢复
          - 最大化 → 坐标是全屏带阴影，不是用户想要的，要用户先取消最大化
          - 坐标超出主屏范围 → 多显示器场景，警告用户确认
        """
        try:
            import ctypes
            from apps.core.channels.qianniu.win_hwnd import (
                find_qianniu_main_hwnd_best_effort,
            )
            from apps.core.channels.qianniu.window_pin import get_window_rect
        except Exception as e:
            QMessageBox.warning(
                self, "导入失败",
                f"找不到窗口检测模块：{e!r}\n"
                "请确认 v1.5.x 模块已就位（apps/core/channels/qianniu/window_pin.py）。",
            )
            return

        hwnd = find_qianniu_main_hwnd_best_effort() or 0
        if hwnd <= 0:
            QMessageBox.information(
                self, "找不到千牛",
                "未找到千牛主窗口。请先在桌面把千牛 App 打开（任意账号都行），\n"
                "再回到这里点本按钮。",
            )
            return

        user32 = ctypes.windll.user32
        # ── 1. 最小化检查 → 自动恢复 ─────────────────────────────────
        if user32.IsIconic(hwnd):
            ret = QMessageBox.question(
                self, "千牛已最小化",
                "千牛当前处于「最小化」状态，直接读会得到 (-32000, -32000) 这种无效坐标。\n\n"
                "要先自动恢复千牛窗口再读取吗？\n"
                "（点「是」会调 ShowWindow(SW_RESTORE) 把它恢复到非最小化状态）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            SW_RESTORE = 9
            user32.ShowWindow(int(hwnd), SW_RESTORE)
            # 等一小段让 Windows 完成动画 + 重新计算坐标
            import time as _t
            _t.sleep(0.4)

        # ── 2. 最大化检查 → 提示用户先取消（不自动改避免改变用户预期）─
        if user32.IsZoomed(hwnd):
            QMessageBox.warning(
                self, "千牛已最大化",
                "千牛当前处于「最大化」状态，读到的坐标会是带阴影的全屏值"
                "（如 -8, -8, 1936, 1056），不是你想要的固定窗口大小。\n\n"
                "请先在千牛右上角点「向下还原」按钮取消最大化，\n"
                "把窗口手动拖/调整到你想要的位置和大小，再点本按钮。",
            )
            return

        # ── 3. 读取实际坐标 ──────────────────────────────────────────
        rect = get_window_rect(int(hwnd))
        if rect is None:
            QMessageBox.warning(
                self, "读取失败",
                f"找到千牛窗口（HWND={hwnd}）但读取位置失败。",
            )
            return

        # ── 4. 坐标合理性检查 ────────────────────────────────────────
        warnings = []
        if rect.x < -100 or rect.y < -100:
            warnings.append(
                f"X={rect.x} / Y={rect.y} 为大负数；可能是窗口被拖到主屏外（多显示器场景），\n"
                "或者最小化没恢复成功"
            )
        if rect.width < 500 or rect.height < 350:
            warnings.append(
                f"窗口尺寸偏小（{rect.width}×{rect.height}）；"
                "OCR 至少需要 500×350 才能稳定识别文字。\n"
                "  建议把千牛拖大一点再点本按钮。"
            )

        # 拿主屏尺寸做范围判断（SM_CXSCREEN=0, SM_CYSCREEN=1）
        screen_w = int(user32.GetSystemMetrics(0))
        screen_h = int(user32.GetSystemMetrics(1))
        if rect.x > screen_w or rect.y > screen_h:
            warnings.append(
                f"X/Y 超出主屏 ({screen_w}×{screen_h})；锁定后窗口可能在副屏或不可见区域"
            )

        # ── 5. 填回 UI ───────────────────────────────────────────────
        self.spin_pin_x.setValue(int(rect.x))
        self.spin_pin_y.setValue(int(rect.y))
        self.spin_pin_width.setValue(int(rect.width))
        self.spin_pin_height.setValue(int(rect.height))

        # ── 6. 显示结果 + 警告（如有）──────────────────────────────
        msg_lines = [
            f"千牛主窗口 HWND = {hwnd}",
            f"  X = {rect.x}    Y = {rect.y}",
            f"  宽 = {rect.width}  高 = {rect.height}",
            f"  主屏尺寸 = {screen_w} × {screen_h}",
        ]
        if warnings:
            msg_lines.append("")
            msg_lines.append("⚠ 警告：")
            for w in warnings:
                msg_lines.append(f"  • {w}")
            msg_lines.append("")
            msg_lines.append("建议：先把千牛手动拖到合理位置（如主屏左上角附近，1280×800），再点本按钮重取。")
            QMessageBox.warning(self, "已读取，但有异常", "\n".join(msg_lines))
        else:
            msg_lines.append("")
            msg_lines.append("记得点最下面「保存到 base_settings.yaml」让设置生效。")
            QMessageBox.information(self, "已自动取位置 ✓", "\n".join(msg_lines))

    def _on_pin_test_apply(self) -> None:
        """立即按当前 4 个 spin 的值把千牛拖过去（验证用，不依赖接待启动）。"""
        try:
            from apps.core.channels.qianniu.win_hwnd import (
                find_qianniu_main_hwnd_best_effort,
            )
            from apps.core.channels.qianniu.window_pin import (
                WindowRect, pin_window_to_rect,
            )
        except Exception as e:
            QMessageBox.warning(
                self, "导入失败",
                f"找不到窗口锁定模块：{e!r}",
            )
            return

        hwnd = find_qianniu_main_hwnd_best_effort() or 0
        if hwnd <= 0:
            QMessageBox.information(
                self, "找不到千牛",
                "请先在桌面打开千牛 App（任意账号都行），再点本按钮。",
            )
            return

        target = WindowRect(
            x=int(self.spin_pin_x.value()),
            y=int(self.spin_pin_y.value()),
            width=int(self.spin_pin_width.value()),
            height=int(self.spin_pin_height.value()),
        )
        # 用一个本地 log 收集器收消息（pin_window_to_rect 会调 log）
        log_lines: list[str] = []
        ok = pin_window_to_rect(int(hwnd), target, log=log_lines.append)

        if ok:
            QMessageBox.information(
                self, "✅ 锁定测试成功",
                f"千牛已被拖到目标位置：\n"
                f"  X = {target.x}    Y = {target.y}\n"
                f"  宽 = {target.width}  高 = {target.height}\n\n"
                f"日志：\n" + "\n".join(log_lines[-3:]),
            )
        else:
            QMessageBox.warning(
                self, "❌ 锁定测试失败",
                f"MoveWindow 失败。可能原因：\n"
                f"  • 千牛权限受限（试试以管理员身份运行本程序）\n"
                f"  • 坐标超出屏幕（X={target.x} 是否超过屏幕宽度？）\n\n"
                f"日志：\n" + "\n".join(log_lines[-5:]),
            )

    def _reload_path_label(self) -> None:
        self.lbl_path.setText(f"<small>配置文件：<code>{default_base_settings_path()}</code></small>")

    def _load_from_disk(self) -> None:
        st = load_base_settings()
        self.ed_deepseek.setText(st.deepseek_api_key)
        self.ed_dashscope.setText(st.dashscope_api_key)
        self.ed_openai.setText(st.openai_api_key)
        self.ed_anthropic.setText(st.anthropic_api_key)
        self.ed_gemini.setText(st.gemini_api_key)
        self.ed_llm_api_base.setText(st.llm_api_base)
        self.combo_front.setCurrentText(st.model_front_desk)
        self.combo_deep.setCurrentText(st.model_deep_analysis)
        self.combo_embedding.setCurrentText(
            getattr(st, "embedding_model", "") or "text-embedding-3-large")
        self.chk_gemini_tools.setChecked(getattr(st, "llm_gemini_attach_search_tool", True))
        self.ed_serverchan.setText(st.push_serverchan_sendkey)
        self.ed_pushplus.setText(st.push_pushplus_token)
        self.ed_wecom.setText(st.push_wecom_webhook)
        self.ed_push_host.setText(st.push_host_alert_url)
        self.ed_audio_exe.setText(st.audio_target_exe or "AliWorkbench.exe")
        self.chk_audio_gate.setChecked(getattr(st, "audio_gate_fire_only_when_qianniu_running", True))
        self.spin_audio_threshold.setValue(float(getattr(st, "audio_peak_threshold", 0.02)))
        self.spin_audio_poll.setValue(float(getattr(st, "audio_poll_interval_s", 0.08)))
        self.chk_visual_sentry.setChecked(bool(getattr(st, "visual_sentry_enabled", True)))
        self.spin_visual_interval.setValue(int(getattr(st, "visual_sentry_interval_s", 4)))
        self.spin_capture_delay.setValue(float(getattr(st, "capture_delay_s", 1.5)))
        self.spin_sweep.setValue(int(st.sweep_interval_minutes or 2))
        self.ed_vm_program.setText(st.vm_host_program)
        self.ed_vm_args.setText(st.vm_host_args)
        self.slider_kb_adherence.setValue(int(getattr(st, "kb_adherence_pct", 90)))

        # v1.5.x 拟人化开关
        self.chk_mouse_jitter.setChecked(bool(getattr(st, "humanize_mouse_jitter_enabled", True)))
        self.chk_real_typing.setChecked(bool(getattr(st, "humanize_real_typing_enabled", False)))
        self.chk_reply_timing.setChecked(bool(getattr(st, "humanize_reply_timing_enabled", False)))
        self.chk_quiet_hours.setChecked(bool(getattr(st, "humanize_quiet_hours_enabled", True)))
        self.chk_pin_window.setChecked(bool(getattr(st, "pin_window_enabled", False)))
        self.spin_pin_x.setValue(int(getattr(st, "pin_window_x", 100)))
        self.spin_pin_y.setValue(int(getattr(st, "pin_window_y", 100)))
        self.spin_pin_width.setValue(int(getattr(st, "pin_window_width", 1280)))
        self.spin_pin_height.setValue(int(getattr(st, "pin_window_height", 800)))
        self.spin_pin_drift.setValue(int(getattr(st, "pin_window_drift_tolerance_px", 10)))
        self.chk_pin_dpi_warn_only.setChecked(bool(getattr(st, "pin_window_dpi_warn_only", True)))
        self._on_pin_toggle(self.chk_pin_window.isChecked())  # 初始化子控件启用态
        self.chk_idle_action.setChecked(bool(getattr(st, "humanize_idle_action_enabled", False)))
        _mode = str(getattr(st, "text_extract_mode", "ocr")).strip().lower()
        if _mode not in ("ocr", "clipboard", "hybrid"):
            _mode = "ocr"
        self.combo_text_extract.setCurrentText(_mode)

    def _gather_settings(self) -> BaseSettings:
        prev = load_base_settings()
        deep_m = self.combo_deep.currentText().strip()
        legacy_anthropic_model = prev.anthropic_model
        if deep_m.startswith("anthropic/"):
            legacy_anthropic_model = deep_m.split("/", 1)[1]
        return BaseSettings(
            deepseek_api_key=self.ed_deepseek.text().strip(),
            dashscope_api_key=self.ed_dashscope.text().strip(),
            openai_api_key=self.ed_openai.text().strip(),
            anthropic_api_key=self.ed_anthropic.text().strip(),
            gemini_api_key=self.ed_gemini.text().strip(),
            llm_api_base=self.ed_llm_api_base.text().strip(),
            llm_gemini_attach_search_tool=self.chk_gemini_tools.isChecked(),
            model_front_desk=self.combo_front.currentText().strip(),
            model_deep_analysis=deep_m,
            anthropic_model=legacy_anthropic_model,
            push_serverchan_sendkey=self.ed_serverchan.text().strip(),
            push_pushplus_token=self.ed_pushplus.text().strip(),
            push_wecom_webhook=self.ed_wecom.text().strip(),
            push_host_alert_url=self.ed_push_host.text().strip(),
            audio_target_exe=self.ed_audio_exe.text().strip() or "AliWorkbench.exe",
            audio_gate_fire_only_when_qianniu_running=self.chk_audio_gate.isChecked(),
            audio_peak_threshold=float(self.spin_audio_threshold.value()),
            audio_poll_interval_s=float(self.spin_audio_poll.value()),
            audio_cooldown_s=prev.audio_cooldown_s,
            sweep_interval_minutes=int(self.spin_sweep.value()),
            visual_sentry_enabled=self.chk_visual_sentry.isChecked(),
            visual_sentry_interval_s=int(self.spin_visual_interval.value()),
            capture_delay_s=float(self.spin_capture_delay.value()),
            vm_host_program=self.ed_vm_program.text().strip(),
            vm_host_args=self.ed_vm_args.text().strip(),
            embedding_model=self.combo_embedding.currentText().strip() or "text-embedding-3-large",
            kb_adherence_pct=int(self.slider_kb_adherence.value()),
            panse_exclusive_embed_enabled=prev.panse_exclusive_embed_enabled,
            panse_embed_model_dir=prev.panse_embed_model_dir,
            panse_rerank_model_id=prev.panse_rerank_model_id,
            panse_rrf_k=prev.panse_rrf_k,
            panse_rerank_min_score=prev.panse_rerank_min_score,
            panse_rag_pool_limit=prev.panse_rag_pool_limit,

            # v1.5.x 拟人化：6 个 UI 开关 + 1 个下拉，其余参数 pass-through prev
            pin_window_enabled=self.chk_pin_window.isChecked(),
            pin_window_x=int(self.spin_pin_x.value()),
            pin_window_y=int(self.spin_pin_y.value()),
            pin_window_width=int(self.spin_pin_width.value()),
            pin_window_height=int(self.spin_pin_height.value()),
            pin_window_drift_tolerance_px=int(self.spin_pin_drift.value()),
            pin_window_dpi_warn_only=self.chk_pin_dpi_warn_only.isChecked(),

            text_extract_mode=self.combo_text_extract.currentText().strip().lower(),

            humanize_reply_timing_enabled=self.chk_reply_timing.isChecked(),
            humanize_reply_delay_min_s=prev.humanize_reply_delay_min_s,
            humanize_reply_delay_max_s=prev.humanize_reply_delay_max_s,
            humanize_typing_extra_s_per_chars=prev.humanize_typing_extra_s_per_chars,
            humanize_typing_extra_chars_unit=prev.humanize_typing_extra_chars_unit,
            humanize_gaussian_jitter_ratio=prev.humanize_gaussian_jitter_ratio,
            humanize_quiet_hours_enabled=self.chk_quiet_hours.isChecked(),
            humanize_quiet_hours_start=prev.humanize_quiet_hours_start,
            humanize_quiet_hours_end=prev.humanize_quiet_hours_end,

            humanize_real_typing_enabled=self.chk_real_typing.isChecked(),
            humanize_typing_inter_char_min_s=prev.humanize_typing_inter_char_min_s,
            humanize_typing_inter_char_max_s=prev.humanize_typing_inter_char_max_s,
            humanize_typing_typo_rate=prev.humanize_typing_typo_rate,
            humanize_typing_backspace_pause_min_s=prev.humanize_typing_backspace_pause_min_s,
            humanize_typing_backspace_pause_max_s=prev.humanize_typing_backspace_pause_max_s,

            humanize_mouse_jitter_enabled=self.chk_mouse_jitter.isChecked(),
            humanize_mouse_jitter_px=prev.humanize_mouse_jitter_px,
            humanize_mouse_jitter_sigma_divisor=prev.humanize_mouse_jitter_sigma_divisor,
            humanize_mouse_curved_motion=prev.humanize_mouse_curved_motion,
            humanize_mouse_motion_steps=prev.humanize_mouse_motion_steps,

            humanize_idle_action_enabled=self.chk_idle_action.isChecked(),
            humanize_idle_expected_per_hour=prev.humanize_idle_expected_per_hour,
            humanize_idle_min_interval_s=prev.humanize_idle_min_interval_s,
            humanize_idle_dwell_min_s=prev.humanize_idle_dwell_min_s,
            humanize_idle_dwell_max_s=prev.humanize_idle_dwell_max_s,
        )

    def _search_qianniu_processes(self) -> None:
        from apps.core.audio import search_audio_candidate_processes

        rows = search_audio_candidate_processes()
        if not rows:
            QMessageBox.information(
                self,
                "搜索千牛进程名",
                "没找到名字含 workbench / qianniu / 千牛 / alimm 的进程。\n"
                "请先把千牛打开（确保已登录到聊天界面）后再点本按钮。\n"
                "若仍找不到，可能是 psutil 没权限读取该进程名（试试以管理员运行）。",
            )
            return
        # 取最常见的（如 AliWorkbench.exe）作默认选项
        names = sorted({nm for _pid, nm in rows}, key=lambda s: (s.lower() != "aliworkbench.exe", s.lower()))
        lines = [f"PID={pid}    {nm}" for pid, nm in rows]
        dlg = QDialog(self)
        dlg.setWindowTitle("搜索到的千牛相关进程")
        dlg.resize(520, 380)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("以下是当前所有可能的千牛相关进程，请选一个写回「监听进程名」："))
        combo = QComboBox()
        combo.addItems(names)
        v.addWidget(combo)
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setPlainText("\n".join(lines))
        log.setMinimumHeight(180)
        v.addWidget(log)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            picked = combo.currentText().strip()
            if picked:
                self.ed_audio_exe.setText(picked)

    def _open_audio_probe_dialog(self) -> None:
        from apps.core.audio import enumerate_sessions_now

        dlg = QDialog(self)
        dlg.setWindowTitle("实时峰值监视（10 秒，逐 WASAPI 会话）")
        dlg.resize(720, 480)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            f"阈值（来自当前面板，不需保存即可生效本次诊断）："
            f"<b>{self.spin_audio_threshold.value():.3f}</b>。"
            "现在请让千牛响一次叮咚——下方会逐 tick 打印每条会话的峰值。"
        ))
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setMinimumHeight(360)
        v.addWidget(log)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        threshold = float(self.spin_audio_threshold.value())
        # 关心的进程名（小写 substring）
        target_low = (self.ed_audio_exe.text().strip() or "AliWorkbench.exe").lower()

        state = {
            "ticks": 0,
            "max_per_pid": {},  # type: dict[tuple[int,str], float]
            "global_max": 0.0,
            "would_fire": False,
        }

        timer = QTimer(dlg)
        timer.setInterval(100)
        total_ms = 10_000

        def on_tick() -> None:
            state["ticks"] += 1
            try:
                rows = enumerate_sessions_now()
            except Exception as e:
                log.appendPlainText(f"[t=+{state['ticks']*0.1:.1f}s] 枚举异常：{e!r}")
                return
            if isinstance(rows, str):
                # 返回错误字符串：pycaw 未加载或 COM 异常
                if state["ticks"] == 1:
                    log.appendPlainText(f"[错误] {rows}")
                return
            if not rows:
                log.appendPlainText(f"[t=+{state['ticks']*0.1:.1f}s] WASAPI 会话列表为空（声卡无活跃输出？）")
                return
            # 仅在峰值有变化或首次时打印（避免刷屏太狠）
            tick_global = max((p for _pid, _nm, p, _hm in rows), default=0.0)
            if tick_global > state["global_max"]:
                state["global_max"] = tick_global
            for pid, nm, peak, has_meter in rows:
                key = (pid, nm)
                prev_mx = state["max_per_pid"].get(key, 0.0)
                if peak > prev_mx:
                    state["max_per_pid"][key] = peak
            if tick_global >= threshold:
                state["would_fire"] = True
            # 每秒打印一次完整快照
            if state["ticks"] % 10 == 0 or tick_global >= threshold:
                ts = state["ticks"] * 0.1
                lines = [f"[t=+{ts:5.1f}s] global_max_so_far={state['global_max']:.4f}  threshold={threshold:.3f}  would_fire={'YES' if state['would_fire'] else 'no'}"]
                shown = 0
                for pid, nm, peak, has_meter in rows:
                    if shown >= 8 and peak < 0.005 and target_low not in nm.lower():
                        continue
                    flag = "*" if target_low in nm.lower() else " "
                    meter_flag = "" if has_meter else " (no-meter)"
                    lines.append(f"  {flag} PID={pid:<6} {nm:<28} peak={peak:.4f}{meter_flag}")
                    shown += 1
                log.appendPlainText("\n".join(lines))

        def finish() -> None:
            timer.stop()
            # 汇总
            ranked = sorted(state["max_per_pid"].items(), key=lambda kv: (-kv[1], kv[0][1].lower()))
            log.appendPlainText("\n=== 10 秒诊断结束 ===")
            log.appendPlainText(f"全局最大峰值：{state['global_max']:.4f}    当前阈值：{threshold:.3f}")
            log.appendPlainText(f"是否会触发：{'YES（按当前阈值，会触发接待）' if state['would_fire'] else 'NO（没有任何会话峰值越过阈值）'}")
            if ranked:
                log.appendPlainText("各会话峰值（按最大值倒序）：")
                for (pid, nm), mx in ranked[:15]:
                    flag = "*" if target_low in nm.lower() else " "
                    log.appendPlainText(f"  {flag} PID={pid:<6} {nm:<28} max_peak={mx:.4f}")
            # 建议
            hit_target = next((mx for (pid, nm), mx in ranked if target_low in nm.lower()), 0.0)
            if hit_target > 0.001 and hit_target < threshold:
                sug = max(0.003, hit_target * 0.6)
                log.appendPlainText(f"\n建议：千牛会话峰值 ≈ {hit_target:.4f}，把阈值调到 {sug:.3f} 即可触发。")
            elif hit_target <= 0.001 and state["global_max"] >= threshold:
                log.appendPlainText("\n提示：千牛自身会话读不到峰值，但全局有其它会话有声。")
                log.appendPlainText("叮咚可能走的是 PID=0 系统通知声道——观察上面是否有 SystemSounds 项跳动。")
            elif state["global_max"] < 0.001:
                log.appendPlainText("\n提示：10 秒内没听到任何声音。请确认：")
                log.appendPlainText("  1) 千牛设置 → 消息提醒 → 提示音 没被关；")
                log.appendPlainText("  2) Windows 声音 → 通信 → 设为「不执行任何操作」（避免叮咚被自动降音）；")
                log.appendPlainText("  3) 系统主音量没静音、扬声器选对。")

        timer.timeout.connect(on_tick)
        QTimer.singleShot(total_ms, finish)
        timer.start()
        dlg.exec()
        timer.stop()

    def _launch_vm_host(self) -> None:
        prog = self.ed_vm_program.text().strip()
        if not prog:
            QMessageBox.information(
                self,
                "虚拟机",
                "请先在上方填写「主机程序」路径并保存；也可临时填写后直接点本按钮试用。",
            )
            return
        parts = shlex.split(self.ed_vm_args.text(), posix=False)
        ok = QProcess.startDetached(prog, parts)
        if not ok:
            QMessageBox.warning(self, "虚拟机", "未能启动进程，请检查路径、参数或权限。")

    def _copy_guest_launch_hint(self) -> None:
        exe = "AIWorkbench.exe"
        if getattr(sys, "frozen", False):
            exe = sys.executable
        text = (
            f"在虚拟机内打开命令行，进入本软件所在目录后执行（每店不同名称）：\r\n"
            f'{exe} --profile 店铺甲\r\n\r\n'
            "或为桌面快捷方式「目标」末尾追加： --profile 店铺甲"
        )
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "已复制", "多开示例已复制到剪贴板。")

    def _show_copy_dialog(self, title: str, text: str) -> None:
        """弹只读文本框 + 复制全部按钮，方便复制给开发者。"""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        lay.addWidget(edit)
        row = QHBoxLayout()
        btn_copy = QPushButton("复制全部")

        def _do_copy() -> None:
            QApplication.clipboard().setText(text)
            btn_copy.setText("已复制 ✓")

        btn_copy.clicked.connect(_do_copy)
        row.addWidget(btn_copy)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        row.addWidget(btn_close)
        lay.addLayout(row)
        dlg.exec()

    def _export_fix_archive(self) -> None:
        """
        v1.6.2 bug历史按钮：一键同时做两件事——
          1. 生成 bug 过往历史报告（markdown）+ 弹可复制对话框
          2. 打开截图文件夹（dist/data/sqlite/debug），方便直接拿图发开发者
        """
        report_ok = False
        md = ""
        out_name = ""
        try:
            from apps.core.diagnostics.fix_archive import (
                build_report_markdown,
                export_markdown,
            )
            md = build_report_markdown()
            out_name = export_markdown().name
            report_ok = True
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"bug历史导出失败：{e!r}")

        # 打开截图文件夹（与报告独立，报告失败也尝试开文件夹）
        opened_dir = ""
        try:
            import os
            from apps.core.runtime_paths import default_sqlite_db_path

            debug_dir = default_sqlite_db_path().parent / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            opened_dir = str(debug_dir)
            os.startfile(opened_dir)  # noqa: S606
        except Exception as e:
            opened_dir = f"（自动打开失败，请手动打开）{e!r}"

        if report_ok:
            self._show_copy_dialog(
                f"bug 历史（已保存 {out_name}）\n截图文件夹：{opened_dir}",
                md,
            )

    def _ai_root_cause(self) -> None:
        """把『修了仍复现』的问题交给 LLM 分析根因。"""
        try:
            from apps.core.diagnostics.fix_archive import summarize_for_llm
            summary = summarize_for_llm()
        except Exception as e:
            QMessageBox.warning(self, "分析失败", f"读取修复档案失败：{e!r}")
            return
        if summary.startswith("（暂无"):
            QMessageBox.information(
                self, "AI 根因分析",
                "目前没有『修了仍复现』的问题——说明修复都生效了，或运行时间还短。",
            )
            return
        try:
            from apps.core.ai.llm_client import deep_analysis_completion
            from apps.core.configs.base_settings import load_base_settings
            prompt = (
                "下面是一个客服程序里『反复修但仍复现』的问题清单，"
                "每条含历次修复摘要和当时根因猜测。请指出："
                "为什么之前的修复没生效、真正根因、下一步改哪里。简洁可执行。\n\n"
                + summary
            )
            analysis = (deep_analysis_completion(
                settings=load_base_settings(),
                system="你是资深工程师，做缺陷根因分析。",
                user=prompt,
            ) or "").strip()
        except Exception as e:
            self._show_copy_dialog(
                "AI 根因分析（LLM 不可用）",
                f"LLM 调用失败：{e!r}\n\n--- 复现问题清单 ---\n{summary}",
            )
            return
        self._show_copy_dialog("AI 根因分析", analysis or summary)

    def _save(self) -> None:
        st = self._gather_settings()
        if not st.model_front_desk.strip():
            QMessageBox.warning(self, "无法保存", "请填写「前台实时客服模型」。")
            return
        if not st.model_deep_analysis.strip():
            QMessageBox.warning(self, "无法保存", "请填写「AI 陪伴与深度分析模型」。")
            return
        save_base_settings(st)
        self._reload_path_label()
        QMessageBox.information(self, "已保存", f"已写入：\n{default_base_settings_path()}")

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._reload_path_label()
        self._defer_sync_scroll_width()

    def _test_all_preset_models(self) -> None:
        st = self._gather_settings()
        lines: list[str] = []

        lines.append("① 通用路径（litellm_completion_text / Chat Completions）")
        for model in _all_presets_ordered_unique():
            key = resolve_litellm_api_key(st, model)
            if not key:
                lines.append(f"  ⏭ {model}  跳过（未配置对应密钥）")
                continue
            try:
                raw = litellm_completion_text(
                    settings=st,
                    model=model,
                    system='你只回复一个单词 "OK"。不要其它内容。',
                    user="ping",
                    max_tokens=16,
                    temperature=0.0,
                )
                snippet = (raw or "").replace("\r", "").replace("\n", " ")[:120]
                lines.append(f"  ✓ {model}\n      → {snippet}")
            except Exception as e:
                lines.append(f"  ✗ {model}\n      → {e}")

        lines.append("")
        lines.append(
            "② 深度任务路径（deep_analysis=True，与各深度预设模型一一对应）"
        )
        _seen_deep: set[str] = set()
        for model in _DEEP_MODELS:
            if model in _seen_deep:
                continue
            _seen_deep.add(model)
            key = resolve_litellm_api_key(st, model)
            if not key:
                lines.append(f"  ⏭ {model}  跳过（未配置对应密钥）")
                continue
            try:
                raw = litellm_completion_text(
                    settings=st,
                    model=model,
                    system='你只回复一个单词 "OK"。不要其它内容。',
                    user="ping",
                    max_tokens=64,
                    temperature=0.0,
                    deep_analysis=True,
                )
                snippet = (raw or "").replace("\r", "").replace("\n", " ")[:120]
                lines.append(f"  ✓ {model}\n      → {snippet}")
            except Exception as e:
                lines.append(f"  ✗ {model}\n      → {e}")

        dlg = QDialog(self)
        dlg.setWindowTitle("全部预设连通性结果")
        dlg.resize(720, 520)
        v = QVBoxLayout(dlg)
        te = QPlainTextEdit(dlg)
        te.setReadOnly(True)
        te.setPlainText("\n".join(lines))
        v.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()

    def _open_chat_test_dialog(self) -> None:
        dlg = _LLMChatTestDialog(self, self._gather_settings)
        pre = self.combo_front.currentText().strip()
        if pre:
            i = dlg.combo_model.findText(pre)
            if i >= 0:
                dlg.combo_model.setCurrentIndex(i)
            else:
                dlg.combo_model.setEditText(pre)
        dlg.exec()

    def _test_push_channels(self) -> None:
        from apps.core.push.service import push_all

        st = self._gather_settings()
        rs = push_all(
            st,
            title="AIWorkbench 推送测试",
            body="若收到本条，说明 Server酱 / PushPlus / 企微 Webhook 至少一路可用。",
        )
        if not rs:
            QMessageBox.warning(
                self,
                "推送测试",
                "未配置任何推送通道（Server酱 / PushPlus / 企微 / 宿主机 HTTP）。",
            )
            return
        QMessageBox.information(
            self,
            "推送结果",
            "\n".join(f"{r.channel}: ok={r.ok} {r.detail[:80]}" for r in rs),
        )
