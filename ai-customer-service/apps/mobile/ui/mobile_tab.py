"""
apps/mobile/ui/mobile_tab.py
============================
手机接待 Tab — PM 级产品设计，面向普通用户。

设计原则：
  1. 每个按钮 = 动词 + 说明词（"开始接待" 而不是 "启动"）
  2. Tooltip 必写：悬停 1.5s 可看到功能说明
  3. 状态三重表达：颜色 + 图标 + 中文文字
  4. 空状态有 4 步引导，不让用户迷失
  5. 所有跨线程写日志 → pyqtSignal + QueuedConnection，避免崩溃
  6. 每 2s 写 IPC 状态文件，供局域网仪表盘读取
  7. 每 1s 轮询 control_signal.json，响应紧急暂停指令
"""
from __future__ import annotations

import json
import logging
import queue
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.core.runtime_paths import mobile_state_dir as _mobile_state_dir, project_root as _project_root
from apps.mobile.device.device_manager import DeviceManager, DeviceState

_log = logging.getLogger("apps.mobile.ui")

# 通过 runtime_paths 解析，PyInstaller 打包和开发模式均正确指向项目根目录。
_STATE_DIR: Path = _mobile_state_dir()
_WEB_PORT = 8080

# ── 状态颜色 / 中文标签 ────────────────────────────────────────────────────
_COLOR = {
    DeviceState.DISCONNECTED: "#95A5A6",
    DeviceState.CONNECTING:   "#F39C12",
    DeviceState.CONNECTED:    "#3498DB",
    DeviceState.RUNNING:      "#27AE60",
    DeviceState.PAUSED:       "#E67E22",
    DeviceState.ERROR:        "#E74C3C",
}
_LABEL_CN = {
    DeviceState.DISCONNECTED: "未连接",
    DeviceState.CONNECTING:   "正在连接…",
    DeviceState.CONNECTED:    "已连接（待启动）",
    DeviceState.RUNNING:      "🟢 接待中",
    DeviceState.PAUSED:       "⏸ 已暂停",
    DeviceState.ERROR:        "❌ 发生错误",
}


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _styled_btn(text: str, color: str, hover: str, tooltip: str = "") -> QPushButton:
    """生成带统一样式的操作按钮。"""
    btn = QPushButton(text)
    btn.setFixedHeight(32)
    btn.setStyleSheet(
        f"QPushButton {{background:{color};color:#fff;border-radius:4px;"
        f"padding:0 10px;font-size:13px;}}"
        f"QPushButton:hover {{background:{hover};}}"
        f"QPushButton:disabled {{background:#555;color:#888;}}"
    )
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


# ===========================================================================
# 顶部统计栏（含一键暂停/恢复）
# ===========================================================================

class _SummaryBar(QWidget):
    """
    顶部一排指标 + 全局操作：
      左侧 3 个统计单元：今日接待总数 / 接待中设备 / 异常设备
      右侧 2 个全局按钮：⏸ 全部暂停 / ▶ 全部恢复
    数字大、文字小，一眼看出整体状态。
    """

    pause_all_clicked = pyqtSignal()
    resume_all_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(78)
        self.setStyleSheet(
            "background:#1C2E4A;border-radius:6px;"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 6, 16, 6)
        row.setSpacing(0)

        specs = [
            ("—", "今日接待总数", "#ECF0F1"),
            ("—", "接待中设备", "#2ECC71"),
            ("—", "异常设备",   "#E74C3C"),
        ]
        self._val_labels: list[QLabel] = []

        for i, (val, title, val_color) in enumerate(specs):
            if i:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color:#2C4470; min-width:1px; max-width:1px;")
                row.addWidget(sep)

            cell = QWidget()
            v = QVBoxLayout(cell)
            v.setContentsMargins(24, 2, 24, 2)
            v.setSpacing(2)
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)

            vl = QLabel(val)
            f = QFont(); f.setPointSize(24); f.setBold(True)
            vl.setFont(f)
            vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.setStyleSheet(f"color:{val_color};")
            self._val_labels.append(vl)

            tl = QLabel(title)
            tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tl.setStyleSheet("color:#7F8C8D;font-size:11px;")

            v.addWidget(vl)
            v.addWidget(tl)
            row.addWidget(cell, stretch=1)

        # ── 右侧：一键全部暂停 / 恢复 ─────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color:#2C4470; min-width:1px; max-width:1px;")
        row.addWidget(sep2)

        btn_cell = QWidget()
        btn_v = QVBoxLayout(btn_cell)
        btn_v.setContentsMargins(16, 4, 16, 4)
        btn_v.setSpacing(4)

        self._btn_pause_all = QPushButton("⏸  全部暂停")
        self._btn_pause_all.setFixedHeight(28)
        self._btn_pause_all.setToolTip(
            "一键暂停所有正在接待的设备（设备连接保持，可随时恢复）。\n"
            "适合临时离场、午休、发现异常时使用。"
        )
        self._btn_pause_all.setStyleSheet(
            "QPushButton{background:#E67E22;color:#fff;border-radius:4px;"
            "font-size:12px;padding:0 12px;}"
            "QPushButton:hover{background:#F39C12;}"
            "QPushButton:disabled{background:#444;color:#888;}"
        )

        self._btn_resume_all = QPushButton("▶  全部恢复")
        self._btn_resume_all.setFixedHeight(28)
        self._btn_resume_all.setToolTip("恢复所有已暂停设备的接待循环。")
        self._btn_resume_all.setStyleSheet(
            "QPushButton{background:#27AE60;color:#fff;border-radius:4px;"
            "font-size:12px;padding:0 12px;}"
            "QPushButton:hover{background:#2ECC71;}"
            "QPushButton:disabled{background:#444;color:#888;}"
        )

        self._btn_pause_all.clicked.connect(self.pause_all_clicked)
        self._btn_resume_all.clicked.connect(self.resume_all_clicked)

        btn_v.addWidget(self._btn_pause_all)
        btn_v.addWidget(self._btn_resume_all)
        row.addWidget(btn_cell)

    def refresh(self, total: int, active: int, errors: int) -> None:
        self._val_labels[0].setText(str(total))
        self._val_labels[0].setStyleSheet("color:#ECF0F1;font-size:24px;font-weight:bold;")
        self._val_labels[1].setText(str(active))
        err_color = "#E74C3C" if errors else "#95A5A6"
        self._val_labels[2].setText(str(errors))
        self._val_labels[2].setStyleSheet(f"color:{err_color};font-size:24px;font-weight:bold;")
        # 按钮启用状态：有 RUNNING 才允许暂停；有 PAUSED 才允许恢复（active 不包含 paused）
        self._btn_pause_all.setEnabled(active > 0)


# ===========================================================================
# 影子模式横幅（开启后顶部显眼提示）
# ===========================================================================

