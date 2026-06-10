"""
apps/mobile/adapter/mobile_adapter.py
=======================================
uiautomator2 实现的千牛手机版适配器。

LOCATORS 字典顶置，千牛改版只需改这一处。
所有点击/输入强制经过 HumanBehavior，不得直接调用 u2 原生方法。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from apps.mobile.adapter.base import Message, QianniuAdapter, Session

_log = logging.getLogger("apps.mobile.adapter")

# ---------------------------------------------------------------------------
# 实测 Locators（通过 poc_runner.py --dump-ui 在 v6.x 千牛上确认）
# 千牛改版后请先运行 --dump-ui，再更新此字典；业务代码不需要改。
# ---------------------------------------------------------------------------
QIANNIU_PACKAGE = "com.taobao.qianniu"

LOCATORS: dict[str, dict[str, str]] = {
    # 接待列表页
    "session_list": {
        "resourceId": f"{QIANNIU_PACKAGE}:id/viewCenterSectionList",
    },
    # 聊天页输入框（实测 resource-id）
    "chat_input_box": {
        "resourceId": f"{QIANNIU_PACKAGE}:id/msgcenter_panel_input_edit",
    },
    # 发送按钮（无 resource-id，用 text 定位）
    "chat_send_button": {
        "text": "发送",
    },
    # 通知栏标准控件
    "notif_title": {"resourceId": "android:id/title"},
    "notif_body":  {"resourceId": "android:id/big_text"},
    "notif_body2": {"resourceId": "android:id/text"},
}

# 时间 / 状态字符串的正则，用于从 content-desc 中过滤掉非买家名内容
_RE_TIME = re.compile(
    r"^(\d{1,2}:\d{2}|昨天|前天|\d+月\d+日|\d{4}-\d{2}-\d{2}|星期[一二三四五六日])$"
)
_STATUS_TAGS = frozenset({"[已读]", "[未读]", ""})


def _is_meta(text: str) -> bool:
    """判断 content-desc 是否为时间/状态（非买家名）。"""
    return bool(_RE_TIME.match(text)) or text in _STATUS_TAGS


# ---------------------------------------------------------------------------
# 适配器实现
# ---------------------------------------------------------------------------

class MobileQianniuAdapter(QianniuAdapter):
    """
    uiautomator2 实现。
    HumanBehavior 实例由调用者注入（解耦 + 方便测试时替换为 no-op）。
    """

    def __init__(
        self,
        device_addr: str,
        *,
        human_behavior: Any | None = None,  # HumanBehavior 实例
    ) -> None:
        self._device_addr = device_addr
        self._d: Any = None  # uiautomator2.Device
        self._hb = human_behavior
        self._notif_thread: threading.Thread | None = None
        self._notif_stop = threading.Event()

    # --- 设备属性 ---

    @property
    def device_id(self) -> str:
        return self._device_addr

    # --- 生命周期 ---

    def connect(self) -> bool:
        try:
            import uiautomator2 as u2  # type: ignore[import]
            self._d = u2.connect(self._device_addr)
            info = self._d.info
            _log.info("已连接: %s  Android %s", self._device_addr, info.get("sdkInt"))
            return True
        except Exception as exc:
            _log.error("连接失败 %s: %r", self._device_addr, exc)
            return False

    def disconnect(self) -> None:
        self._notif_stop.set()
        if self._notif_thread and self._notif_thread.is_alive():
            self._notif_thread.join(timeout=3.0)
        self._d = None
        _log.info("已断开: %s", self._device_addr)

    def is_alive(self) -> bool:
        if self._d is None:
            return False
        try:
            _ = self._d.info
            return True
        except Exception:
            return False

    # --- 内部工具 ---

    def _ensure_session_list(self) -> bool:
        """确保接待列表（viewCenterSectionList）可见。"""
        if self._d is None:
            return False
        rid = LOCATORS["session_list"]["resourceId"]
        if self._d(resourceId=rid).exists(timeout=3):
            return True
        self._d.press("back")
        time.sleep(1.0)
        return bool(self._d(resourceId=rid).exists(timeout=3))

    def _read_session_rows(self) -> list[dict[str, str]]:
        """
        扫描接待列表页的 android.view.View 元素，按顺序提取买家名和已读状态。
        返回 [{"buyer": str, "preview": str, "unread": "是"|"否"}, ...]
        """
        if self._d is None:
            return []
        rows: list[dict[str, str]] = []
        all_views = self._d(className="android.view.View")
        count = all_views.count

        for i in range(count):
            try:
                desc = all_views[i].info.get("contentDescription", "")
                if not desc:
                    continue
                if desc == "[未读]" and rows:
                    rows[-1]["unread"] = "是"
                elif not _is_meta(desc):
                    rows.append({"buyer": desc, "unread": "否", "preview": ""})
            except Exception:
                continue

        # 填充 preview（最近一条 TextView，按位序对应各会话）
        try:
            all_tvs = self._d(className="android.widget.TextView")
            tv_texts: list[str] = []
            for j in range(all_tvs.count):
                try:
                    t = all_tvs[j].get_text() or ""
                    if t and not _is_meta(t):
                        tv_texts.append(t)
                except Exception:
                    continue
            for k, row in enumerate(rows):
                if k < len(tv_texts):
                    row["preview"] = tv_texts[k]
        except Exception:
            pass

        return rows

    # --- 会话读取 ---

    def list_unread_sessions(self) -> list[Session]:
        try:
            if not self._ensure_session_list():
                return []
            rows = self._read_session_rows()
            return [
                Session(
                    session_id=f"{self._device_addr}:{r['buyer']}",
                    buyer_name=r["buyer"],
                    unread=True,
                    last_msg_preview=r["preview"],
                    device_id=self._device_addr,
                )
                for r in rows if r["unread"] == "是"
            ]
        except Exception as exc:
            _log.error("list_unread_sessions 异常: %r", exc)
            return []

    def list_all_sessions(self, limit: int = 10) -> list[Session]:
        try:
            if not self._ensure_session_list():
                return []
            rows = self._read_session_rows()[:limit]
            return [
                Session(
                    session_id=f"{self._device_addr}:{r['buyer']}",
                    buyer_name=r["buyer"],
                    unread=r["unread"] == "是",
                    last_msg_preview=r["preview"],
                    device_id=self._device_addr,
                )
                for r in rows
            ]
        except Exception as exc:
            _log.error("list_all_sessions 异常: %r", exc)
            return []

    def switch_to_session(self, session: Session) -> bool:
        """
        点击匹配买家昵称的会话行（经过 HumanBehavior）。
        """
        try:
            if not self._ensure_session_list():
                return False

            target = self._d(
                className="android.view.View",
                description=session.buyer_name,
            )
            if not target.exists(timeout=3):
                _log.warning("switch_to_session: 找不到买家 %r", session.buyer_name)
                return False

            if self._hb:
                self._hb.human_click(target)
            else:
                target.click()
            time.sleep(2.0)
            return True
        except Exception as exc:
            _log.error("switch_to_session 异常: %r", exc)
            return False

    # --- 消息读取 ---

    def read_latest_messages(self, limit: int = 10) -> list[Message]:
        """扫描聊天页 TextView，返回最近 limit 条有效文本消息。

        气泡侧别检测：取控件 bounds.left + bounds.right 的中点，与屏幕宽度中线比较。
          - 中点 < 屏幕中线 → 左侧气泡 → 买家发送
          - 中点 ≥ 屏幕中线 → 右侧气泡 → 客服/卖家发送
        若无法获取 bounds 或屏幕宽度，则保守回退为 is_from_buyer=True（不丢消息）。
        """
        try:
            if self._d is None:
                return []

            # 获取屏幕宽度，用于气泡侧别判断
            screen_mid_x: int = 0
            try:
                w = self._d.info.get("displayWidth", 0)
                screen_mid_x = w // 2 if w else 0
            except Exception:
                pass

            msgs: list[Message] = []
            all_tvs = self._d(className="android.widget.TextView")
            count = all_tvs.count
            for i in range(count - 1, max(count - 30, -1), -1):
                try:
                    elem = all_tvs[i]
                    t = elem.get_text() or ""
                    if not t or len(t) <= 1 or _is_meta(t):
                        continue

                    # 气泡侧别：通过 X 轴中心点判断
                    if screen_mid_x:
                        try:
                            bounds = elem.info.get("bounds", {})
                            left  = bounds.get("left",  0)
                            right = bounds.get("right", 0)
                            bubble_cx = (left + right) // 2
                            is_buyer  = bubble_cx < screen_mid_x
                        except Exception:
                            is_buyer = True  # 保守策略
                    else:
                        is_buyer = True

                    msgs.append(Message(
                        msg_type="text",
                        text=t,
                        is_from_buyer=is_buyer,
                        timestamp="",
                        raw={},
                    ))
                    if len(msgs) >= limit:
                        break
                except Exception:
                    continue
            return list(reversed(msgs))
        except Exception as exc:
            _log.error("read_latest_messages 异常: %r", exc)
            return []

    def get_current_buyer_anchor(self) -> str:
        """尝试读取聊天页顶部 ActionBar / 标题区的买家昵称。"""
        try:
            if self._d is None:
                return ""
            tvs = self._d(className="android.widget.TextView")
            if tvs.count > 0:
                text = tvs[0].get_text() or ""
                if text and not _is_meta(text):
                    return text
            return ""
        except Exception as exc:
            _log.error("get_current_buyer_anchor 异常: %r", exc)
            return ""

    # --- 消息发送 ---

    def send_text(self, text: str) -> bool:
        """
        在当前聊天页输入并发送文本。
        全程经过 HumanBehavior（点击输入框、输入、点发送）。
        """
        try:
            if self._d is None:
                return False

            # 输入框：优先 resource-id，降级 EditText
            input_box = self._d(resourceId=LOCATORS["chat_input_box"]["resourceId"])
            if not input_box.exists(timeout=3):
                input_box = self._d(className="android.widget.EditText")
            if not input_box.exists(timeout=3):
                _log.warning("send_text: 找不到输入框")
                return False

            if self._hb:
                self._hb.human_click(input_box)
                self._hb.human_type(input_box, text)
            else:
                input_box.click()
                time.sleep(0.3)
                input_box.set_text(text)
            time.sleep(0.5)

            # 发送按钮
            send_btn = self._d(text=LOCATORS["chat_send_button"]["text"])
            if not send_btn.exists(timeout=2):
                send_btn = self._d(description="发送")
            if not send_btn.exists(timeout=2):
                _log.warning("send_text: 找不到发送按钮")
                return False

            if self._hb:
                self._hb.human_click(send_btn)
            else:
                send_btn.click()
            time.sleep(0.5)
            _log.info("send_text 成功: %r", text[:60])
            return True
        except Exception as exc:
            _log.error("send_text 异常: %r", exc)
            return False

    # --- 通知监听 ---

    def screenshot_bytes(self) -> bytes | None:
        """返回当前屏幕截图的 PNG 字节，供 UI 预览使用。失败返回 None。"""
        try:
            if self._d is None:
                return None
            import io
            buf = io.BytesIO()
            self._d.screenshot().save(buf, format="png")
            return buf.getvalue()
        except Exception as exc:
            _log.debug("screenshot_bytes 失败: %r", exc)
            return None

    def start_notification_listener(self, callback: Any) -> None:
        """后台线程轮询 Android 通知抽屉，捕获千牛通知后回调 callback(sender, text)。"""
        if self._notif_thread and self._notif_thread.is_alive():
            return
        self._notif_stop.clear()
        self._notif_thread = threading.Thread(
            target=self._notif_loop,
            args=(callback,),
            name=f"MobileNotif-{self._device_addr}",
            daemon=True,
        )
        self._notif_thread.start()
        _log.info("通知监听线程已启动: %s", self._device_addr)

    def _notif_loop(self, callback: Any) -> None:
        while not self._notif_stop.is_set():
            try:
                if self._d is None:
                    break
                self._d.open_notification()
                time.sleep(0.8)

                notif = self._d(packageName=QIANNIU_PACKAGE)
                if notif.exists(timeout=1):
                    title_elem = self._d(resourceId=LOCATORS["notif_title"]["resourceId"])
                    body_elem = self._d(resourceId=LOCATORS["notif_body"]["resourceId"])
                    if not body_elem.exists(timeout=1):
                        body_elem = self._d(resourceId=LOCATORS["notif_body2"]["resourceId"])

                    sender = title_elem.get_text() if title_elem.exists(timeout=1) else ""
                    body = body_elem.get_text() if body_elem.exists(timeout=1) else ""
                    if sender or body:
                        _log.info("通知: sender=%r body=%r", sender, body[:60])
                        try:
                            callback(sender, body)
                        except Exception as cb_exc:
                            _log.error("通知回调异常: %r", cb_exc)

                self._d.press("back")
                time.sleep(3.0)
            except Exception as exc:
                _log.warning("通知监听循环异常: %r", exc)
                time.sleep(5.0)
