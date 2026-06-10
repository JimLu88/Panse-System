"""
v1.6.0 重复回复防御层：SessionReplyBudget。

为什么需要：
  - v1.5.8 之前的去重只有 last_buyer_digest（多 cycle 同消息冷却），
    覆盖不到「同 cycle 内 welcome + LLM 双发」「LLM 同意图不同表述」等路径
  - 用户决策：每个会话，同一买家消息 AI 最多回 3 条；相似度 > 0.75 拒发；
    买家有新回复（OCR digest 变）则计数重置

核心数据结构：
  - 按 session_id 维护一个 SessionReplyBudget 实例
  - 每实例记录 current_buyer_digest + reply_count + recent_replies(ring buffer)
  - 发送前 can_send() 返回 (允许?, 原因)
  - 发送成功后 record_sent() 推入历史

兼容性：
  - bypass_dedup=True 用于风控弹窗的"重新生成回复"路径（#4），跳过相似度但仍受 3 次上限管
  - 旧调用方不接此模块 = 老行为继续

为什么用 Jaccard 字符集而不是编辑距离：
  - 中文短句够用：'您好我们发圆通' vs '您好这边走圆通' 字符集重合率高
  - 纯 stdlib、O(n)、不依赖 difflib
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable


def jaccard_similarity(a: str, b: str) -> float:
    """
    字符集 Jaccard 相似度（中文短句友好，无外部依赖）。

    返回 [0.0, 1.0]，1.0 = 字符集完全一样。
    """
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


class SessionReplyBudget:
    """
    单个会话的回复预算 + 已发文本 ring buffer。

    使用：
        budget = SessionReplyBudget()
        ok, reason = budget.can_send("您好在的呢~", buyer_digest)
        if ok:
            actually_send(text)
            budget.record_sent(text)
        else:
            log(f"跳过：{reason}")
    """

    DEFAULT_HARD_LIMIT = 3
    DEFAULT_SIMILARITY_THRESHOLD = 0.75
    DEFAULT_RING_SIZE = 10

    def __init__(
        self,
        *,
        hard_limit: int = DEFAULT_HARD_LIMIT,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        ring_size: int = DEFAULT_RING_SIZE,
    ) -> None:
        self.hard_limit = max(1, int(hard_limit))
        self.similarity_threshold = float(similarity_threshold)
        self.current_buyer_digest: str = ""
        self.reply_count: int = 0
        self.recent_replies: deque[str] = deque(maxlen=int(ring_size))
        self._lock = threading.Lock()

    def can_send(
        self,
        text: str,
        buyer_digest: str,
        *,
        bypass_dedup: bool = False,
    ) -> tuple[bool, str]:
        """
        判定是否允许发送 text。

        Args:
            text: 即将入队的回复文本
            buyer_digest: 当前买家消息的归一化摘要
                          （见 reply_guards.normalize_buyer_digest）
            bypass_dedup: 是否绕过相似度检查（风控重发场景需要）
                          注意：bypass_dedup=True 仍受 hard_limit 管控，防无限循环

        Returns:
            (allow, reason) — allow=False 时 reason 给日志使用
        """
        t = (text or "").strip()
        if not t:
            return False, "empty_text"

        with self._lock:
            # 锁 1：买家消息变了 → 计数器清零（属于新一轮回复）
            if buyer_digest != self.current_buyer_digest:
                self.current_buyer_digest = buyer_digest
                self.reply_count = 0

            # 锁 2：硬上限。bypass_dedup 路径也受此限制（防 LLM 无限重新生成）
            if self.reply_count >= self.hard_limit:
                return False, f"hard_limit_{self.hard_limit}_reached"

            # 锁 3：相似度。bypass_dedup=True 时跳过
            if not bypass_dedup:
                for prev in self.recent_replies:
                    sim = jaccard_similarity(t, prev)
                    if sim > self.similarity_threshold:
                        return False, (
                            f"similar_to_prev(sim={sim:.2f},"
                            f"prev={prev[:18]!r})"
                        )

        return True, ""

    def record_sent(self, text: str) -> None:
        """发送成功后调用：计数 +1，文本入 ring buffer。"""
        t = (text or "").strip()
        if not t:
            return
        with self._lock:
            self.reply_count += 1
            self.recent_replies.append(t)

    def reset(self) -> None:
        """手动重置预算（人工接管后/会话切换等场景）。"""
        with self._lock:
            self.current_buyer_digest = ""
            self.reply_count = 0
            self.recent_replies.clear()

    def snapshot(self) -> dict:
        """诊断用：返回当前状态快照（线程安全）。"""
        with self._lock:
            return {
                "current_buyer_digest": self.current_buyer_digest,
                "reply_count": self.reply_count,
                "recent_replies": list(self.recent_replies),
                "hard_limit": self.hard_limit,
                "similarity_threshold": self.similarity_threshold,
            }


class ReplyBudgetRegistry:
    """
    全局 session_id → SessionReplyBudget 注册表。线程安全，单例使用。

    使用：
        from apps.core.orchestrator.outbound_history import get_budget_registry
        registry = get_budget_registry()
        budget = registry.get_or_create("source_id::session_id")
    """

    def __init__(self) -> None:
        self._budgets: dict[str, SessionReplyBudget] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_key: str) -> SessionReplyBudget:
        with self._lock:
            b = self._budgets.get(session_key)
            if b is None:
                b = SessionReplyBudget()
                self._budgets[session_key] = b
            return b

    def reset_session(self, session_key: str) -> None:
        with self._lock:
            b = self._budgets.get(session_key)
        if b is not None:
            b.reset()

    def keys(self) -> Iterable[str]:
        with self._lock:
            return list(self._budgets.keys())

    def clear_all(self) -> None:
        """重启接待或换店时调用。"""
        with self._lock:
            self._budgets.clear()


_REGISTRY_SINGLETON: ReplyBudgetRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_budget_registry() -> ReplyBudgetRegistry:
    """进程内单例，所有调用方共享同一个 registry。"""
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        with _REGISTRY_LOCK:
            if _REGISTRY_SINGLETON is None:
                _REGISTRY_SINGLETON = ReplyBudgetRegistry()
    return _REGISTRY_SINGLETON
