"""
apps/mobile/orchestrator/mobile_brain_bridge.py
================================================
手机端消息 → 现有 brain → 经 MobileAdapter 发出。

设计原则：
  - 零复制现有 brain 代码，只做调用
  - 去重 / 噪声过滤直接复用 reply_guards / input_quality_gate
  - 发送前昵称锚定校验（send_with_anchor_check）
  - trigger="mobile" 由 event_pipeline._execute() 中新增分支处理
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("apps.mobile.orchestrator")

# ---------------------------------------------------------------------------
# 令牌桶限速器（每设备独立实例）
# ---------------------------------------------------------------------------

class _TokenBucket:
    """
    简单令牌桶：capacity 令牌、每秒补充 refill_rate 个。

    用于保护 brain / LLM 调用链不被短时间大量买家消息淹没。
    正常接待节奏（<10 条/分钟）完全不受影响。
    """

    def __init__(self, capacity: float = 10.0, refill_rate: float = 10 / 60.0) -> None:
        """
        Args:
            capacity:    桶容量（允许的最大积累令牌数），默认 10 条/突发。
            refill_rate: 每秒补充令牌数，默认 10/60 ≈ 0.167（即每分钟 10 条）。
        """
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._tokens: float = capacity
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """消耗一个令牌。有令牌则返回 True；已耗尽返回 False（调用方应丢弃本次消息）。"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