class _ShadowModeBanner(QWidget):
    """
    影子模式开启时悬挂在统计栏下方，明确告知"消息不会真实发送"。
    避免商家看到接待统计数字误以为真在接待。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setStyleSheet(
            "background:#2A3A10;border:1px solid #5D8030;border-radius:4px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lbl = QLabel(
            "🧪  <b>试运行模式中</b>　·　所有回复仅记录到日志，"
            "<span style='color:#FFD;'>不会真实发送给买家</span>　·　"
            "确认效果满意后请关闭"
        )
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet("color:#CFE0A0;font-size:12px;")
        lay.addWidget(lbl)
        lay.addStretch()
        self.hide()


# ===========================================================================
# 空状态引导（无设备时显示）
# ===========================================================================

class _EmptyGuide(QWidget):
    """
    首次使用时显示的引导卡片，4 步告诉用户怎么开始。
    最下方有一个大按钮直接跳转到"添加设备"流程。
    """

    add_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setContentsMargins(60, 40, 60, 40)
        outer.setSpacing(0)

        # 大图标
        icon = QLabel("📱")
        f0 = QFont(); f0.setPointSize(52)
        icon.setFont(f0)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(icon)

        outer.addSpacing(12)

        title = QLabel("手机接待助手")
        f1 = QFont(); f1.setPointSize(17); f1.setBold(True)
        title.setFont(f1)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        sub = QLabel("用手机千牛自动回复买家消息，无需全程盯屏")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:#888;font-size:13px;")
        outer.addWidget(sub)

        outer.addSpacing(28)

        # 步骤卡片
        card = QGroupBox("快速开始 — 四步搞定")
        card.setStyleSheet(
            "QGroupBox { border:1px solid #3A5070; border-radius:6px; "
            "padding-top:14px; font-size:13px; font-weight:bold; }"
            "QGroupBox::title { subcontrol-origin:margin; left:12px; }"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setSpacing(14)
        card_lay.setContentsMargins(20, 10, 20, 14)

        steps = [
            ("❶", "准备设备",
             "打开手机或雷电模拟器中的<b>千牛 App</b>，\n"
             "并确保已通过 USB 数据线 或 WiFi 连接到这台电脑。"),
            ("❷", "扫描设备",
             "点击左侧<b>「🔍 扫描新设备」</b>，\n"
             "系统自动识别已连接的手机（无需手动输入 IP）。"),
            ("❸", "绑定店铺",
             "点击<b>「＋ 添加设备」</b>，选择设备类型并绑定店铺配置文件。\n"
             "一台手机对应一家店铺，多台互不影响。"),
            ("❹", "开始接待",
             "点击设备卡片上的<b>「▶ 开始接待」</b>，\n"
             "系统自动读取并回复买家消息，日志实时显示在右侧。"),
        ]

        for num, sub_title, desc in steps:
            row_w = QWidget()
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(10)

            num_lbl = QLabel(num)
            nf = QFont(); nf.setPointSize(18); nf.setBold(True)
            num_lbl.setFont(nf)
            num_lbl.setFixedWidth(32)
            num_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            num_lbl.setStyleSheet("color:#3498DB;")

            right_v = QVBoxLayout()
            right_v.setSpacing(2)
            ttl = QLabel(sub_title)
            tf = QFont(); tf.setBold(True); tf.setPointSize(12)
            ttl.setFont(tf)
            dsc = QLabel(desc)
            dsc.setTextFormat(Qt.TextFormat.RichText)
            dsc.setWordWrap(True)
            dsc.setStyleSheet("color:#aaa;font-size:12px;")
            right_v.addWidget(ttl)
            right_v.addWidget(dsc)

            row_lay.addWidget(num_lbl)
            row_lay.addLayout(right_v, stretch=1)
            card_lay.addWidget(row_w)

        outer.addWidget(card)
        outer.addSpacing(20)

        btn = QPushButton("＋  立即添加第一台设备，开始接待")
        btn.setFixedHeight(44)
        btn.setStyleSheet(
            "QPushButton{background:#2980B9;color:#fff;border-radius:5px;"
            "font-size:14px;font-weight:bold;}"
            "QPushButton:hover{background:#3498DB;}"
        )
        btn.setToolTip("打开设备添加向导，填写设备信息并绑定店铺")
        btn.clicked.connect(self.add_clicked)
        outer.addWidget(btn)


# ===========================================================================
# 设备卡片
# ===========================================================================

class DeviceCard(QGroupBox):
    """
    单台设备的运行状态卡片。

    信息层次：
      主标题 = 店铺名（用户关心的）
      次要信息 = 设备编号 / 类型
      操作按钮 = 中文动词，颜色区分
      右侧 = 实时截屏预览（5s 刷新）
    """

    start_clicked = pyqtSignal(str)
    pause_clicked = pyqtSignal(str)
    stop_clicked  = pyqtSignal(str)
    test_clicked  = pyqtSignal(str)

    def __init__(
        self, device_id: str, shop_name: str, device_type: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device_id   = device_id
        self._shop_name  = shop_name
        self._dev_type   = device_type

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QGroupBox{border:1px solid #3A5070;border-radius:6px;"
            "padding-top:14px;font-size:13px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:12px;}"
        )
        self._update_title(DeviceState.DISCONNECTED)

        # ── 信息区 ──────────────────────────────────────
        self._lbl_status = QLabel()
        self._lbl_status.setTextFormat(Qt.TextFormat.RichText)

        self._lbl_device = QLabel(f"设备编号：{device_id}　类型：{self._type_cn(device_type)}")
        self._lbl_device.setStyleSheet("color:#888;font-size:12px;")

        self._lbl_stats = QLabel("今日接待：0 条　|　上次回复：—　|　异常：0 次")
        self._lbl_stats.setStyleSheet("font-size:12px;")

        info_lay = QVBoxLayout()
        info_lay.setSpacing(5)
        info_lay.addWidget(self._lbl_status)
        info_lay.addWidget(self._lbl_device)
        info_lay.addWidget(self._lbl_stats)
        info_lay.addStretch()

        # ── 截屏预览 ────────────────────────────────────
        self._lbl_preview = QLabel("📸 截屏预览")
        self._lbl_preview.setFixedSize(160, 96)
        self._lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_preview.setStyleSheet(
            "background:#111;border:1px solid #3A5070;"
            "color:#555;font-size:11px;border-radius:3px;"
        )
        self._lbl_preview.setToolTip("每 5 秒自动刷新设备屏幕截图（仅接待中时）")

        top_row = QHBoxLayout()
        top_row.addLayout(info_lay, stretch=1)
        top_row.addWidget(self._lbl_preview)

        # ── 操作按钮 ────────────────────────────────────
        self._btn_start = _styled_btn(
            "▶  开始接待", "#27AE60", "#2ECC71",
            "启动自动接待：系统将实时监控买家消息，并按话术规则自动回复。\n"
            "回复过程完全模拟真人操作，不易被平台检测。",
        )
        self._btn_pause = _styled_btn(
            "⏸  暂停接待", "#E67E22", "#F39C12",
            "暂停自动回复（设备连接保持）。\n"
            "暂停期间买家消息不会自动回复，随时可点击「继续接待」恢复。",
        )
        self._btn_stop = _styled_btn(
            "⏹  停止接待", "#C0392B", "#E74C3C",
            "停止接待并断开 ADB 连接。\n"
            "再次接待需要重新点击「开始接待」。\n"
            "（设备本身不受影响，只是程序与设备解绑。）",
        )
        self._btn_test = _styled_btn(
            "🔧  测试连接", "#34495E", "#4A6075",
            "立即检测设备连通性（adapter.is_alive）。\n"
            "故障排查时点击此按钮，比靠错误日志快得多。",
        )

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_pause)
        btn_row.addWidget(self._btn_stop)
        btn_row.addWidget(self._btn_test)
        btn_row.addStretch()

        card_lay = QVBoxLayout(self)
        card_lay.addLayout(top_row)
        card_lay.addSpacing(6)
        card_lay.addLayout(btn_row)

        self._btn_start.clicked.connect(lambda: self.start_clicked.emit(self.device_id))
        self._btn_pause.clicked.connect(lambda: self.pause_clicked.emit(self.device_id))
        self._btn_stop.clicked.connect(lambda: self.stop_clicked.emit(self.device_id))
        self._btn_test.clicked.connect(lambda: self.test_clicked.emit(self.device_id))

        self.update_state(DeviceState.DISCONNECTED, 0, 0.0)

    @staticmethod
    def _type_cn(t: str) -> str:
        return {"emulator": "雷电模拟器", "wifi": "WiFi 无线", "usb": "USB 数据线"}.get(t, t)

    def _update_title(self, state: DeviceState) -> None:
        dot_color = _COLOR.get(state, "#888")
        label_cn  = _LABEL_CN.get(state, str(state))
        shop      = self._shop_name or "（未绑定店铺）"
        self.setTitle(
            f'<span style="color:{dot_color};">●</span>  {shop}  '
            f'<span style="color:{dot_color};font-size:11px;">[{label_cn}]</span>'
        )

    def update_state(
        self,
        state: DeviceState,
        count: int,
        last_at: float,
        error: str = "",
        error_count: int = 0,
    ) -> None:
        self._update_title(state)
        color = _COLOR.get(state, "#888")
        label = _LABEL_CN.get(state, str(state))

        detail = f"  <small style='color:#E74C3C;'>{error}</small>" if error else ""
        self._lbl_status.setText(
            f'<span style="color:{color};font-weight:bold;font-size:13px;">'
            f'{label}</span>{detail}'
        )

        last_str = time.strftime("%H:%M:%S", time.localtime(last_at)) if last_at else "—"
        err_html = (
            f"<span style='color:#E74C3C;'>异常：{error_count} 次</span>"
            if error_count else f"异常：{error_count} 次"
        )
        self._lbl_stats.setText(
            f"今日接待：{count} 条　|　上次回复：{last_str}　|　{err_html}"
        )
        self._lbl_stats.setTextFormat(Qt.TextFormat.RichText)

        running   = state == DeviceState.RUNNING
        paused    = state == DeviceState.PAUSED
        connected = state in (DeviceState.CONNECTED, DeviceState.RUNNING, DeviceState.PAUSED)

        self._btn_start.setEnabled(not running and connected or state in (DeviceState.DISCONNECTED, DeviceState.ERROR))
        self._btn_start.setText("▶  继续接待" if paused else "▶  开始接待")
        self._btn_pause.setEnabled(running)
        self._btn_stop.setEnabled(connected or running or paused)

    def update_screenshot(self, img_bytes: bytes) -> None:
        img = QImage.fromData(img_bytes)
        if not img.isNull():
            # 缩略图 80×144（竖屏），FastTransformation 避免主线程阻塞
            px = QPixmap.fromImage(img).scaled(
                80, 144,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._lbl_preview.setPixmap(px)
            self._lbl_preview.setText("")


# ===========================================================================
# 添加设备向导
# ===========================================================================

class AddDeviceDialog(QDialog):
    """
    分三栏的添加设备对话框：
    ① 选择设备类型（带说明）
    ② 填写设备编号 / IP
    ③ 选择店铺配置文件
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加手机设备")
        self.setMinimumWidth(480)

        vbox = QVBoxLayout(self)
        vbox.setSpacing(12)

        # ── ① 设备类型 ─────────────────────────────────
        type_box = QGroupBox("① 选择设备连接类型")
        type_lay = QVBoxLayout(type_box)
        self._combo_type = QComboBox()
        # v1.5.x：APK 方案默认排第一，无 ADB / 无外来 APK 痕迹
        items = [
            ("apk_http", "🤖  Android APK  （v1.5+ 真手机，最稳，推荐）"),
            ("wifi",     "📶  WiFi 无线调试  （同局域网手机 + uiautomator2）"),
            ("emulator", "🖥️  雷电模拟器  （本机端口）"),
            ("usb",      "🔌  USB 数据线  （直连真机）"),
        ]
        self._type_keys = [k for k, _ in items]
        for _, label in items:
            self._combo_type.addItem(label)
        self._combo_type.setToolTip(
            "推荐：Android APK（无 ADB / 无外来 APK 痕迹，v1.5 新增）\n"
            "兼容：WiFi/雷电模拟器/USB 走 uiautomator2 旧方案（v1.4 兼容路径）"
        )
        self._combo_type.currentIndexChanged.connect(self._on_type_changed)
        type_lay.addWidget(self._combo_type)

        self._lbl_type_hint = QLabel()
        self._lbl_type_hint.setWordWrap(True)
        self._lbl_type_hint.setStyleSheet("color:#888;font-size:12px;")
        type_lay.addWidget(self._lbl_type_hint)
        vbox.addWidget(type_box)

        # ── ② 设备编号 ─────────────────────────────────
        id_box = QGroupBox("② 填写设备编号 / 地址")
        id_form = QFormLayout(id_box)
        self._edit_id = QLineEdit()
        self._edit_id.setPlaceholderText("127.0.0.1:5555")
        self._edit_id.setToolTip(
            "雷电模拟器默认: 127.0.0.1:5555\n"
            "WiFi 手机: 192.168.x.x:5555（手机 IP + 端口）\n"
            "USB 手机: 设备串号（从 adb devices 命令获取）"
        )
        id_form.addRow("设备地址：", self._edit_id)
        vbox.addWidget(id_box)

        # ── ②' APK 配对（仅 apk_http 类型显示）─────────
        self._apk_box = QGroupBox("②' APK 配对（粘贴 APK 屏幕上的二维码 JSON）")
        apk_lay = QVBoxLayout(self._apk_box)
        apk_hint = QLabel(
            "操作步骤：\n"
            "  1) 在手机 APK 主界面看到「PC 端配对二维码」\n"
            "  2) 用任意扫码 App 扫描该二维码（如微信扫一扫）\n"
            "  3) 将识别出的 JSON 字符串粘贴到下方\n"
            "  4) 点「测试连接」验证；成功后再点「✔ 确认添加」"
        )
        apk_hint.setWordWrap(True)
        apk_hint.setStyleSheet("color:#666;font-size:12px;")
        apk_lay.addWidget(apk_hint)

        from PyQt6.QtWidgets import QPlainTextEdit
        self._edit_apk_json = QPlainTextEdit()
        self._edit_apk_json.setPlaceholderText(
            '{"ip":"192.168.1.42","port":8765,"token":"xxx..."}'
        )
        self._edit_apk_json.setFixedHeight(80)
        apk_lay.addWidget(self._edit_apk_json)

        apk_btn_row = QHBoxLayout()
        self._btn_apk_parse = QPushButton("🔍 解析 + 测试连接")
        self._btn_apk_parse.clicked.connect(self._on_apk_parse_test)
        self._lbl_apk_status = QLabel("尚未测试")
        self._lbl_apk_status.setStyleSheet("color:#888;")
        apk_btn_row.addWidget(self._btn_apk_parse)
        apk_btn_row.addWidget(self._lbl_apk_status, stretch=1)
        apk_lay.addLayout(apk_btn_row)

        vbox.addWidget(self._apk_box)
        self._apk_box.setVisible(False)
        # 解析结果缓存（apk_http 路径用）
        self._apk_pairing = None  # type: ignore[assignment]

        # ── ③ 店铺配置 ─────────────────────────────────
        shop_box = QGroupBox("③ 绑定店铺配置文件")
        shop_lay = QVBoxLayout(shop_box)
        hint = QLabel("选择已有的店铺配置（.yaml 文件），如果没有，请先在「设置中心」创建。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:12px;")
        shop_lay.addWidget(hint)

        shop_row = QHBoxLayout()
        self._edit_shop = QLineEdit()
        self._edit_shop.setPlaceholderText("configs/shops/xxx.yaml")
        self._edit_shop.setToolTip("店铺配置文件路径，通常在 configs/shops/ 目录下")
        btn_browse = QPushButton("📂  浏览文件…")
        btn_browse.setFixedWidth(110)
        btn_browse.setToolTip("打开文件选择器，找到店铺配置文件")
        btn_browse.clicked.connect(self._browse_shop)
        shop_row.addWidget(self._edit_shop)
        shop_row.addWidget(btn_browse)
        shop_lay.addLayout(shop_row)
        vbox.addWidget(shop_box)

        # ── 确认按钮 ────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("✔  确认添加")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

        self._on_type_changed(0)

    def _on_accept(self) -> None:
        """提交前先做格式校验，校验失败仅弹提示，不关闭对话框。"""
        device_id, dtype, shop_cfg = self.result_values()
        err = self._validate(device_id, dtype, shop_cfg)
        if err:
            QMessageBox.warning(self, "填写有误", err)
            return
        # apk_http 路径：确认前必须先「测试连接」通过 + 保存 pairing 到磁盘
        if dtype == "apk_http":
            if self._apk_pairing is None:
                QMessageBox.warning(
                    self, "请先测试连接",
                    "Android APK 设备需要先点「🔍 解析 + 测试连接」验证可达性。",
                )
                return
            try:
                from apps.mobile.device.pairing import save_pairing
                ok = save_pairing(self._apk_pairing)
                if not ok:
                    QMessageBox.warning(
                        self, "保存失败",
                        "配对令牌保存失败，请检查 ~/.aiworkbench/ 目录写权限。",
                    )
                    return
            except Exception as e:
                QMessageBox.warning(self, "保存异常", f"{e!r}")
                return
        self.accept()

    @staticmethod
    def _validate(device_id: str, dtype: str, shop_cfg: str) -> str:
        """返回错误描述，无错返回空字符串。"""
        import re as _re
        if not device_id:
            if dtype == "apk_http":
                return "请先在「②' APK 配对」区域粘贴 JSON 并点「测试连接」。"
            return "请填写「设备地址」（如 192.168.1.123:5555）"
        if dtype in ("wifi", "emulator"):
            # 格式：IP:Port
            if not _re.match(r"^[0-9.]+:\d{2,5}$", device_id):
                return (
                    f"「{device_id}」不是合法的 IP:Port 格式。\n\n"
                    "正确示例：\n"
                    "  WiFi 真机： 192.168.1.123:5555\n"
                    "  雷电模拟器： 127.0.0.1:5555"
                )
        if dtype == "apk_http":
            if not _re.match(r"^[0-9.]+:\d{2,5}$", device_id):
                return (
                    f"「{device_id}」不是合法 IP:Port 格式。\n"
                    "请重新点「🔍 解析 + 测试连接」从 JSON 解析。"
                )
        if not shop_cfg:
            return "请选择「店铺配置文件」（在 configs/shops/ 目录下的 .yaml 文件）"
        if not Path(shop_cfg).exists():
            return f"找不到店铺配置文件：\n{shop_cfg}\n\n请点「📂 浏览文件…」重新选择。"
        return ""

    def _on_type_changed(self, idx: int) -> None:
        # 顺序与 items 保持一致：apk_http / wifi / emulator / usb
        hints = [
            "Android APK（推荐）：手机装我们自己的 APK + 启用无障碍 → 启动 HTTP 服务 →\n"
            "PC 端粘贴 APK 屏幕上的二维码 JSON。无需 ADB，无外来 APK 痕迹。",
            "WiFi 调试：在手机「开发者选项」→ 无线调试，记录显示的「IP+端口」。\n"
            "示例：192.168.1.123:5555",
            "雷电模拟器：打开雷电，确认 ADB 端口为 5555。默认地址 127.0.0.1:5555。",
            "USB 调试：手机连接电脑后在「开发者选项 → USB 调试」中授权。\n"
            "之后点左侧「扫描新设备」直接取串号，无需手填。",
        ]
        if idx < len(hints):
            self._lbl_type_hint.setText(hints[idx])
        defaults = ["（由 APK 配对自动填写）", "192.168.x.x:5555", "127.0.0.1:5555", ""]
        if idx < len(defaults):
            self._edit_id.setPlaceholderText(defaults[idx])

        # apk_http 时显示 APK 配对块，禁用 _edit_id 让用户走配对流程；
        # 其他类型相反
        is_apk = (idx == 0)
        self._apk_box.setVisible(is_apk)
        self._edit_id.setEnabled(not is_apk)
        if is_apk:
            self._edit_id.clear()
            self._apk_pairing = None
            self._lbl_apk_status.setText("尚未测试")
            self._lbl_apk_status.setStyleSheet("color:#888;")

    def _on_apk_parse_test(self) -> None:
        """解析二维码 JSON → HttpMobileAdapter.connect() 测试 → 填回 _edit_id。"""
        raw = self._edit_apk_json.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "缺 JSON", "请先粘贴 APK 屏幕上的二维码 JSON。")
            return
        try:
            from apps.mobile.device.pairing import parse_pairing_qr
            from apps.mobile.adapter.http_mobile_adapter import HttpMobileAdapter
        except Exception as e:
            QMessageBox.warning(self, "import 失败", f"{e!r}\n请确认已安装 httpx。")
            return

        rec = parse_pairing_qr(raw)
        if rec is None:
            self._lbl_apk_status.setText("❌ JSON 格式不正确")
            self._lbl_apk_status.setStyleSheet("color:#c62828;")
            return

        # 同步测连：connect() 会探 /health + /api/sessions（鉴权）
        self._lbl_apk_status.setText("测试中…")
        self._lbl_apk_status.setStyleSheet("color:#1976D2;")
        self.repaint()

        adapter = HttpMobileAdapter(
            ip=rec.ip, port=rec.port, token=rec.token,
        )
        try:
            ok = adapter.connect()
        finally:
            try:
                adapter.disconnect()
            except Exception:
                pass

        if ok:
            self._apk_pairing = rec
            self._edit_id.setText(rec.device_id)
            self._lbl_apk_status.setText(f"✅ 连接成功：{rec.device_id}")
            self._lbl_apk_status.setStyleSheet("color:#2e7d32;font-weight:bold;")
        else:
            self._apk_pairing = None
            self._lbl_apk_status.setText(
                f"❌ 连接失败：{rec.device_id}（确认手机 APK 启动 + 无障碍授权 + WiFi 同段）"
            )
            self._lbl_apk_status.setStyleSheet("color:#c62828;")

    def _browse_shop(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择店铺配置文件",
            str(_project_root() / "configs" / "shops"),
            "YAML 配置 (*.yaml *.yml)",
        )
        if path:
            self._edit_shop.setText(path)

    def result_values(self) -> tuple[str, str, str]:
        """返回 (device_id, device_type_key, shop_cfg_path)。"""
        idx   = self._combo_type.currentIndex()
        dtype = self._type_keys[idx] if idx < len(self._type_keys) else "wifi"
        return self._edit_id.text().strip(), dtype, self._edit_shop.text().strip()


