"""
apps/mobile/adapter/http_mobile_adapter.py
==========================================
QianniuAdapter 的 HTTP 实现：通过 LAN 调用 Android APK 的 Ktor 服务。

对应 APK 端（android_apk/app/src/main/java/com/aiworkbench/qianniu_agent/http/HttpServer.kt）：
  GET  /health                 → 不鉴权，连通性检查
  GET  /api/sessions           → SessionListReader
  GET  /api/messages?n=5       → MessageReader
  POST /api/switch             → 切换会话 {"name": "..."}
  POST /api/send               → 发消息 {"text": "...", "skipHumanize": false}
  GET  /api/current_anchor     → 当前聊天页标题

设计要点：
  - 单实例对应一台手机（device_id = "ip:port"）
  - 所有 /api/* 请求带 Authorization: Bearer <token>
  - 超时分级：connect/anchor 短超时；send 长超时（APK 端拟人化延迟 8-50s）
  - 任何 HTTP 异常都吞掉返回 False/[]/""，与 base.QianniuAdapter 契约一致
  - 通知监听 = 后台线程轮询 GET /api/sessions（默认 4s 一次）
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import httpx

from apps.mobile.adapter.base import Message, QianniuAdapter, Session

log = logging.getLogger("apps.mobile.adapter.http")


# 短超时：health / sessions / anchor / messages
_TIMEOUT_FAST_S = 5.0
# 长超时：send（APK 端可能拟人化等 8-90s 再发出）
_TIMEOUT_SEND_S = 120.0
# 切换会话：会触发 dispatchGesture，需等待 UI 渲染
_TIMEOUT_SWITCH_S = 10.0


class HttpMobileAdapter(QianniuAdapter):
    """
    通过 LAN HTTP 调用 Android APK（QianniuForegroundService + HttpServer）。

    Args:
        ip:    APK 显示的 LAN IP，如 "192.168.1.42"
        port:  默认 8765
        token: PairingAuth 生成的 Base64 token（PC 端通过扫码拿到）
        device_label: UI 显示用的别名（可选）
    """

    def __init__(
        self,
        ip: str,
        port: int = 8765,
        token: str = "",
        device_label: str = "",
    ) -> None:
        self._ip = (ip or "").strip()
        self._port = int(port)
        self._token = (token or "").strip()
        self._label = device_label
        self._base = f"http://{self._ip}:{self._port}"
        self._auth_header = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        self._client: httpx.Client | None = None

        # 通知监听
        self._notify_thread: threading.Thread | None = None
        self._notify_stop = threading.Event()
        self._notify_seen: dict[str, int] = {}  # name -> last seen unread count

        # ── 兼容 device_manager._device_loop 的 u2-only 字段 ──
        # _d  = uiautomator2 device，HTTP 路径无此对象；为 None 时循环跳过 press("back")
        # _hb = PC 端 HumanBehavior；APK 端已内嵌拟人化，PC 端不再补
        self._d = None
        self._hb = None

    # ── 内部：HTTP 客户端 ─────────────────────────────────────────

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base,
                headers=self._auth_header,
                timeout=_TIMEOUT_FAST_S,
            )
        return self._client

    def _get(self, path: str, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            c = self._ensure_client()
            r = c.get(path, timeout=timeout or _TIMEOUT_FAST_S)
            if r.status_code == 401:
                log.warning("HttpAdapter: 401 unauthorized (token 不匹配？)")
                return None
            if r.status_code >= 400:
                log.warning("HttpAdapter: GET %s -> %d %s", path, r.status_code, r.text[:200])
                return None
            return r.json()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
            log.warning("HttpAdapter GET %s failed: %r", path, e)
            return None
        except Exception as e:  # JSON decode etc
            log.exception("HttpAdapter GET %s 未知异常: %r", path, e)
            return None

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        try:
            c = self._ensure_client()
            r = c.post(path, json=payload, timeout=timeout or _TIMEOUT_FAST_S)
            if r.status_code == 401:
                log.warning("HttpAdapter: 401 unauthorized (token 不匹配？)")
                return None
            if r.status_code == 503:
                # 503 通常是 quiet_hours / accessibility_not_connected
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                log.info("HttpAdapter POST %s -> 503 %s", path, body.get("error", r.text[:200]))
                return body
            if r.status_code >= 400:
                log.warning("HttpAdapter: POST %s -> %d %s", path, r.status_code, r.text[:200])
                return None
            return r.json()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
            log.warning("HttpAdapter POST %s failed: %r", path, e)
            return None
        except Exception as e:
            log.exception("HttpAdapter POST %s 未知异常: %r", path, e)
            return None

    # ── 生命周期 ─────────────────────────────────────────────────

    def connect(self) -> bool:
        """探活：GET /health 必须 200 且 accessibilityConnected=true。"""
        resp = self._get("/health")
        if resp is None:
            return False
        if not resp.get("ok"):
            return False
        if not resp.get("accessibilityConnected"):
            log.warning(
                "HttpAdapter: APK 端无障碍未启用，请去手机系统设置 → 无障碍 → 启用千牛接待助手"
            )
            return False
        # 顺手用 /api/sessions 校验 token（401 → token 错）
        token_ok = self._get("/api/sessions")
        if token_ok is None:
            log.warning("HttpAdapter: /api/sessions 鉴权失败，token 不匹配")
            return False
        log.info("HttpAdapter: 连接成功 %s", self._base)
        return True

    def disconnect(self) -> None:
        self._notify_stop.set()
        if self._notify_thread and self._notify_thread.is_alive():
            self._notify_thread.join(timeout=2.0)
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def is_alive(self) -> bool:
        """轻量探活：只看 /health 200，不校验 token / accessibility。"""
        resp = self._get("/health", timeout=2.5)
        return bool(resp and resp.get("ok"))

    # ── 会话读取 ─────────────────────────────────────────────────

    def list_unread_sessions(self) -> list[Session]:
        all_sess = self._list_sessions_raw()
        return [s for s in all_sess if s.unread]

    def list_all_sessions(self, limit: int = 10) -> list[Session]:
        all_sess = self._list_sessions_raw()
        return all_sess[: max(0, int(limit))]

    def _list_sessions_raw(self) -> list[Session]:
        """统一从 APK 拉一次 sessions，再做过滤。"""
        resp = self._get("/api/sessions")
        if not resp:
            return []
        items = resp.get("sessions") or []
        out: list[Session] = []
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            unread_count = int(it.get("unreadCount") or 0)
            preview = (it.get("previewText") or "")
            out.append(
                Session(
                    session_id=f"{self.device_id}:{name}",
                    buyer_name=name,
                    unread=unread_count > 0,
                    last_msg_preview=preview,
                    device_id=self.device_id,
                )
            )
        return out

    def switch_to_session(self, session: Session) -> bool:
        if not session.buyer_name:
            return False
        resp = self._post(
            "/api/switch",
            {"name": session.buyer_name},
            timeout=_TIMEOUT_SWITCH_S,
        )
        if not resp:
            return False
        return bool(resp.get("ok"))

    # ── 消息读取 ─────────────────────────────────────────────────

    def read_latest_messages(self, limit: int = 10) -> list[Message]:
        resp = self._get(f"/api/messages?n={int(limit)}")
        if not resp:
            return []
        items = resp.get("messages") or []
        out: list[Message] = []
        for it in items:
            text = it.get("text") or ""
            side = (it.get("side") or "UNKNOWN").upper()
            captured = it.get("capturedAt")
            out.append(
                Message(
                    msg_type="text",   # APK 端目前只读文本气泡；图片/卡片后续阶段
                    text=text,
                    is_from_buyer=(side == "LEFT"),
                    timestamp=str(captured) if captured is not None else "",
                    raw={"side": side, "capturedAt": captured},
                )
            )
        return out

    def get_current_buyer_anchor(self) -> str:
        resp = self._get("/api/current_anchor")
        if not resp:
            return ""
        return (resp.get("chatTitle") or "").strip()

    # ── 消息发送 ─────────────────────────────────────────────────

    def send_text(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        resp = self._post(
            "/api/send",
            {"text": text, "skipHumanize": False},
            timeout=_TIMEOUT_SEND_S,
        )
        if not resp:
            return False
        if resp.get("error") == "quiet_hours":
            log.info("HttpAdapter: APK 端深夜降级，跳过发送")
            return False
        return bool(resp.get("ok"))

    # ── 通知监听（后台轮询 sessions 检测新未读）──────────────────

    def start_notification_listener(self, callback: Callable[[str, str], None]) -> None:
        """
        启动后台线程每 4s 拉一次 /api/sessions，
        发现某买家的 unreadCount 增加时触发 callback(sender, preview)。
        """
        if self._notify_thread and self._notify_thread.is_alive():
            return
        self._notify_stop.clear()
        self._notify_seen.clear()
        # 初始化 baseline，避免首次轮询把全部已存在的未读都报上去
        for s in self._list_sessions_raw():
            self._notify_seen[s.buyer_name] = 1 if s.unread else 0

        def _loop() -> None:
            while not self._notify_stop.is_set():
                try:
                    current = self._list_sessions_raw()
                    for s in current:
                        prev = self._notify_seen.get(s.buyer_name, 0)
                        now = 1 if s.unread else 0
                        if now > prev:
                            try:
                                callback(s.buyer_name, s.last_msg_preview)
                            except Exception:
                                log.exception("HttpAdapter notification callback raised")
                        self._notify_seen[s.buyer_name] = now
                except Exception:
                    log.exception("HttpAdapter notification loop iteration failed")
                self._notify_stop.wait(timeout=4.0)
            log.info("HttpAdapter notification listener exited")

        self._notify_thread = threading.Thread(
            target=_loop, name=f"HttpAdapter-Notify-{self._ip}", daemon=True
        )
        self._notify_thread.start()

    # ── 设备属性 ─────────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        return f"{self._ip}:{self._port}"

    @property
    def label(self) -> str:
        return self._label or self.device_id