class MobileBrainBridge:
    """
    桥接手机端适配器和现有 PipelineOrchestrator。

    用法::

        bridge = MobileBrainBridge(
            adapter=adapter,
            shop_cfg_path=Path("configs/shops/demo.yaml"),
        )
        bridge.handle_mobile_message(session, buyer_text)
    """

    def __init__(
        self,
        *,
        adapter: Any,               # MobileQianniuAdapter
        shop_cfg_path: Path,
        db_path: Path | None = None,
        log_fn: Any | None = None,
        cooldown_s: float = 90.0,
    ) -> None:
        from apps.core.orchestrator.action_queue import ActionQueue
        from apps.core.orchestrator.event_pipeline import EventPipeline, PipelineOrchestrator
        from apps.core.runtime_paths import default_sqlite_db_path

        self._adapter = adapter
        self._shop_cfg_path = shop_cfg_path
        self._cooldown_s = cooldown_s
        self._log_fn = log_fn or (lambda m: _log.info(m))

        self._last_digest: str = ""
        self._last_handled_at: float = 0.0
        self._lock = threading.Lock()

        device_id = getattr(adapter, "device_id", "mobile:unknown")
        shop_id   = Path(shop_cfg_path).stem          # e.g. "demo_shop"
        source_id = f"mobile:{device_id}:{shop_id}"   # PC + mobile 同店铺不共享去重 key
        session_id = f"mobile_session:{device_id}"

        # 令牌桶：10 条/分钟，保护 LLM 不被突发消息淹没
        self._rate_limiter = _TokenBucket(capacity=10.0, refill_rate=10 / 60.0)
        self._source_id = source_id

        self._action_queue: ActionQueue = ActionQueue()
        self._pipeline = EventPipeline(self._action_queue)
        self._orchestrator = PipelineOrchestrator(
            shop_cfg_path=shop_cfg_path,
            source_id=source_id,
            session_id=session_id,
            pipeline=self._pipeline,
            db_path=db_path or default_sqlite_db_path(),
            log=self._log_fn,
        )

        # 启动 mobile 专属出队 executor 线程
        self._executor_stop = threading.Event()
        self._executor_thread = threading.Thread(
            target=self._mobile_executor_loop,
            name=f"MobileExecutor-{device_id}",
            daemon=True,
        )
        self._executor_thread.start()

    # --- 消息处理入口 ---

    def handle_mobile_message(self, session: Any, buyer_text: str) -> None:
        """
        处理一条手机端买家消息。
        应用去重 / 噪声过滤后，封装为 trigger="mobile" 的 NewMessageEvent
        交给 PipelineOrchestrator。
        """
        from apps.core.ai.input_quality_gate import check_buyer_input
        from apps.core.orchestrator.models import NewMessageEvent
        from apps.core.orchestrator.reply_guards import (
            is_echo_or_noise_buyer_text,
            normalize_buyer_digest,
            should_skip_duplicate_buyer,
        )

        t = (buyer_text or "").strip()
        if not t:
            return

        # 令牌桶限速：正常节奏（<10 条/分钟）不受影响；突发时静默丢弃
        if not self._rate_limiter.consume():
            self._log_fn(f"[mobile] 限速触发，丢弃消息: {t!r:.40}")
            return

        gate = check_buyer_input(t)
        if gate.action == "discard_log":
            self._log_fn(f"[mobile] 丢弃噪声消息 rule={gate.rule_name!r}")
            return

        if is_echo_or_noise_buyer_text(t):
            self._log_fn(f"[mobile] Echo/噪声，跳过: {t!r:.40}")
            return

        with self._lock:
            if should_skip_duplicate_buyer(
                buyer_text=t,
                last_digest=self._last_digest,
                last_handled_at=self._last_handled_at,
                cooldown_s=self._cooldown_s,
            ):
                self._log_fn(f"[mobile] 冷却中，跳过重复消息: {t!r:.40}")
                return
            self._last_digest = normalize_buyer_digest(t)
            self._last_handled_at = time.time()

        ev = NewMessageEvent(
            source_id=self._source_id,
            session_id=session.session_id,
            trigger="mobile",
            payload={
                "buyer_text": t,
                "buyer_name": session.buyer_name,
            },
        )
        self._log_fn(f"[mobile] 投递事件 buyer={session.buyer_name!r} text={t!r:.60}")
        self._orchestrator.handle_new_message_event(ev)

    # --- 发送前昵称锚定校验 ---

    def send_with_anchor_check(self, expected_buyer: str, text: str) -> bool:
        """发送前再读买家昵称，不一致则熔断（返回 False）。"""
        current = self._adapter.get_current_buyer_anchor()
        if (
            current
            and expected_buyer
            and expected_buyer not in current
            and current not in expected_buyer
        ):
            self._log_fn(
                f"[mobile] 昵称锚定不匹配 expected={expected_buyer!r} now={current!r}，熔断"
            )
            return False
        return self._adapter.send_text(text)

    # --- Mobile 专属 ActionQueue 出队 executor ---

    def _mobile_executor_loop(self) -> None:
        """
        监听 ActionQueue；source_id 以 "mobile:" 开头的 SEND_TEXT / SOOTHE_WAIT
        动作交给 MobileAdapter 发送，其余丢弃。
        """
        from apps.core.orchestrator.models import ActionKind

        while not self._executor_stop.is_set():
            try:
                item = self._action_queue.get(timeout=1.0)
            except Exception:
                continue

            try:
                if not item.source_id.startswith("mobile:"):
                    continue

                if item.kind == ActionKind.SEND_TEXT:
                    text = str(item.payload.get("text", ""))
                    buyer_name = str(item.payload.get("buyer_name", ""))
                    if text:
                        ok = self.send_with_anchor_check(buyer_name, text)
                        self._log_fn(
                            f"[mobile] SEND_TEXT {'✓' if ok else '✗'} "
                            f"buyer={buyer_name!r} text={text!r:.60}"
                        )
                elif item.kind == ActionKind.SOOTHE_WAIT:
                    text = str(item.payload.get("text", ""))
                    if text:
                        ok = self._adapter.send_text(text)
                        self._log_fn(f"[mobile] SOOTHE_WAIT {'✓' if ok else '✗'} {text!r:.40}")
                else:
                    self._log_fn(f"[mobile] 忽略动作 kind={item.kind}")
            except Exception as exc:
                _log.error("mobile executor 处理异常: %r", exc)

    def shutdown(self) -> None:
        self._executor_stop.set()
        if self._executor_thread.is_alive():
            self._executor_thread.join(timeout=3.0)