# ===========================================================================
# ZMQ IPC 服务器（主程序侧：广播状态 + 接收控制命令）
# ===========================================================================

try:
    import zmq as _zmq  # type: ignore[import]
    _ZMQ_AVAILABLE = True
except ImportError:
    _zmq = None  # type: ignore[assignment]
    _ZMQ_AVAILABLE = False

_ZMQ_PUB_ADDR = "tcp://127.0.0.1:5556"
_ZMQ_REP_ADDR = "tcp://127.0.0.1:5557"


class _ZmqIpcServer:
    """
    两个后台线程：
      PUB 线程：每 2s 向局域网仪表盘广播设备状态 JSON。
      REP 线程：接收仪表盘发来的控制命令，放入 cmd_queue 供 Qt 主线程消费。

    若 pyzmq 未安装，静默降级（只有文件 IPC 生效）。
    """

    def __init__(self, cmd_queue: "queue.Queue[str]") -> None:
        self._cmd_queue = cmd_queue
        self._state_fn: "Any" = None          # 由 set_state_fn() 注入
        self._stop = threading.Event()
        self._pub_sock: "Any" = None
        self._rep_sock: "Any" = None

    def set_state_fn(self, fn: "Any") -> None:
        """注入状态字典构建函数（MobileTab._build_ipc_state）。"""
        self._state_fn = fn

    def start(self) -> None:
        if not _ZMQ_AVAILABLE:
            _log.debug("pyzmq 未安装，ZMQ IPC 服务器已跳过")
            return
        try:
            ctx = _zmq.Context.instance()

            self._pub_sock = ctx.socket(_zmq.PUB)
            self._pub_sock.bind(_ZMQ_PUB_ADDR)

            self._rep_sock = ctx.socket(_zmq.REP)
            self._rep_sock.bind(_ZMQ_REP_ADDR)
            self._rep_sock.setsockopt(_zmq.RCVTIMEO, 500)  # 500ms 超时，循环检 _stop

            threading.Thread(
                target=self._pub_loop,
                daemon=True,
                name="ZmqPubState",
            ).start()
            threading.Thread(
                target=self._rep_loop,
                daemon=True,
                name="ZmqRepControl",
            ).start()
            _log.info("ZMQ IPC 服务器已启动 PUB=%s REP=%s", _ZMQ_PUB_ADDR, _ZMQ_REP_ADDR)
        except Exception as exc:
            _log.warning("ZMQ IPC 服务器启动失败: %r", exc)

    def stop(self) -> None:
        self._stop.set()
        for sock in (self._pub_sock, self._rep_sock):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def publish(self, state: dict) -> None:
        """外部调用：由 _write_ipc_state 在 Qt 定时器内调用（线程安全：ZMQ PUB 无需锁）。"""
        if self._pub_sock is None:
            return
        try:
            self._pub_sock.send(
                json.dumps(state, ensure_ascii=False).encode("utf-8"),
                _zmq.NOBLOCK,
            )
        except Exception:
            pass

    def _pub_loop(self) -> None:
        """每 2s 广播一次状态（兜底，状态变化时 _write_ipc_state 也会主动 publish）。"""
        while not self._stop.is_set():
            self._stop.wait(2.0)
            if self._state_fn is not None and not self._stop.is_set():
                try:
                    self.publish(self._state_fn())
                except Exception:
                    pass

    def _rep_loop(self) -> None:
        """接收控制命令，放入队列，立即回复确认。"""
        while not self._stop.is_set():
            if self._rep_sock is None:
                break
            try:
                raw = self._rep_sock.recv()
                msg = json.loads(raw.decode("utf-8"))
                action = msg.get("action", "")
                if action in ("pause_all", "resume_all"):
                    self._cmd_queue.put(action)
                    self._rep_sock.send(json.dumps({"ok": True}).encode("utf-8"))
                else:
                    self._rep_sock.send(
                        json.dumps({"ok": False, "error": "unknown action"}).encode("utf-8")
                    )
            except _zmq.Again:
                pass   # RCVTIMEO，继续循环
            except _zmq.ZMQError:
                break
            except Exception as exc:
                _log.debug("ZMQ REP 解析异常: %r", exc)


