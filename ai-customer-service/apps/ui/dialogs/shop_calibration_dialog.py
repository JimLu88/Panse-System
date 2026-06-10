"""
屏幕取点写入店铺 YAML：倒计时 10 秒内第一次左键点击即写入（程序改文件，无需用户编辑配置）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from apps.core.capture.screen import Rect
from apps.core.channels.qianniu.driver import Point
from apps.core.configs.loader import ShopConfig, load_shop_config
from apps.core.configs.shop_yaml_calibration import apply_click_calibration

CAPTURE_SECONDS = 10


def _fmt_point(p: Point | None, *, optional: bool = False) -> str:
    if p is None:
        return "未配置" + ("（本项可选）" if optional else "")
    if int(p.x) == 0 and int(p.y) == 0:
        return "尚无有效坐标（当前为 0,0，请取点）"
    return f"<b>({p.x}, {p.y})</b>"


def _fmt_rect(r: Rect | None) -> str:
    if r is None:
        return "尚未配置矩形"
    w, h = r.width(), r.height()
    if w <= 0 or h <= 0:
        return (
            f"矩形未录完整：left={r.left} top={r.top} right={r.right} bottom={r.bottom} "
            f"（需先左上再右下，使宽高为正）"
        )
    return (
        f"left={r.left} top={r.top} right={r.right} bottom={r.bottom} "
        f"<small>（宽 {w} × 高 {h}）</small>"
    )


def _saved_snapshot_html(tid: str, shop: ShopConfig) -> str:
    """当前下拉选中项在 YAML 里已落盘的值（与最近一次取点动态无关）。"""
    qn = shop.qianniu
    parts: list[str] = []

    def need_qn() -> bool:
        if qn is None:
            parts.append("<span style='color:#c0392b'>本文件缺少 <code>qianniu</code> 配置块。</span>")
            return False
        return True

    if tid == "input_box_point":
        parts.append("<b>输入框中心</b>（屏幕像素）")
        if need_qn():
            parts.append(_fmt_point(qn.input_box_point, optional=False))
    elif tid == "send_button_point":
        parts.append("<b>发送按钮</b>（未配置时程序用 Enter 发送）")
        if need_qn():
            parts.append(_fmt_point(qn.send_button_point, optional=True))
    elif tid == "chat_scroll_point":
        parts.append("<b>聊天记录滚动位置</b>")
        if need_qn():
            parts.append(_fmt_point(qn.chat_scroll_point, optional=True))
    elif tid == "taskbar_icon_point":
        parts.append("<b>任务栏本店图标</b>")
        if need_qn():
            parts.append(_fmt_point(qn.taskbar_icon_point, optional=True))
    elif tid in ("ocr_chat_tl", "ocr_chat_br"):
        parts.append("<b>聊天区域 OCR 矩形</b>（<code>ocr_chat_rect</code>，两项共用同一矩形）")
        parts.append(_fmt_rect(shop.ocr_chat_rect))
        parts.append(
            "<small>当前下拉为「"
            + ("左上角" if tid == "ocr_chat_tl" else "右下角")
            + "」：请确保矩形已覆盖整段聊天内容区。</small>"
        )
    elif tid in ("ocr_right_tl", "ocr_right_br"):
        parts.append("<b>右侧区域 OCR 矩形</b>（<code>ocr_right_rect</code>）")
        parts.append(_fmt_rect(shop.ocr_right_rect))
        parts.append(
            "<small>当前为「" + ("左上角" if tid == "ocr_right_tl" else "右下角") + "」校准项。</small>"
        )
    elif tid in ("session_list_tl", "session_list_br"):
        parts.append("<b>左侧会话列表 ROI</b>（<code>qianniu.session_list_rect</code>）")
        if need_qn():
            parts.append(_fmt_rect(qn.session_list_rect))
            parts.append(
                "<small>当前为「"
                + ("左上角" if tid == "session_list_tl" else "右下角")
                + "」校准项。</small>"
            )
    elif tid in ("restore_title_tl", "restore_title_br"):
        parts.append("<b>恢复后标题栏 OCR 矩形</b>（<code>restore_title_ocr_rect</code>）")
        if need_qn():
            parts.append(_fmt_rect(qn.restore_title_ocr_rect))
            parts.append(
                "<small>当前为「"
                + ("左上角" if tid == "restore_title_tl" else "右下角")
                + "」校准项。</small>"
            )
    elif tid in ("buyer_nick_tl", "buyer_nick_br"):
        parts.append("<b>买家昵称区域</b>（<code>ocr_buyer_nick_rect</code>，发送前身份校验用）")
        parts.append(_fmt_rect(shop.ocr_buyer_nick_rect))
        parts.append(
            "<small>当前为「"
            + ("左上角" if tid == "buyer_nick_tl" else "右下角")
            + "」校准项。此项可选——未配置时跳过发送前昵称校验。</small>"
        )
    else:
        parts.append(f"<span style='color:#c0392b'>未知字段：{tid}</span>")

    return "<div style='line-height:1.5'>" + "<br/>".join(parts) + "</div>"

_TARGET_CHOICES: list[tuple[str, str]] = [
    ("输入框中心（点击输入框中间）", "input_box_point"),
    ("发送按钮", "send_button_point"),
    ("聊天记录滚动位置", "chat_scroll_point"),
    ("聊天区域 OCR — 左上角（建议先取此项）", "ocr_chat_tl"),
    ("聊天区域 OCR — 右下角（再取此项）", "ocr_chat_br"),
    ("右侧区域 OCR — 左上角", "ocr_right_tl"),
    ("右侧区域 OCR — 右下角", "ocr_right_br"),
    ("左侧会话列表 — 左上角（未读切换 ROI，先此项）", "session_list_tl"),
    ("左侧会话列表 — 右下角", "session_list_br"),
    (
        "任务栏本店图标（叮咚恢复用；槽位顺序可改，改排序后请用此项重取点）",
        "taskbar_icon_point",
    ),
    ("恢复后标题栏 OCR — 左上（区分多店窗口）", "restore_title_tl"),
    ("恢复后标题栏 OCR — 右下", "restore_title_br"),
    ("买家昵称区域 — 左上（可选，发送前防错发）", "buyer_nick_tl"),
    ("买家昵称区域 — 右下", "buyer_nick_br"),
]


def _sep():
    """水平分隔线。"""
    from PyQt6.QtWidgets import QFrame
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


class _AutoCalibrateThread(QThread):
    """后台线程运行自动识别，避免卡 UI。"""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # AutoCalibrateResult
    finished_err = pyqtSignal(str)

    def __init__(self, settings=None, yaml_path=None) -> None:
        super().__init__()
        self._settings = settings
        self._yaml_path = yaml_path

    def run(self) -> None:
        try:
            from apps.core.automation.auto_calibrate import run_auto_calibrate
            result = run_auto_calibrate(
                settings=self._settings,
                progress_cb=lambda msg: self.progress.emit(msg),
                shop_yaml_path=self._yaml_path,
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.finished_err.emit(str(e))


class _MouseCaptureThread(QThread):
    """在最多 CAPTURE_SECONDS 秒内捕获第一次屏幕左键按下（屏幕坐标）。"""

    captured = pyqtSignal(int, int)
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._listener = None

    def run(self) -> None:
        try:
            from pynput import mouse
        except ImportError:
            self.failed.emit("未安装 pynput，请执行：pip install pynput")
            return

        ev = threading.Event()
        xy = [0, 0]

        def on_click(x: float, y: float, button: object, pressed: bool) -> bool | None:
            if pressed and button == mouse.Button.left:
                xy[0] = int(x)
                xy[1] = int(y)
                ev.set()
                return False
            return None

        listener = mouse.Listener(on_click=on_click)
        self._listener = listener
        listener.start()
        deadline = time.monotonic() + float(CAPTURE_SECONDS)
        try:
            while time.monotonic() < deadline:
                if self.isInterruptionRequested():
                    self.failed.emit("")
                    return
                if ev.wait(timeout=0.05):
                    self.captured.emit(xy[0], xy[1])
                    return
            self.failed.emit(
                f"{CAPTURE_SECONDS} 秒内未检测到左键点击。请点「开始」后，在倒计时内在目标位置单击。"
            )
        finally:
            try:
                listener.stop()
            except Exception:
                pass
            self._listener = None


class ShopCalibrationDialog(QDialog):
    def __init__(self, parent, yaml_path: Path) -> None:
        super().__init__(parent)
        self._path = yaml_path
        self._thread: _MouseCaptureThread | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_timer_tick)
        self._remaining = 0
        self._last_save: tuple[str, int, int] | None = None

        self.setWindowTitle("千牛屏幕坐标校准")
        self.resize(560, 560)

        root = QVBoxLayout(self)

        self.lbl_file = QLabel(f"配置文件：<code>{yaml_path.name}</code>")
        self.lbl_file.setToolTip(str(yaml_path.resolve()))
        self.lbl_file.setWordWrap(True)
        root.addWidget(self.lbl_file)

        # ── 一：自动识别区域 ────────────────────────────────────────────────
        box_auto = QGroupBox("一、自动识别（推荐）")
        la = QVBoxLayout(box_auto)
        la.addWidget(QLabel(
            "<small>千牛已打开且界面完整可见后点此。程序截屏后自动找到各坐标，弹出预览确认再写入。</small>"
        ))
        self.btn_auto_calibrate = QPushButton("🔍 开始自动识别坐标")
        self.btn_auto_calibrate.setMinimumHeight(44)
        self.btn_auto_calibrate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto_calibrate.clicked.connect(self._on_auto_calibrate)
        la.addWidget(self.btn_auto_calibrate)
        self.lbl_auto_status = QLabel("<small>尚未运行</small>")
        self.lbl_auto_status.setWordWrap(True)
        la.addWidget(self.lbl_auto_status)
        root.addWidget(box_auto)

        # ── 二：手动逐项校准区域 ────────────────────────────────────────────
        box_manual = QGroupBox("二、手动逐项校准（自动识别不准时用）")
        lm = QVBoxLayout(box_manual)
        lm.addWidget(QLabel(
            "<small>① 在下拉框选好要写入的字段；"
            "② 点「开始 10 秒倒计时」；"
            "③ 倒计时内在千牛目标位置<b>左键单击一次</b>，坐标立即写入。</small>"
        ))
        form_m = QFormLayout()
        self.combo_target = QComboBox()
        for label, tid in _TARGET_CHOICES:
            self.combo_target.addItem(label, tid)
        form_m.addRow("写入字段", self.combo_target)
        self.lbl_saved_snapshot = QLabel()
        self.lbl_saved_snapshot.setWordWrap(True)
        self.lbl_saved_snapshot.setTextFormat(Qt.TextFormat.RichText)
        form_m.addRow("当前值", self.lbl_saved_snapshot)
        self.combo_target.currentIndexChanged.connect(self._on_target_changed)
        lm.addLayout(form_m)

        row_run = QHBoxLayout()
        self.btn_start = QPushButton(f"开始 {CAPTURE_SECONDS} 秒倒计时并取点")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self._on_start)
        row_run.addWidget(self.btn_start, 1)
        self.btn_save = QPushButton("再保存一次")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.setEnabled(False)
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setToolTip("把上一次取到的坐标再写入一次（通常不需要）。")
        self.btn_save.clicked.connect(self._on_save_again)
        row_run.addWidget(self.btn_save)
        lm.addLayout(row_run)

        self.lbl_countdown = QLabel("")
        self.lbl_countdown.setWordWrap(True)
        lm.addWidget(self.lbl_countdown)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        lm.addWidget(self.lbl_result)
        root.addWidget(box_manual)

        # ── 底部按钮 ────────────────────────────────────────────────────────
        row_open = QHBoxLayout()
        self.btn_open_dir = QPushButton("打开配置文件夹")
        self.btn_open_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_dir.clicked.connect(self._on_open_config_dir)
        row_open.addWidget(self.btn_open_dir)
        row_open.addStretch(1)
        root.addLayout(row_open)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_saved_snapshot()

    def _refresh_saved_snapshot(self) -> None:
        tid = self.combo_target.currentData()
        if not isinstance(tid, str):
            self.lbl_saved_snapshot.setText("")
            return
        try:
            shop = load_shop_config(self._path)
        except Exception as e:
            self.lbl_saved_snapshot.setText(
                f"<span style='color:#c0392b'>无法读取配置：{e!s}</span>"
            )
            return
        self.lbl_saved_snapshot.setText(_saved_snapshot_html(tid, shop))

    def _on_target_changed(self, _idx: int) -> None:
        self._last_save = None
        self.btn_save.setEnabled(False)
        self._refresh_saved_snapshot()

    def _on_save_again(self) -> None:
        if self._last_save is None:
            QMessageBox.information(self, "保存", "请先在倒计时内成功取点一次，再使用「保存」。")
            return
        tid, x, y = self._last_save
        try:
            apply_click_calibration(self._path, tid, x, y)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        QMessageBox.information(
            self,
            "已保存",
            f"已将「{tid}」坐标 ({x}, {y}) 再次写入文件：\n{self._path.name}",
        )
        self._refresh_saved_snapshot()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._abort_capture()
        super().closeEvent(event)

    def _abort_capture(self) -> None:
        self._timer.stop()
        if self._thread is not None:
            self._thread.requestInterruption()
            self._thread.wait(4000)
            self._thread = None
        self.btn_start.setEnabled(True)

    def _on_auto_calibrate(self) -> None:
        """一键自动识别：截全屏→(anchor 预测优先)→预览→确认写入。"""
        from apps.core.configs.base_settings import load_base_settings

        # ── v1.6.3：锁定窗口没动 → 组件坐标也不会变，重校多余，弹窗确认 ──
        if not self._confirm_recalibrate_if_window_unchanged():
            return

        self.btn_auto_calibrate.setEnabled(False)
        self.lbl_auto_status.setText("<small>自动识别运行中，请稍候…</small>")
        QApplication.processEvents()

        self._auto_thread = _AutoCalibrateThread(
            settings=load_base_settings(), yaml_path=self._path,
        )
        self._auto_thread.progress.connect(
            lambda msg: self.lbl_auto_status.setText(f"<small>{msg}</small>")
        )
        self._auto_thread.finished_ok.connect(self._on_auto_done)
        self._auto_thread.finished_err.connect(self._on_auto_error)
        self._auto_thread.start()

    def _confirm_recalibrate_if_window_unchanged(self) -> bool:
        """锁定窗口与上次 anchor 采集时一致 → 弹窗确认是否真要重校。
        返回 True=继续校准；False=用户取消。无 anchor/定位不到窗口时直接返回 True。"""
        try:
            from apps.core.automation.anchor_calibrate import (
                from_yaml_dict, window_unchanged,
            )
            from apps.core.automation.auto_calibrate import _locate_qianniu_window_uia
            from apps.core.configs.shop_yaml_calibration import read_calib_anchor

            anchor = from_yaml_dict(read_calib_anchor(self._path))
            if anchor is None:
                return True  # 首次/无 anchor，正常走满屏搜
            cur = _locate_qianniu_window_uia()
            if not cur:
                return True  # 定位不到窗口，照常校准
            if not window_unchanged(anchor.base_window, cur):
                return True  # 窗口已变，正常重校
        except Exception:
            return True  # 任何异常都不挡用户校准

        ret = QMessageBox.question(
            self, "锁定窗口位置未改变",
            "检测到千牛锁定窗口位置与上次校准时一致（窗口没动过）。\n"
            "既然窗口没变，各组件坐标通常也不会变，重新校准多半是多余的。\n\n"
            "仍要重新校准各窗口坐标吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ret == QMessageBox.StandardButton.Yes

    def _on_auto_error(self, msg: str) -> None:
        self.btn_auto_calibrate.setEnabled(True)
        self.lbl_auto_status.setText(f"<small style='color:red'>识别失败：{msg}</small>")
        QMessageBox.critical(self, "自动识别失败", msg)

    def _on_auto_done(self, result) -> None:
        self.btn_auto_calibrate.setEnabled(True)
        c = result.coords

        # ── 多窗口：先让用户手动点发送按钮确认 ──────────────────────────
        if result.needs_manual_send:
            self.lbl_auto_status.setText(
                f"<small style='color:orange'>检测到 {result.multi_window_count} 个聊天窗口，"
                "请在 10 秒内点击<b>您要接待的那个窗口</b>的「发送」按钮…</small>"
            )
            self._pending_result = result
            self._capture_thread = _MouseCaptureThread()
            self._capture_thread.captured.connect(self._on_manual_send_captured)
            self._capture_thread.failed.connect(self._on_manual_send_failed)
            self._capture_thread.start()
            return

        self._show_result_preview(result)

    def _on_manual_send_captured(self, x: int, y: int) -> None:
        result = self._pending_result
        result.coords.send_button_x = x
        result.coords.send_button_y = y
        # 补全依赖发送按钮的字段
        result.coords.input_box_x = max(0, x - 200)
        result.coords.input_box_y = y
        result.coords.chat_scroll_x = x - 100
        result.coords.chat_scroll_y = y - 200
        if result.coords.ocr_chat_right is None:
            result.coords.ocr_chat_right = x + 20
        if result.coords.ocr_chat_bottom is None:
            result.coords.ocr_chat_bottom = y - 10
        result.notes.append(f"用户手动确认发送按钮：({x}, {y})")
        self.lbl_auto_status.setText(f"<small>已确认发送按钮 ({x}, {y})，请查看预览</small>")
        self._show_result_preview(result)

    def _on_manual_send_failed(self, msg: str) -> None:
        self.lbl_auto_status.setText("<small style='color:red'>未收到点击，已取消</small>")
        if msg:
            QMessageBox.warning(self, "未完成", msg)

    # ── 把识别结果画到截图上，返回带标注的 PNG bytes ────────────────────────
    @staticmethod
    def _render_annotated(result) -> bytes | None:
        """用 PIL 在截图上画出识别到的区域和点位，返回 PNG bytes；失败返回 None。"""
        if not result.screenshot_png:
            return None
        try:
            import io
            from PIL import Image, ImageDraw, ImageFont

            img = Image.open(io.BytesIO(result.screenshot_png)).convert("RGB")
            draw = ImageDraw.Draw(img, "RGBA")
            c = result.coords

            # 矩形区域：半透明填充 + 彩色边框
            def draw_rect(l, t, r, b, color_rgb, label):
                if None in (l, t, r, b):
                    return
                fill = color_rgb + (40,)   # 半透明
                draw.rectangle([l, t, r, b], fill=fill, outline=color_rgb + (220,), width=3)
                # 在左上角写标签
                draw.rectangle([l, t - 22, l + len(label) * 11 + 6, t], fill=color_rgb + (200,))
                draw.text((l + 3, t - 20), label, fill=(255, 255, 255, 255))

            # 点位：彩色圆 + 十字线
            def draw_point(x, y, color_rgb, label):
                if x is None or y is None:
                    return
                r = 14
                draw.ellipse([x - r, y - r, x + r, y + r],
                             fill=color_rgb + (180,), outline=color_rgb + (255,), width=3)
                draw.line([x - 20, y, x + 20, y], fill=color_rgb + (255,), width=2)
                draw.line([x, y - 20, x, y + 20], fill=color_rgb + (255,), width=2)
                draw.rectangle([x + r + 2, y - 12, x + r + 2 + len(label) * 10 + 4, y + 10],
                               fill=(0, 0, 0, 160))
                draw.text((x + r + 4, y - 11), label, fill=(255, 255, 255, 255))

            draw_rect(c.ocr_chat_left,      c.ocr_chat_top,
                      c.ocr_chat_right,     c.ocr_chat_bottom,
                      (255, 80,  80),  "聊天OCR区")
            draw_rect(c.session_list_left,  c.session_list_top,
                      c.session_list_right, c.session_list_bottom,
                      (80,  160, 255), "会话列表")
            draw_point(c.input_box_x,   c.input_box_y,   (50,  205, 50),  "输入框")
            draw_point(c.send_button_x, c.send_button_y, (255, 165,  0),  "发送键")
            draw_point(c.chat_scroll_x, c.chat_scroll_y, (180, 100, 255), "滚动点")
            draw_point(c.taskbar_icon_x, c.taskbar_icon_y, (255, 215, 0), "任务栏")
            draw_point(getattr(c, "service_btn_x", None),
                       getattr(c, "service_btn_y", None), (0, 220, 180), "客服键")
            # 千牛主窗口边界（黄色细虚线，仅参考）
            if c.qianniu_window_left is not None and c.qianniu_window_right is not None:
                draw.rectangle(
                    [c.qianniu_window_left, c.qianniu_window_top,
                     c.qianniu_window_right, c.qianniu_window_bottom],
                    outline=(255, 215, 0, 200), width=2,
                )

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def _show_result_preview(self, result) -> None:
        """守护包装：预览渲染里任何异常都弹错误框，而不是让 PyQt6 静默 abort 整个进程。"""
        try:
            self._show_result_preview_impl(result)
        except Exception as e:
            import traceback
            detail = traceback.format_exc()
            try:
                self.lbl_auto_status.setText(
                    f"<small style='color:red'>预览渲染出错：{e!r}</small>"
                )
            except Exception:
                pass
            QMessageBox.critical(
                self, "预览出错（已防止闪退）",
                f"识别已完成，但预览渲染抛出异常：\n{e!r}\n\n"
                f"坐标可能仍可用，可重试或手动校准。\n\n详细栈：\n{detail[-1500:]}",
            )

    def _show_result_preview_impl(self, result) -> None:
        c = result.coords
        conf_color = {"high": "green", "medium": "orange", "low": "red"}.get(
            result.confidence, "orange")
        self.lbl_auto_status.setText(
            f"<small>识别完成，方法：{result.method}，"
            f"置信度：<b style='color:{conf_color}'>{result.confidence}</b></small>"
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("自动识别结果预览 — 确认后写入")
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.resize(900, 700)
        lay = QVBoxLayout(dlg)

        summary = (
            f"<b>识别方法：</b>{result.method}　"
            f"<b>置信度：</b><span style='color:{conf_color}'>{result.confidence}</span>"
        )
        lay.addWidget(QLabel(summary))

        # v1.6.3：anchor 预测被复检否决、退回满屏搜的字段，红色横幅提醒人工复核
        fallback = list(getattr(c, "calib_fallback_fields", []) or [])
        if fallback:
            warn = QLabel(
                "⚠ 以下字段 anchor 预测被 AI 复检否决，已退回满屏搜（请重点核对）：<br/>"
                f"<b style='color:#c0392b'>{', '.join(fallback)}</b>"
            )
            warn.setTextFormat(Qt.TextFormat.RichText)
            warn.setWordWrap(True)
            lay.addWidget(warn)

        # ── 带标注的截图预览 ─────────────────────────────────────────────
        annotated_png = self._render_annotated(result)
        if annotated_png:
            from PyQt6.QtGui import QPixmap
            from PyQt6.QtWidgets import QScrollArea
            import io as _io

            px = QPixmap()
            px.loadFromData(annotated_png, "PNG")

            # 按宽度缩放到 860px 以适应对话框
            max_w = 860
            if px.width() > max_w:
                px = px.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)

            lbl_img = QLabel()
            lbl_img.setPixmap(px)
            lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)

            scroll = QScrollArea()
            scroll.setWidget(lbl_img)
            scroll.setWidgetResizable(False)
            scroll.setMinimumHeight(320)
            lay.addWidget(scroll)

            legend = ("<small>"
                      "<span style='color:#ff5050'>■ 红框 = 聊天OCR区</span>　"
                      "<span style='color:#50a0ff'>■ 蓝框 = 会话列表</span>　"
                      "<span style='color:#ffd700'>□ 黄框 = 千牛主窗口</span>　"
                      "<span style='color:#32cd32'>● 绿点 = 输入框</span>　"
                      "<span style='color:#ffa500'>● 橙点 = 发送键</span>　"
                      "<span style='color:#b464ff'>● 紫点 = 滚动点</span>　"
                      "<span style='color:#ffd700'>● 金点 = 任务栏图标</span>"
                      "</small>")
            lbl_leg = QLabel(legend)
            lbl_leg.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(lbl_leg)
        else:
            lay.addWidget(QLabel("<small style='color:gray'>截图不可用，仅显示数字坐标</small>"))

        # ── 坐标数字列表 ─────────────────────────────────────────────────
        fields = [
            ("输入框中心",    c.input_box_x,        c.input_box_y,         True),
            ("发送按钮",      c.send_button_x,      c.send_button_y,       True),
            ("聊天区滚动点",  c.chat_scroll_x,      c.chat_scroll_y,       False),
            ("聊天区OCR左上", c.ocr_chat_left,      c.ocr_chat_top,        True),
            ("聊天区OCR右下", c.ocr_chat_right,     c.ocr_chat_bottom,     True),
            ("会话列表左上",  c.session_list_left,  c.session_list_top,    True),
            ("会话列表右下",  c.session_list_right, c.session_list_bottom, True),
            ("任务栏图标",    c.taskbar_icon_x,     c.taskbar_icon_y,      True),
            ("客服按钮",      getattr(c, "service_btn_x", None),
                              getattr(c, "service_btn_y", None),           False),
        ]
        rows_html = "<table cellspacing='4'>"
        for label, x, y, important in fields:
            if x is not None and y is not None:
                color = "inherit"; icon = "✅"
            else:
                color = "#888"; icon = "❌ 未识别"
            mark = " <b>（关键）</b>" if important else ""
            rows_html += (
                f"<tr><td>{icon}</td>"
                f"<td><span style='color:{color}'>{label}{mark}</span></td>"
                f"<td><span style='color:{color}'>"
                + (f"({x}, {y})" if x is not None else "—")
                + "</span></td></tr>"
            )
        rows_html += "</table>"
        lbl_f = QLabel(rows_html)
        lbl_f.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lbl_f)

        lay.addWidget(QLabel("<b>识别日志：</b>"))
        te = QTextEdit()
        te.setReadOnly(True)
        te.setMaximumHeight(80)
        te.setPlainText("\n".join(result.notes))
        lay.addWidget(te)

        if not c.has_critical():
            w = QLabel(
                "<p style='color:orange'>⚠️ 关键字段未完整识别，建议确保千牛界面完整可见后重试。</p>"
            )
            w.setWordWrap(True)
            lay.addWidget(w)

        from PyQt6.QtWidgets import QDialogButtonBox as _BB
        bb = _BB(_BB.StandardButton.Ok | _BB.StandardButton.Cancel)
        bb.button(_BB.StandardButton.Ok).setText("确认写入")
        bb.button(_BB.StandardButton.Cancel).setText("取消")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            from apps.core.automation.auto_calibrate import apply_auto_calibrate_result
            written = apply_auto_calibrate_result(self._path, result)
        except Exception as e:
            QMessageBox.critical(self, "写入失败", str(e))
            return

        QMessageBox.information(
            self, "写入完成",
            f"已写入 {len(written)} 个字段：\n" + "\n".join(f"  • {w}" for w in written)
            + "\n\n建议点「启动全自动」测试效果，效果不佳可再次点「自动识别」重跑。",
        )

    def _refresh_saved_snapshot(self) -> None:
        tid = self.combo_target.currentData()
        if not isinstance(tid, str):
            self.lbl_saved_snapshot.setText("")
            return
        try:
            shop = load_shop_config(self._path)
        except Exception as e:
            self.lbl_saved_snapshot.setText(f"<span style='color:#c0392b'>无法读取：{e!s}</span>")
            return
        self.lbl_saved_snapshot.setText(_saved_snapshot_html(tid, shop))

    def _on_target_changed(self, _idx: int) -> None:
        self._last_save = None
        self.btn_save.setEnabled(False)
        self._refresh_saved_snapshot()

    def _on_save_again(self) -> None:
        if not self._last_save:
            return
        tid, x, y = self._last_save
        try:
            apply_click_calibration(self._path, tid, x, y)
            self.lbl_result.setText(f"已再次写入 <b>{tid}</b>：({x}, {y})")
        except Exception as e:
            QMessageBox.critical(self, "写入失败", str(e))

    def _on_timer_tick(self) -> None:
        self._remaining -= 1
        if self._remaining > 0:
            self.lbl_countdown.setText(f"请左键单击目标位置 — 剩余 <b>{self._remaining}</b> 秒")
        else:
            self.lbl_countdown.setText("倒计时结束。")
            self._timer.stop()

    def _on_start(self) -> None:
        self.lbl_result.clear()
        tid = self.combo_target.currentData()
        if not isinstance(tid, str) or not tid:
            QMessageBox.warning(self, "取点校准", "请先选择要写入的字段。")
            return
        self.btn_start.setEnabled(False)
        self.btn_save.setEnabled(False)
        self._remaining = CAPTURE_SECONDS
        self.lbl_countdown.setText(f"请左键单击目标位置 — 剩余 <b>{self._remaining}</b> 秒")
        self._timer.start()
        self._thread = _MouseCaptureThread()
        self._thread.captured.connect(self._on_captured)
        self._thread.failed.connect(self._on_failed)
        self._thread.finished.connect(lambda: setattr(self, "_thread", None))
        self._thread.start()

    def _on_captured(self, x: int, y: int) -> None:
        self._timer.stop()
        tid = self.combo_target.currentData()
        if not isinstance(tid, str):
            self.btn_start.setEnabled(True)
            return
        try:
            apply_click_calibration(self._path, tid, x, y)
        except Exception as e:
            QMessageBox.critical(self, "写入失败", str(e))
            self.btn_start.setEnabled(True)
            return
        self.lbl_countdown.setText("已捕获。")
        self._last_save = (tid, x, y)
        self.btn_save.setEnabled(True)
        self.lbl_result.setText(f"✅ 已写入 <b>{tid}</b>：({x}, {y})")
        self.btn_start.setEnabled(True)
        self._refresh_saved_snapshot()

    def _on_failed(self, msg: str) -> None:
        self._timer.stop()
        self.btn_start.setEnabled(True)
        self.btn_save.setEnabled(self._last_save is not None)
        if msg:
            QMessageBox.warning(self, "未完成", msg)

    def _on_open_config_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.resolve().parent)))
