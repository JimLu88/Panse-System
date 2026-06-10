"""
apps/mobile/adapter/base.py
============================
QianniuAdapter 抽象基类 + Session / Message 数据类。

业务层只依赖这里的接口，不直接接触 uiautomator2。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Session:
    """一条买家会话的快照。"""
    session_id: str          # 唯一标识（此版本用买家昵称+设备id组合）
    buyer_name: str          # 买家昵称（来自 content-desc）
    unread: bool             # 是否有未读消息
    last_msg_preview: str    # 列表页最后一条消息预览
    device_id: str = ""      # 所属设备标识


@dataclass(frozen=True, slots=True)
class Message:
    """一条聊天气泡的快照。"""
    msg_type: str            # "text" | "image" | "product_card" | "unknown"
    text: str                # 文本内容（图片/商品卡时为空或描述）
    is_from_buyer: bool      # True=买家发、False=卖家/自动回复发
    timestamp: str           # 原始时间戳字符串
    raw: dict[str, Any] = field(default_factory=dict)  # 保留原始控件信息供调试


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class QianniuAdapter(ABC):
    """
    手机端千牛操作适配器。
    每个实例对应一台设备（模拟器 / USB / WiFi）上的一个千牛账号。

    所有方法内部 try/except，异常不上抛，而是返回空值 / False 并记录日志。
    调用方根据返回值决策，无需处理异常。
    """

    # --- 生命周期 ---

    @abstractmethod
    def connect(self) -> bool:
        """建立连接，返回是否成功。"""

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接，释放资源。"""

    @abstractmethod
    def is_alive(self) -> bool:
        """检测连接是否仍然正常。"""

    # --- 会话读取 ---

    @abstractmethod
    def list_unread_sessions(self) -> list[Session]:
        """
        读取当前接待列表中所有未读会话。
        返回 [] 表示无未读或读取失败。
        """

    @abstractmethod
    def list_all_sessions(self, limit: int = 10) -> list[Session]:
        """读取接待列表前 limit 条会话（含已读）。"""

    @abstractmethod
    def switch_to_session(self, session: Session) -> bool:
        """
        切换到指定会话（点击列表中对应行）。
        返回 True 表示已进入聊天页。
        """

    # --- 消息读取 ---

    @abstractmethod
    def read_latest_messages(self, limit: int = 10) -> list[Message]:
        """
        在当前打开的聊天页，读取最近 limit 条消息。
        必须在 switch_to_session 成功后调用。
        """

    @abstractmethod
    def get_current_buyer_anchor(self) -> str:
        """
        读取当前聊天页顶部的买家昵称（用于发送前昵称锚定校验）。
        返回空字符串表示读取失败。
        """

    # --- 消息发送 ---

    @abstractmethod
    def send_text(self, text: str) -> bool:
        """
        在当前聊天页发送文本消息。
        返回 True 表示发送成功。
        所有点击/输入必须经过 HumanBehavior 中间件，不得直接调用 u2 原生方法。
        """

    # --- 通知监听 ---

    @abstractmethod
    def start_notification_listener(
        self,
        callback: Any,   # Callable[[str, str], None]  (sender, text)
    ) -> None:
        """
        启动后台通知监听线程。
        callback(sender: str, text: str) 在主线程外调用，需线程安全。
        """

    # --- 设备属性 ---

    @property
    @abstractmethod
    def device_id(self) -> str:
        """设备唯一标识（ip:port 或 serial）。"""
