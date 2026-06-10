"""敏感回复：预览草稿 + 倒计时，确认后入队发送或中断转人工。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class OutboundReviewDialog(QDialog):
    """主线程模态对话框；on_commit / on_abort_hold 由调用方注入。"""

    def __init__(
        self,
        parent,
        *,
        buyer_preview: str,
        segments: list[str],
        image_items: list[tuple[str, str]],
        default_delay_s: int,
        on_commit: Callable[[], None],
        on_abort_hold: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("敏感回复预览（子弹时间）")
        self.setMinimumSize(560, 480)
        self._on_commit = on_commit
        self._on_abort_hold = on_abort_hold
        self.was_sent = False
        self._abort_handled = False

        lay = QVBoxLayout(self)

        g1 = QGroupBox("客户原话摘要")
        lay.addWidget(g1)
        t1 = QPlainTextEdit((buyer_preview or "").strip() or "（空）")
        t1.setReadOnly(True)
        t1.setMaximumHeight(100)
        g1_l = QVBoxLayout(g1)
        g1_l.addWidget(t1)

        g2 = QGroupBox("待发文字（可多段）")
        lay.addWidget(g2)
        t2 = QPlainTextEdit("\n---\n".join(s for s in segments if s))
        t2.setReadOnly(True)
        g2_l = QVBoxLayout(g2)
        g2_l.addWidget(t2)

        g3 = QGroupBox("待发图片（图库自动匹配）")
        lay.addWidget(g3)
        lst = QListWidget()
        for pth, iid in image_items:
            exists = Path(pth).is_file()
            lst.addItem(f"{'[缺文件] ' if not exists else ''}{Path(pth).name}  ({iid[:8]}…)")
        if not image_items:
            lst.addItem("（本回合无自动发图）")
        g3_l = QVBoxLayout(g3)
        g3_l.addWidget(lst)

        form = QFormLayout()
        self._spin_delay = QSpinBox()
        self._spin_delay.setRange(5, 20)
        self._spin_delay.setSuffix(" 秒")
        self._spin_delay.setValue(max(5, min(20, int(default_delay_s or 8))))
        form.addRow("倒计时后自动发送", self._spin_delay)
        lay.addLayout(form)

        self._lbl_cd = QLabel()
        self._lbl_cd.setStyleSheet("font-size: 18px; font-weight: bold;")
        lay.addWidget(self._lbl_cd)

        row = QHBoxLayout()
        self._btn_send_now = QPushButton("立即发送")
        self._btn_abort = QPushButton("中断并转人工（ManualHold）")
        row.addWidget(self._btn_send_now)
        row.addWidget(self._btn_abort)
        lay.addLayout(row)

        self._btn_send_now.clicked.connect(self._do_send_now)
        self._btn_abort.clicked.connect(self._do_abort)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._remain = int(self._spin_delay.value())
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._spin_delay.valueChanged.connect(self._reset_countdown)
        self._update_label()
        self._timer.start()

    def _reset_countdown(self) -> None:
        self._remain = int(self._spin_delay.value())
        self._update_label()

    def _update_label(self) -> None:
        self._lbl_cd.setText(f"剩余 {self._remain} 秒后将自动发送（可点「立即发送」或「中断」）")

    def _tick(self) -> None:
        self._remain -= 1
        if self._remain <= 0:
            self._timer.stop()
            self._fire_commit()
            if self.was_sent:
                self.accept()
            return
        self._update_label()

    def _do_send_now(self) -> None:
        self._timer.stop()
        self._fire_commit()
        if self.was_sent:
            self.accept()

    def reject(self) -> None:
        """关闭 / Cancel：未成功发送则转人工。"""
        self._timer.stop()
        if not self.was_sent and not self._abort_handled:
            self._abort_handled = True
            try:
                self._on_abort_hold()
            except Exception as e:
                QMessageBox.warning(self, "中断", f"已尝试转人工，但出现异常：{e!r}")
        super().reject()

    def _do_abort(self) -> None:
        self._timer.stop()
        if not self._abort_handled:
            self._abort_handled = True
            try:
                self._on_abort_hold()
            except Exception as e:
                QMessageBox.warning(self, "中断", f"已尝试转人工，但出现异常：{e!r}")
        self.reject()

    def _fire_commit(self) -> None:
        try:
            self._on_commit()
            self.was_sent = True
        except Exception as e:
            QMessageBox.critical(self, "入队失败", str(e))
            self.was_sent = False