# ===========================================================================
# 主 Tab
# ===========================================================================

class MobileTab(QWidget):
    """
    手机接待 Tab 主体。

    布局（从上到下）：
      ─ 统计栏 (_SummaryBar)
      ─ QSplitter:
          左 (210px)  设备列表 + 按钮 + 局域网仪表盘
          中 (stretch=2)  空引导 或 设备卡片
          右 (stretch=1)  操作日志

    线程安全：所有跨线程日志通过 _log_signal + QueuedConnection 写入 QPlainTextEdit。
    IPC：每 2s 写 data/mobile_state/*.json 供局域网仪表盘读取；每 1s 轮询 control_signal.json。
    """

    _log_signal: pyqtSignal = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._manager        = DeviceManager()
        self._cards: dict[str, DeviceCard] = {}
        self._log_buf: dict[str, list[str]] = {}
        self._recent_msgs: list[dict] = []
        self._web_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._shadow_enabled: bool = False
        self._real_adapters: dict[str, Any] = {}   # device_id → 真实 adapter

        # ZMQ IPC：命令队列（REP 线程 → Qt 主线程）
        self._cmd_queue: queue.Queue = queue.Queue()
        self._zmq_server = _ZmqIpcServer(self._cmd_queue)
        self._zmq_server.set_state_fn(self._build_ipc_state)

        self._manager.load_devices_config()

        # ── 统计栏 ──────────────────────────────────────────────────────────
        self._summary = _SummaryBar()

        # ── 左栏 ────────────────────────────────────────────────────────────
        left_w = self._build_left_panel()

        # ── 中栏（空引导 / 卡片区）────────────────────────────────────────
        self._empty_guide = _EmptyGuide()
        self._empty_guide.add_clicked.connect(self._on_add_device)

        self._cards_widget = QWidget()
        self._cards_lay = QVBoxLayout(self._cards_widget)
        self._cards_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._cards_lay.setSpacing(10)
        self._cards_lay.setContentsMargins(6, 6, 6, 6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._cards_widget)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._mid_stack = QStackedWidget()
        self._mid_stack.addWidget(self._empty_guide)   # index 0
        self._mid_stack.addWidget(scroll)               # index 1

        mid_lay = QVBoxLayout()
        mid_lay.setContentsMargins(0, 0, 0, 0)
        mid_lay.setSpacing(0)
        mid_hdr = QLabel("  接待运行面板")
        mid_hdr.setStyleSheet(
            "background:#1C2E4A;color:#AAB;padding:5px 8px;font-size:12px;"
        )
        mid_lay.addWidget(mid_hdr)
        mid_lay.addWidget(self._mid_stack)
        mid_w = QWidget()
        mid_w.setLayout(mid_lay)

        # ── 右栏（日志）────────────────────────────────────────────────────
        right_w = self._build_right_panel()

        # ── 组合 Splitter ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_w)
        splitter.addWidget(mid_w)
        splitter.addWidget(right_w)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([210, 600, 300])

        # ── 试运行模式横幅（默认隐藏，开启后显示）─────────────────────────
        self._shadow_banner = _ShadowModeBanner()

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        root.addWidget(self._summary)
        root.addWidget(self._shadow_banner)
        root.addWidget(splitter)

        # 统计栏的全局按钮信号
        self._summary.pause_all_clicked.connect(self._on_pause_all)
        self._summary.resume_all_clicked.connect(self._on_resume_all)

        # ── 信号连接 ────────────────────────────────────────────────────────
        self._log_signal.connect(self._append_log_line, Qt.ConnectionType.QueuedConnection)

        # ── 定时器 ──────────────────────────────────────────────────────────
        # 5s: 刷新卡片状态 + 截屏
        self._t_refresh = QTimer(self)
        self._t_refresh.setInterval(5_000)
        self._t_refresh.timeout.connect(self._refresh_all)
        self._t_refresh.start()

        # 2s: 写 IPC 状态文件 + ZMQ 广播
        self._t_ipc = QTimer(self)
        self._t_ipc.setInterval(2_000)
        self._t_ipc.timeout.connect(self._write_ipc_state)
        self._t_ipc.start()

        # 1s: 消费 ZMQ 命令队列（紧急暂停/恢复）+ 文件控制信号降级轮询
        self._t_ctrl = QTimer(self)
        self._t_ctrl.setInterval(1_000)
        self._t_ctrl.timeout.connect(self._drain_cmd_queue)
        self._t_ctrl.start()

        # 5s: 检查 Web 进程存活
        self._t_web = QTimer(self)
        self._t_web.setInterval(5_000)
        self._t_web.timeout.connect(self._check_web_proc)
        self._t_web.start()

        # ── ZMQ IPC 服务器 ──────────────────────────────────────────────────
        self._zmq_server.start()

        # 载入已持久化设备
        self._reload_device_list()

    # -----------------------------------------------------------------------
    # 面板构建
    # -----------------------------------------------------------------------

    def _build_left_panel(self) -> QWidget:
        """左栏：设备列表 + 操作按钮 + 局域网仪表盘入口。"""

        hdr = QLabel("  📱  已添加设备")
        hdr.setStyleSheet("background:#1C2E4A;color:#AAB;padding:5px 8px;font-size:12px;")

        self._device_list = QListWidget()
        self._device_list.setToolTip("点击设备名称可在右侧查看该设备的专属日志")

        # 操作按钮
        def _left_btn(text: str, tip: str) -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(30)
            b.setToolTip(tip)
            return b

        btn_scan   = _left_btn("🔍  扫描新设备", "自动检测通过 USB 数据线或 WiFi 连接到电脑的手机设备")
        btn_add    = _left_btn("＋  添加设备",   "打开向导，将找到的设备绑定到指定店铺配置，添加后才能开始接待")
        btn_remove = _left_btn("－  移除选中",   "从列表中删除选中设备，接待循环会立即停止（不影响手机本身）")

        btn_scan.setStyleSheet(
            "QPushButton{background:#2C4470;border-radius:4px;color:#CCC;}"
            "QPushButton:hover{background:#3A5A90;}"
        )
        for b in (btn_add, btn_remove):
            b.setStyleSheet(
                "QPushButton{background:#2A3A50;border-radius:4px;color:#CCC;}"
                "QPushButton:hover{background:#3A5A70;}"
            )

        btn_scan.clicked.connect(self._on_scan_devices)
        btn_add.clicked.connect(self._on_add_device)
        btn_remove.clicked.connect(self._on_remove_device)

        # 局域网仪表盘区
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2A3A50;")

        dash_hdr = QLabel("  🌐  局域网监控仪表盘")
        dash_hdr.setStyleSheet("color:#7F8C8D;font-size:11px;padding:4px 0 2px 0;")

        dash_tip = QLabel("手机浏览器输入地址即可查看\n接待数据并远程紧急暂停")
        dash_tip.setWordWrap(True)
        dash_tip.setStyleSheet("color:#666;font-size:11px;")

        self._btn_web = QPushButton("🚀  启动局域网监控")
        self._btn_web.setFixedHeight(30)
        self._btn_web.setToolTip(
            "启动局域网仪表盘服务（端口 8080）。\n"
            "启动后同局域网内的手机浏览器可实时查看接待数据并紧急暂停所有设备。"
        )
        self._btn_web.setStyleSheet(
            "QPushButton{background:#1A5276;border-radius:4px;color:#CCC;}"
            "QPushButton:hover{background:#2471A3;}"
        )
        self._btn_web.clicked.connect(self._toggle_web_dashboard)

        self._lbl_web_url = QLabel("（未启动）")
        self._lbl_web_url.setStyleSheet("color:#666;font-size:11px;")
        self._lbl_web_url.setWordWrap(True)
        self._lbl_web_url.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._btn_copy_url = QPushButton("📋  复制访问地址")
        self._btn_copy_url.setFixedHeight(26)
        self._btn_copy_url.setToolTip(
            "把仪表盘 URL 复制到剪贴板。\n"
            "建议复制后用微信/钉钉发到手机，避免手抄出错。"
        )
        self._btn_copy_url.setStyleSheet(
            "QPushButton{background:#2A3A50;border-radius:4px;color:#AAA;font-size:11px;}"
            "QPushButton:hover{background:#3A4A60;}"
            "QPushButton:disabled{background:#222;color:#444;}"
        )
        self._btn_copy_url.setEnabled(False)
        self._btn_copy_url.clicked.connect(self._copy_web_url)

        lay = QVBoxLayout()
        lay.setContentsMargins(6, 0, 6, 6)
        lay.setSpacing(5)
        lay.addWidget(self._device_list)
        lay.addWidget(btn_scan)
        lay.addWidget(btn_add)
        lay.addWidget(btn_remove)
        lay.addWidget(sep)
        lay.addWidget(dash_hdr)
        lay.addWidget(dash_tip)
        lay.addWidget(self._btn_web)
        lay.addWidget(self._lbl_web_url)
        lay.addWidget(self._btn_copy_url)

        # ── 影子模式区 ──────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#2A3A50;")
        lay.addWidget(sep2)

        shadow_hdr = QLabel("  🧪  试运行模式（安全验证）")
        shadow_hdr.setStyleSheet("color:#7F8C8D;font-size:11px;padding:4px 0 2px 0;")
        lay.addWidget(shadow_hdr)

        shadow_tip = QLabel("AI 照常分析、生成回复，\n但不会真实发送给买家")
        shadow_tip.setWordWrap(True)
        shadow_tip.setStyleSheet("color:#666;font-size:11px;")
        lay.addWidget(shadow_tip)

        self._btn_shadow = QPushButton("🧪  开启试运行（不真实发送）")
        self._btn_shadow.setFixedHeight(30)
        self._btn_shadow.setToolTip(
            "首次使用强烈建议先开「试运行」一段时间！\n\n"
            "开启后系统会按真实流程接收消息、AI 生成回复，\n"
            "唯一区别是「不真实发送给买家」，仅记录到日志。\n\n"
            "确认回复质量满意后，再关闭试运行正式接待。"
        )
        self._btn_shadow.setStyleSheet(
            "QPushButton{background:#1A3A2A;border-radius:4px;color:#CCC;font-size:12px;}"
            "QPushButton:hover{background:#28503A;}"
        )
        self._btn_shadow.clicked.connect(self._toggle_shadow_mode)
        lay.addWidget(self._btn_shadow)

        btn_shadow_report = QPushButton("📄  查看试运行报告")
        btn_shadow_report.setFixedHeight(28)
        btn_shadow_report.setToolTip(
            "查看最近 7 天的试运行报告，包含拦截总数 + 各设备的样本回复。"
        )
        btn_shadow_report.setStyleSheet(
            "QPushButton{background:#1A2A3A;border-radius:4px;color:#AAA;font-size:12px;}"
            "QPushButton:hover{background:#243A5A;}"
        )
        btn_shadow_report.clicked.connect(self._view_shadow_report)
        lay.addWidget(btn_shadow_report)

        lay.addStretch()

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(hdr)
        outer.addLayout(lay)

        w = QWidget()
        w.setFixedWidth(215)
        w.setLayout(outer)
        return w

    def _build_right_panel(self) -> QWidget:
        """右栏：操作日志面板，可按设备过滤，支持一键清空。"""

        hdr = QLabel("  📋  操作记录")
        hdr.setStyleSheet("background:#1C2E4A;color:#AAB;padding:5px 8px;font-size:12px;")

        self._combo_filter = QComboBox()
        self._combo_filter.addItem("全部设备")
        self._combo_filter.setToolTip("按设备过滤日志，选择「全部设备」查看所有记录")
        self._combo_filter.currentTextChanged.connect(self._apply_log_filter)

        btn_clear = QPushButton("清空")
        btn_clear.setFixedWidth(52)
        btn_clear.setFixedHeight(26)
        btn_clear.setToolTip("清空当前日志显示（不影响实际接待，仅清除屏幕上的记录）")
        btn_clear.clicked.connect(self._log_view_clear)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("过滤设备："))
        filter_row.addWidget(self._combo_filter, stretch=1)
        filter_row.addWidget(btn_clear)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(3000)
        self._log_view.setToolTip("实时操作记录，绿色=成功，红色=错误（字体默认等宽）")
        self._log_view.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")

        right_info = QLabel(
            "💡 提示：日志仅供参考，接待成功后可在千牛后台查看实际消息记录"
        )
        right_info.setStyleSheet("color:#666;font-size:11px;")
        right_info.setWordWrap(True)

        lay = QVBoxLayout()
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)
        lay.addLayout(filter_row)
        lay.addWidget(self._log_view)
        lay.addWidget(right_info)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(hdr)
        outer.addLayout(lay)

        w = QWidget()
        w.setLayout(outer)
        return w

    # -----------------------------------------------------------------------
    # 设备列表管理
    # -----------------------------------------------------------------------

    def _reload_device_list(self) -> None:
        self._device_list.clear()
        for rec in self._manager.all_records():
            self._device_list.addItem(rec.device_id)
            self._ensure_card(rec.device_id, rec.shop_cfg_path, rec.device_type)
        self._update_mid_stack()
        self._update_filter_combo()

    def _ensure_card(
        self, device_id: str, shop_cfg_path: str, device_type: str = "wifi"
    ) -> DeviceCard:
        if device_id not in self._cards:
            shop_name = Path(shop_cfg_path).stem if shop_cfg_path else "（未绑定）"
            card = DeviceCard(device_id, shop_name, device_type)
            card.start_clicked.connect(self._on_start)
            card.pause_clicked.connect(self._on_pause)
            card.stop_clicked.connect(self._on_stop)
            card.test_clicked.connect(self._on_test_connection)
            self._cards[device_id] = card
            self._cards_lay.addWidget(card)
        return self._cards[device_id]

    def _update_mid_stack(self) -> None:
        """无设备时显示引导，有设备时显示卡片区。"""
        has_devices = bool(self._manager.all_records())
        self._mid_stack.setCurrentIndex(1 if has_devices else 0)

    def _on_add_device(self) -> None:
        dlg = AddDeviceDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        device_id, dtype, shop_cfg = dlg.result_values()
        if not device_id:
            QMessageBox.warning(self, "填写不完整", "请填写设备地址（如 127.0.0.1:5555）")
            return
        if self._manager.get_record(device_id):
            QMessageBox.information(self, "已存在", f"设备 {device_id} 已在列表中")
            return
        self._manager.add_device(device_id, dtype, shop_cfg)
        self._manager.save_devices_config()
        self._device_list.addItem(device_id)
        self._ensure_card(device_id, shop_cfg, dtype)
        self._update_mid_stack()
        self._update_filter_combo()
        self._log_device(device_id, f"设备已添加：{device_id}（{dtype}）")

    @staticmethod
    def _strip_icon_prefix(text: str) -> str:
        """从列表项文本中去掉前导状态图标，得到原始 device_id。"""
        return text.split(" ", 1)[-1].strip() if text and text[0] in "🟢🔵⏸🟡❌⚪" else text

    def _on_remove_device(self) -> None:
        item = self._device_list.currentItem()
        if not item:
            QMessageBox.information(self, "未选中", "请先在左侧点击要移除的设备")
            return
        device_id = self._strip_icon_prefix(item.text())
        if QMessageBox.question(
            self, "确认移除",
            f"确定移除设备「{device_id}」？\n正在进行的接待会立即停止。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._manager.stop_device(device_id)
        self._manager.remove_device(device_id)
        self._manager.save_devices_config()
        row = self._device_list.row(item)
        self._device_list.takeItem(row)
        card = self._cards.pop(device_id, None)
        if card:
            self._cards_lay.removeWidget(card)
            card.deleteLater()
        self._update_mid_stack()
        self._update_filter_combo()

    def _on_scan_devices(self) -> None:
        """扫描可用设备 → 直接给「一键添加」按钮，省一次操作。"""
        found = self._manager.list_available_devices()
        if not found:
            QMessageBox.information(
                self, "🔍 扫描结果",
                "未发现任何 ADB 设备。\n\n"
                "请检查：\n"
                "  • 雷电模拟器是否已打开（ADB 端口 5555）\n"
                "  • USB 手机是否已开启「开发者选项 → USB 调试」\n"
                "  • WiFi 手机是否已执行 adb tcpip 5555 命令\n\n"
                "也可以直接点「＋ 添加设备」手动填写地址。",
            )
            return

        # 弹出扫描结果对话框，每行带「添加」按钮
        dlg = QDialog(self)
        dlg.setWindowTitle("🔍 扫描结果")
        dlg.setMinimumWidth(460)
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(8)

        hdr = QLabel(f"发现 <b>{len(found)}</b> 台 ADB 设备。点对应的「添加」按钮即可绑定店铺：")
        hdr.setTextFormat(Qt.TextFormat.RichText)
        hdr.setWordWrap(True)
        vbox.addWidget(hdr)

        type_cn = {"emulator": "雷电模拟器", "wifi": "WiFi 真机", "usb": "USB 真机"}
        for d in found:
            row_w = QFrame()
            row_w.setStyleSheet(
                "QFrame{background:#1A2A3A;border:1px solid #2A3A50;border-radius:4px;}"
            )
            row = QHBoxLayout(row_w)
            row.setContentsMargins(10, 6, 10, 6)

            already = self._manager.get_record(d["device_id"]) is not None
            label_html = (
                f"<b style='color:#FFF;'>{d['device_id']}</b>　"
                f"<span style='color:#888;'>· {type_cn.get(d['type'], d['type'])}</span>"
            )
            if already:
                label_html += "　<span style='color:#888;font-size:11px;'>（已添加）</span>"
            lbl = QLabel(label_html)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(lbl, stretch=1)

            btn = QPushButton("＋ 添加并绑定店铺")
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                "QPushButton{background:#2980B9;color:#fff;border-radius:4px;"
                "padding:0 12px;font-size:12px;}"
                "QPushButton:hover{background:#3498DB;}"
                "QPushButton:disabled{background:#444;color:#888;}"
            )
            btn.setEnabled(not already)
            btn.clicked.connect(
                lambda _checked=False, dev=d, dl=dlg: (
                    dl.accept(),
                    self._open_add_device_prefilled(dev["device_id"], dev["type"]),
                )
            )
            row.addWidget(btn)
            vbox.addWidget(row_w)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(dlg.reject)
        vbox.addWidget(close)
        dlg.exec()

    def _open_add_device_prefilled(self, device_id: str, dtype: str) -> None:
        """从扫描结果直接打开「添加设备」对话框，预填地址和类型。"""
        dlg = AddDeviceDialog(self)
        # 预填类型（顺序：wifi / emulator / usb）
        type_index = {"wifi": 0, "emulator": 1, "usb": 2}.get(dtype, 0)
        dlg._combo_type.setCurrentIndex(type_index)
        dlg._edit_id.setText(device_id)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        device_id2, dtype2, shop_cfg = dlg.result_values()
        if self._manager.get_record(device_id2):
            QMessageBox.information(self, "已存在", f"设备 {device_id2} 已在列表中")
            return
        self._manager.add_device(device_id2, dtype2, shop_cfg)
        self._manager.save_devices_config()
        self._device_list.addItem(device_id2)
        self._ensure_card(device_id2, shop_cfg, dtype2)
        self._update_mid_stack()
        self._update_filter_combo()
        self._refresh_device_list_icons()
        self._log_device(device_id2, f"设备已添加：{device_id2}（{dtype2}）")

    # -----------------------------------------------------------------------
    # 接待控制（开始 / 暂停 / 停止）
    # -----------------------------------------------------------------------

    def _on_start(self, device_id: str) -> None:
        rec = self._manager.get_record(device_id)
        if rec is None:
            return

        if rec.state in (DeviceState.DISCONNECTED, DeviceState.ERROR):
            self._log_device(device_id, "正在连接设备，请稍候…")
            ok = self._manager.connect_device(device_id)
            if not ok:
                QMessageBox.warning(
                    self, "连接失败",
                    f"无法连接到设备 {device_id}。\n\n"
                    "请确认：\n"
                    "  • 雷电模拟器已打开且端口号正确\n"
                    "  • 手机已开启无线调试 / USB 调试\n"
                    "  • adb 工具已安装并在 PATH 中",
                )
                self._log_device(device_id, "❌ 连接失败，请查看上方提示")
                return
            self._log_device(device_id, "✅ 设备连接成功")

        if rec.bridge is None:
            shop_path = Path(rec.shop_cfg_path) if rec.shop_cfg_path else None
            if not shop_path or not shop_path.exists():
                QMessageBox.warning(
                    self, "店铺配置缺失",
                    f"找不到店铺配置文件：\n{rec.shop_cfg_path}\n\n"
                    "请右键点击设备 → 重新绑定店铺，或在「设置中心」创建配置文件。",
                )
                self._log_device(device_id, f"❌ 店铺配置不存在：{rec.shop_cfg_path}")
                return
            from apps.mobile.orchestrator.mobile_brain_bridge import MobileBrainBridge
            rec.bridge = MobileBrainBridge(
                adapter=rec.adapter,
                shop_cfg_path=shop_path,
                log_fn=lambda m, did=device_id: self._log_device(did, m),
            )

        if self._manager.start_device(device_id, rec.bridge):
            self._log_device(device_id, "▶ 接待循环已启动，正在监控买家消息…")
        else:
            self._log_device(device_id, "❌ 启动失败，设备状态异常")

    def _on_pause(self, device_id: str) -> None:
        self._manager.pause_device(device_id)
        self._log_device(device_id, "⏸ 接待已暂停（设备连接保持）")

    def _on_stop(self, device_id: str) -> None:
        self._manager.stop_device(device_id)
        self._log_device(device_id, "⏹ 接待已停止，设备已断开")
        rec = self._manager.get_record(device_id)
        if rec:
            rec.bridge = None

    # -----------------------------------------------------------------------
    # 全局操作（一键暂停 / 恢复 / 测试连接）
    # -----------------------------------------------------------------------

    def _on_pause_all(self) -> None:
        """统计栏「⏸ 全部暂停」按钮：暂停所有 RUNNING 设备。"""
        paused = 0
        for rec in self._manager.all_records():
            if rec.state == DeviceState.RUNNING:
                self._manager.pause_device(rec.device_id)
                self._log_device(rec.device_id, "⏸ 已暂停（一键全部暂停）")
                paused += 1
        if paused == 0:
            QMessageBox.information(self, "无需暂停", "当前没有正在接待的设备。")
        else:
            QMessageBox.information(
                self, "✅ 已暂停",
                f"已暂停 {paused} 台设备。\n点「▶ 全部恢复」可一次性继续接待。"
            )

    def _on_resume_all(self) -> None:
        """统计栏「▶ 全部恢复」按钮：恢复所有 PAUSED 设备。"""
        resumed = 0
        for rec in self._manager.all_records():
            if rec.state == DeviceState.PAUSED and rec.bridge:
                self._manager.start_device(rec.device_id, rec.bridge)
                self._log_device(rec.device_id, "▶ 已恢复（一键全部恢复）")
                resumed += 1
        if resumed == 0:
            QMessageBox.information(self, "无需恢复", "当前没有已暂停的设备。")

    def _on_test_connection(self, device_id: str) -> None:
        """设备卡片「🔧 测试连接」按钮：主动检测 adapter.is_alive()。"""
        rec = self._manager.get_record(device_id)
        if rec is None:
            return
        if rec.adapter is None:
            QMessageBox.information(
                self, "未连接",
                f"设备 {device_id} 还未连接。\n请先点「▶ 开始接待」。"
            )
            return
        self._log_device(device_id, "🔧 正在测试连接…")
        try:
            alive = rec.adapter.is_alive()
        except Exception as exc:
            alive = False
            self._log_device(device_id, f"🔧 测试异常：{exc!r}")
        if alive:
            QMessageBox.information(
                self, "✅ 连接正常",
                f"设备 {device_id} 响应正常，ADB 连接健康。"
            )
            self._log_device(device_id, "🔧 ✅ 连接健康")
        else:
            QMessageBox.warning(
                self, "❌ 连接异常",
                f"设备 {device_id} 无响应。\n\n"
                "可能原因：\n"
                "  • 模拟器/手机已关机或断开 WiFi\n"
                "  • ADB 服务异常（命令行运行 adb kill-server && adb start-server）\n"
                "  • 千牛 App 被系统杀掉\n\n"
                "建议：点「⏹ 停止接待」→「▶ 开始接待」重连。"
            )
            self._log_device(device_id, "🔧 ❌ 连接异常，请重启 ADB 或重连")

    def _copy_web_url(self) -> None:
        """复制局域网仪表盘 URL 到剪贴板。"""
        from PyQt6.QtWidgets import QApplication
        text = self._lbl_web_url.text()
        # 标签文本可能是 "手机浏览器访问：\nhttp://..."，提取 URL 行
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("http"):
                QApplication.clipboard().setText(line)
                QMessageBox.information(
                    self, "📋 已复制",
                    f"地址已复制到剪贴板：\n{line}\n\n"
                    "建议通过微信/钉钉粘贴到手机后打开。"
                )
                return
        QMessageBox.information(
            self, "尚未启动", "请先启动局域网监控仪表盘。"
        )

    # -----------------------------------------------------------------------
    # 定时刷新
    # -----------------------------------------------------------------------

    def _refresh_all(self) -> None:
        if not self.isVisible():
            return
        total = active = errors = 0
        screenshot_candidates: list[Any] = []

        for rec in self._manager.all_records():
            total += rec.today_count
            if rec.state == DeviceState.RUNNING:
                active += 1
            elif rec.state == DeviceState.ERROR:
                errors += 1
            card = self._cards.get(rec.device_id)
            if card:
                card.update_state(
                    rec.state,
                    rec.today_count,
                    rec.last_trigger_at,
                    rec.error_msg,
                    rec.today_error_count,
                )
                # 只收集候选，截图在 _refresh_all 之外错峰执行
                if rec.state in (DeviceState.RUNNING, DeviceState.CONNECTED) and rec.adapter:
                    screenshot_candidates.append(rec)

        self._summary.refresh(total, active, errors)
        self._refresh_device_list_icons()

        # 每台设备错峰 600ms 拉截图，避免主线程集中阻塞
        for idx, rec in enumerate(screenshot_candidates):
            QTimer.singleShot(idx * 600, lambda r=rec: self._refresh_one_screenshot(r))

    def _refresh_one_screenshot(self, rec: Any) -> None:
        """拉取单台设备截图并更新缩略图。错峰由 _refresh_all 的 singleShot 驱动。"""
        if not self.isVisible():
            return
        card = self._cards.get(rec.device_id)
        if card is None:
            return
        try:
            fn = getattr(rec.adapter, "screenshot_bytes", None)
            if fn:
                img = fn()
                if img:
                    card.update_screenshot(img)
        except Exception:
            pass

    def _refresh_device_list_icons(self) -> None:
        """左侧设备列表项加状态前缀，让用户一眼看出哪台在跑/哪台异常。"""
        icon_map = {
            DeviceState.RUNNING:      "🟢",
            DeviceState.CONNECTED:    "🔵",
            DeviceState.PAUSED:       "⏸",
            DeviceState.CONNECTING:   "🟡",
            DeviceState.ERROR:        "❌",
            DeviceState.DISCONNECTED: "⚪",
        }
        recs_by_id = {r.device_id: r for r in self._manager.all_records()}
        for i in range(self._device_list.count()):
            item = self._device_list.item(i)
            if item is None:
                continue
            raw = item.text()
            # 旧文本可能已带 emoji 前缀，剥掉首字符如果是图标
            dev_id = raw.split(" ", 1)[-1].strip() if raw and raw[0] in "🟢🔵⏸🟡❌⚪" else raw
            rec = recs_by_id.get(dev_id)
            if rec:
                icon = icon_map.get(rec.state, "⚪")
                new_text = f"{icon} {dev_id}"
                if new_text != raw:
                    item.setText(new_text)

    # -----------------------------------------------------------------------
    # 日志（线程安全）
    # -----------------------------------------------------------------------

    def _log_device(self, device_id: str, msg: str) -> None:
        _log.info("[%s] %s", device_id, msg)
        ts   = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{device_id}] {msg}"

        buf = self._log_buf.setdefault(device_id, [])
        buf.append(line)
        if len(buf) > 600:
            self._log_buf[device_id] = buf[-500:]

        # 记录最近消息（简单筛选有意义的条目）
        if "买家" in msg or "brain" in msg or "SEND" in msg or "接待" in msg:
            self._recent_msgs.append(
                {"time": ts, "device": device_id, "text": msg[:80]}
            )
            if len(self._recent_msgs) > 200:
                self._recent_msgs = self._recent_msgs[-150:]

        self._log_signal.emit(line)

    @staticmethod
    def _classify_log_color(line: str) -> str | None:
        """根据日志内容返回颜色（None = 默认色）。"""
        if "❌" in line or "失败" in line or "异常" in line or "ERROR" in line.upper():
            return "#E74C3C"   # 红
        if "⚠" in line or "警告" in line or "WARN" in line.upper():
            return "#F39C12"   # 橙
        if "✅" in line or "成功" in line or "已启动" in line or "已恢复" in line:
            return "#2ECC71"   # 绿
        if "🧪" in line:
            return "#8FBC4A"   # 试运行（嫩绿）
        return None

    @pyqtSlot(str)
    def _append_log_line(self, line: str) -> None:
        sel = self._combo_filter.currentText()
        if sel == "全部设备" or f"[{sel}]" in line:
            color = self._classify_log_color(line)
            if color:
                # QPlainTextEdit 支持 appendHtml；escape 一下避免 HTML 注入
                from html import escape as _esc
                self._log_view.appendHtml(
                    f"<span style='color:{color};'>{_esc(line)}</span>"
                )
            else:
                self._log_view.appendPlainText(line)
            sb = self._log_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _apply_log_filter(self, _: str) -> None:
        self._log_view.clear()
        sel = self._combo_filter.currentText()
        if sel == "全部设备":
            all_lines: list[str] = []
            for lines in self._log_buf.values():
                all_lines.extend(lines)
            all_lines.sort()
            self._log_view.setPlainText("\n".join(all_lines[-300:]))
        else:
            self._log_view.setPlainText(
                "\n".join(self._log_buf.get(sel, [])[-300:])
            )

    def _log_view_clear(self) -> None:
        self._log_view.clear()

    def _update_filter_combo(self) -> None:
        cur = self._combo_filter.currentText()
        self._combo_filter.blockSignals(True)
        self._combo_filter.clear()
        self._combo_filter.addItem("全部设备")
        for rec in self._manager.all_records():
            self._combo_filter.addItem(rec.device_id)
        idx = self._combo_filter.findText(cur)
        self._combo_filter.setCurrentIndex(max(idx, 0))
        self._combo_filter.blockSignals(False)

    # -----------------------------------------------------------------------
    # 局域网仪表盘
    # -----------------------------------------------------------------------

    def _toggle_web_dashboard(self) -> None:
        if self._web_proc and self._web_proc.poll() is None:
            # 已在运行 → 停止
            self._web_proc.terminate()
            self._web_proc = None
            self._btn_web.setText("🚀  启动局域网监控")
            self._lbl_web_url.setText("（已停止）")
            self._btn_copy_url.setEnabled(False)
            _log.info("Web 仪表盘已停止")
        else:
            # 未运行 → 启动
            try:
                self._web_proc = subprocess.Popen(
                    [sys.executable, "-m", "apps.web_dashboard"],
                    cwd=str(_project_root()),
                )
                ip  = _local_ip()
                url = f"http://{ip}:{_WEB_PORT}"
                self._btn_web.setText("⛔  停止局域网监控")
                self._lbl_web_url.setText(
                    f"手机浏览器访问：\n{url}"
                )
                self._btn_copy_url.setEnabled(True)
                _log.info("Web 仪表盘已启动: %s", url)
                QMessageBox.information(
                    self, "🌐 局域网监控已启动",
                    f"仪表盘已启动！\n\n"
                    f"手机（同 WiFi）浏览器打开：\n{url}\n\n"
                    f"可实时查看接待数据，并一键紧急暂停所有设备。\n\n"
                    f"💡 点「📋 复制访问地址」可一键复制到剪贴板。",
                )
            except Exception as e:
                QMessageBox.warning(self, "启动失败", f"无法启动仪表盘服务：{e}\n\n请确认已安装 fastapi 和 uvicorn。")

    def _check_web_proc(self) -> None:
        if self._web_proc and self._web_proc.poll() is not None:
            # 进程意外退出
            self._web_proc = None
            self._btn_web.setText("🚀  启动局域网监控")
            self._lbl_web_url.setText("（已停止）")
            self._btn_copy_url.setEnabled(False)

    # -----------------------------------------------------------------------
    # 影子模式
    # -----------------------------------------------------------------------

    def _toggle_shadow_mode(self) -> None:
        """
        切换试运行模式（影子模式）。

        为避免运行中 device_loop 的局部 adapter 引用与替换发生竞态，
        切换前先把所有 RUNNING 设备暂停 → 替换 adapter → 恢复。
        """
        from apps.mobile.orchestrator.shadow_mode import ShadowAdapter

        self._shadow_enabled = not self._shadow_enabled

        # 先记下需要恢复的设备（切换完后重启）
        to_resume: list[str] = []
        for rec in self._manager.all_records():
            if rec.state == DeviceState.RUNNING:
                self._manager.pause_device(rec.device_id)
                to_resume.append(rec.device_id)

        if self._shadow_enabled:
            # 把所有设备的 adapter 包上 ShadowAdapter
            for rec in self._manager.all_records():
                if rec.adapter and not isinstance(rec.adapter, ShadowAdapter):
                    self._real_adapters[rec.device_id] = rec.adapter
                    shadow = ShadowAdapter(rec.adapter)
                    rec.adapter = shadow
                    if rec.bridge:
                        rec.bridge._adapter = shadow   # 同步更新 bridge
            self._btn_shadow.setText("⏹  关闭试运行（恢复真实发送）")
            self._btn_shadow.setStyleSheet(
                "QPushButton{background:#2A3A10;border-radius:4px;color:#8F8;font-size:12px;}"
                "QPushButton:hover{background:#3A5A18;}"
            )
            self._shadow_banner.show()
            QMessageBox.information(
                self, "🧪 试运行已开启",
                "试运行模式已开启！\n\n"
                "系统会正常分析买家消息并生成回复，\n"
                "但 ❌ 不会真实发送给买家（仅记录到日志）。\n\n"
                "确认回复质量满意后，点「关闭试运行」开始正式接待。",
            )
            self._log_device("all", "🧪 试运行已开启：消息不会真实发送，仅记录回复内容")
        else:
            # 恢复真实 adapter
            for rec in self._manager.all_records():
                real = self._real_adapters.pop(rec.device_id, None)
                if real is not None:
                    rec.adapter = real
                    if rec.bridge:
                        rec.bridge._adapter = real
            self._btn_shadow.setText("🧪  开启试运行（不真实发送）")
            self._btn_shadow.setStyleSheet(
                "QPushButton{background:#1A3A2A;border-radius:4px;color:#CCC;font-size:12px;}"
                "QPushButton:hover{background:#28503A;}"
            )
            self._shadow_banner.hide()
            self._log_device("all", "✅ 试运行已关闭：切回真实接待，消息将正常发送")

        # 切换完毕，恢复之前 RUNNING 的设备
        for did in to_resume:
            rec = self._manager.get_record(did)
            if rec and rec.bridge:
                self._manager.start_device(did, rec.bridge)

    def _view_shadow_report(self) -> None:
        from apps.mobile.orchestrator.shadow_diff import generate_report
        report = generate_report(days=7)
        dlg = QDialog(self)
        dlg.setWindowTitle("影子模式测试报告（最近 7 天）")
        dlg.resize(640, 480)
        txt = QPlainTextEdit(dlg)
        txt.setReadOnly(True)
        txt.setPlainText(report)
        txt.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")
        lay = QVBoxLayout(dlg)
        lay.addWidget(txt)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(dlg.reject)
        lay.addWidget(btn)
        dlg.exec()

    # -----------------------------------------------------------------------
    # IPC 状态构建（供 ZMQ 广播 + 文件写入共用）
    # -----------------------------------------------------------------------

    def _build_ipc_state(self) -> dict:
        """构建当前完整状态字典，键与 state_reader.py 的缓存键一致。"""
        recs   = self._manager.all_records()
        total  = sum(r.today_count for r in recs)
        active = sum(1 for r in recs if r.state == DeviceState.RUNNING)
        errors = sum(1 for r in recs if r.state == DeviceState.ERROR)

        overview = {
            "total_today":    total,
            "active_devices": active,
            "error_devices":  errors,
            "paused":         False,
            "updated_at":     datetime.now().isoformat(timespec="seconds"),
        }
        devices = [
            {
                "device_id":    r.device_id,
                "type":         r.device_type,
                "shop":         Path(r.shop_cfg_path).stem if r.shop_cfg_path else "",
                "state":        str(r.state),
                "today_count":  r.today_count,
                "last_trigger": (
                    time.strftime("%H:%M:%S", time.localtime(r.last_trigger_at))
                    if r.last_trigger_at else ""
                ),
                "error_msg":    r.error_msg,
            }
            for r in recs
        ]
        recent = [
            {
                "time":   m["time"],
                "device": m["device"],
                "buyer":  "买家****",   # 脱敏
                "text":   m["text"][:60],
            }
            for m in self._recent_msgs[-50:]
        ]
        return {"overview": overview, "devices": devices, "recent_msgs": recent}

    # -----------------------------------------------------------------------
    # IPC 状态发布（每 2s）：ZMQ 广播 + 文件写入（降级/备份）
    # -----------------------------------------------------------------------

    def _write_ipc_state(self) -> None:
        try:
            state = self._build_ipc_state()

            # ZMQ 广播（首选，仪表盘实时接收）
            self._zmq_server.publish(state)

            # 文件写入（降级兼容 / 历史记录）
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            (_STATE_DIR / "overview.json").write_text(
                json.dumps(state["overview"], ensure_ascii=False), encoding="utf-8"
            )
            (_STATE_DIR / "devices.json").write_text(
                json.dumps(state["devices"], ensure_ascii=False), encoding="utf-8"
            )
            (_STATE_DIR / "recent_msgs.json").write_text(
                json.dumps(state["recent_msgs"], ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            _log.debug("IPC 写入失败: %r", exc)

    # -----------------------------------------------------------------------
    # 命令消费（每 1s）：ZMQ 队列（主路） + 文件控制信号（降级）
    # -----------------------------------------------------------------------

    def _handle_control_action(self, action: str) -> None:
        """执行控制命令（在 Qt 主线程调用）。"""
        if action == "pause_all":
            for rec in self._manager.all_records():
                if rec.state == DeviceState.RUNNING:
                    self._manager.pause_device(rec.device_id)
                    self._log_device(rec.device_id, "⚠️ 收到紧急暂停指令（来自仪表盘）")
        elif action == "resume_all":
            for rec in self._manager.all_records():
                if rec.state == DeviceState.PAUSED and rec.bridge:
                    self._manager.start_device(rec.device_id, rec.bridge)
                    self._log_device(rec.device_id, "▶ 收到恢复指令（来自仪表盘）")

    def _drain_cmd_queue(self) -> None:
        """消费 ZMQ REP 线程放入的控制命令（Qt 主线程安全）。"""
        # 主路：ZMQ 命令队列
        while True:
            try:
                action = self._cmd_queue.get_nowait()
                self._handle_control_action(action)
            except queue.Empty:
                break

        # 降级：文件控制信号（ZMQ 未连接时仪表盘写文件）
        sig_file = _STATE_DIR / "control_signal.json"
        if not sig_file.exists():
            return
        try:
            data   = json.loads(sig_file.read_text(encoding="utf-8"))
            action = data.get("action", "none")
            if action in ("pause_all", "resume_all"):
                self._handle_control_action(action)
                sig_file.write_text(json.dumps({"action": "none"}), encoding="utf-8")
        except Exception:
            pass
