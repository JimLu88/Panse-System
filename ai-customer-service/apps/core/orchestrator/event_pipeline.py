from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Iterable

from .action_queue import ActionQueue
from .models import ActionItem, ActionKind, ActionPriority


@dataclass(frozen=True, slots=True)
class Event:
    source_id: str
    session_id: str
    trigger: str  # vision|audio|manual|sweep
    priority: int = 100


EventProducer = Callable[[], Iterable[Event]]


class EventPipeline:
    """事件 → ActionItem 入队。"""

    def __init__(self, action_queue: ActionQueue):
        self._q = action_queue
        # v1.6.14 客服存档钩子：入队"我方回复"后回调 (source_id, session_id, text)
        # 由 PipelineOrchestrator 设置（带 shop/db 上下文）。默认 None=不存档。
        self._archive_hook = None

    def _maybe_archive_outbound(self, source_id: str, session_id: str, text: str) -> None:
        hook = self._archive_hook
        if hook is None:
            return
        try:
            hook(source_id, session_id, text)
        except Exception:
            pass  # 存档为软附加，绝不影响入队

    def enqueue_noop(self, source_id: str, session_id: str, *, priority: int = 100) -> str:
        action_id = str(uuid.uuid4())
        item = ActionItem(
            action_id=action_id,
            source_id=source_id,
            session_id=session_id,
            kind=ActionKind.NOOP,
            payload={},
            priority=ActionPriority(int(priority)),
        )
        self._q.put(item)
        return action_id

    def _budget_check_and_record(
        self,
        *,
        source_id: str,
        session_id: str,
        text: str,
        buyer_digest: str,
        bypass_dedup: bool,
    ) -> bool:
        """
        v1.6.0 接入 SessionReplyBudget：发送前判定 3 次硬上限 + 相似度。

        返回 True=允许入队（并已 record_sent 提前预占名额）；
            False=拒发，调用方应记日志后不入队。

        buyer_digest 为空时跳过检查（兼容旧调用方）。
        """
        if not buyer_digest:
            return True
        try:
            from apps.core.orchestrator.outbound_history import get_budget_registry
            session_key = f"{source_id}::{session_id}"
            budget = get_budget_registry().get_or_create(session_key)
            ok, reason = budget.can_send(text, buyer_digest, bypass_dedup=bypass_dedup)
            if not ok:
                import logging
                logging.getLogger("apps.core.orchestrator").warning(
                    "[budget] 拒发: session=%s reason=%s text=%r",
                    session_key, reason, text[:40],
                )
                return False
            budget.record_sent(text)
            return True
        except Exception as e:
            # budget 任何异常都不该阻塞发送，记日志即可
            import logging
            logging.getLogger("apps.core.orchestrator").exception(
                "[budget] check 异常，放行入队: %r", e,
            )
            return True

    def enqueue_soothe_wait(
        self,
        source_id: str,
        session_id: str,
        *,
        text: str = "您好，在的呢～",
        chat_log_meta: dict | None = None,
        buyer_digest: str = "",
        bypass_dedup: bool = False,
    ) -> str:
        # v1.6.0：传 buyer_digest 才走 SessionReplyBudget；不传则旧行为
        if not self._budget_check_and_record(
            source_id=source_id, session_id=session_id, text=text,
            buyer_digest=buyer_digest, bypass_dedup=bypass_dedup,
        ):
            return ""  # 被预算拒绝，未入队
        action_id = str(uuid.uuid4())
        payload: dict = {"text": text}
        if chat_log_meta:
            payload["chat_log_meta"] = dict(chat_log_meta)
        item = ActionItem(
            action_id=action_id,
            source_id=source_id,
            session_id=session_id,
            kind=ActionKind.SOOTHE_WAIT,
            payload=payload,
            priority=ActionPriority(10),
        )
        self._q.put(item)
        self._maybe_archive_outbound(source_id, session_id, text)
        return action_id

    def enqueue_send_text(
        self,
        source_id: str,
        session_id: str,
        *,
        text: str,
        priority: int = 100,
        chat_log_meta: dict | None = None,
        buyer_digest: str = "",
        bypass_dedup: bool = False,
    ) -> str:
        # v1.6.0：传 buyer_digest 才走 SessionReplyBudget；不传则旧行为
        if not self._budget_check_and_record(
            source_id=source_id, session_id=session_id, text=text,
            buyer_digest=buyer_digest, bypass_dedup=bypass_dedup,
        ):
            return ""  # 被预算拒绝，未入队
        action_id = str(uuid.uuid4())
        payload: dict = {"text": text}
        if chat_log_meta:
            payload["chat_log_meta"] = dict(chat_log_meta)
        item = ActionItem(
            action_id=action_id,
            source_id=source_id,
            session_id=session_id,
            kind=ActionKind.SEND_TEXT,
            payload=payload,
            priority=ActionPriority(int(priority)),
        )
        self._q.put(item)
        self._maybe_archive_outbound(source_id, session_id, text)
        return action_id

    def enqueue_send_image(
        self,
        source_id: str,
        session_id: str,
        *,
        image_path: str,
        priority: int = 85,
        chat_log_meta: dict | None = None,
    ) -> str:
        action_id = str(uuid.uuid4())
        payload: dict = {"image_path": str(image_path)}
        if chat_log_meta:
            payload["chat_log_meta"] = dict(chat_log_meta)
        item = ActionItem(
            action_id=action_id,
            source_id=source_id,
            session_id=session_id,
            kind=ActionKind.SEND_IMAGE,
            payload=payload,
            priority=ActionPriority(int(priority)),
        )
        self._q.put(item)
        return action_id

    def enqueue_reacquire_context(self, source_id: str, session_id: str, *, shop_cfg_path: str) -> str:
        action_id = str(uuid.uuid4())
        item = ActionItem(
            action_id=action_id,
            source_id=source_id,
            session_id=session_id,
            kind=ActionKind.REACQUIRE_CONTEXT,
            payload={"shop_cfg_path": shop_cfg_path},
            priority=ActionPriority(5),
        )
        self._q.put(item)
        return action_id


# --- Brain：音频/兜底 → OCR → 意图/Jim → RAG+策略 → Claude → 风控 → 入队 ---

from collections.abc import Callable as TypingCallable  # noqa: E402

from apps.core.ai.customer_reply_routing import build_routed_reply_plan  # noqa: E402
from apps.core.ai.vision_card import (  # noqa: E402
    PendingCardContext,
    buyer_message_is_substantive,
    extract_card_json_from_rgb,
    format_card_context_for_prompt,
    heuristic_product_card_like,
    match_product_by_card,
)
from apps.core.logging.panse_chat_log import get_panse_customer_chat_log  # noqa: E402
from apps.core.logging.panse_hitl_jsonl import append_panse_hitl_record  # noqa: E402
from apps.core.ai.rag_kb import (  # noqa: E402
    format_campaign_block,
    format_gallery_block,
    format_product_block,
    retrieve_active_campaigns,
    retrieve_gallery_hints,
    retrieve_product_snippets,
    retrieve_replenish_answer,
    customization_requires_jim,
)
from apps.core.automation.vision import capture_chat_rgb, effective_chat_rect  # noqa: E402
from apps.core.configs.base_settings import BaseSettings, load_base_settings  # noqa: E402
from apps.core.configs.loader import load_shop_config  # noqa: E402
from apps.core.crm.events import (  # noqa: E402
    SessionEvent,
    ensure_session_row,
    get_session_customer_display_name,
    insert_message,
    insert_session_event,
    session_has_message_today,
    set_manual_hold,
)
from apps.core.crm.policy_repo import (  # noqa: E402
    PolicyRow,
    ensure_policy_row,
    get_policy,
    session_get_state,
    session_set_anger,
    session_set_followup,
)
from apps.core.crm.db import connect, init_db  # noqa: E402
from apps.core.intent.classify import classify_buyer_text  # noqa: E402
from apps.core.ocr.buyer_extract import extract_buyer_message_block  # noqa: E402
from apps.core.ocr.dual_engine import get_dual_ocr_engine  # noqa: E402
from apps.core.orchestrator.models import NewMessageEvent  # noqa: E402
# v1.6.6 修 NameError：这 5 个 reply_guards 符号在本文件被全文多处使用，但此前
# 仅在两个局部分支做函数内 import；转人工兜底等其它分支不在该作用域 → NameError →
# 兜底崩溃、不回复也不发兜底话术。改为模块级 import 一次，覆盖所有用法。
from apps.core.orchestrator.reply_guards import (  # noqa: E402
    is_echo_or_noise_buyer_text,
    is_only_opening_after_strip,
    normalize_buyer_digest,
    should_send_welcome,
    should_skip_duplicate_buyer,
)
from apps.core.push.service import push_all  # noqa: E402
from apps.core.risk_guard.guard import (  # noqa: E402
    check_llm_segments,
    check_money_hard_block_buyer,
    check_outbound_text,
    format_blacklist_for_prompt,
    load_phrase_blacklist,
)
from apps.core.orchestrator import health as companion_health  # noqa: E402
from apps.core.runtime_paths import default_few_shot_path, default_sqlite_db_path  # noqa: E402
from apps.core.strategy.copy import DEFAULT_REPLENISH_REPLY, HANDOFF_SOOTHE_LINE, load_fallback_phrases  # noqa: E402
from apps.core.strategy.discount import discount_round_hint  # noqa: E402
from apps.core.strategy.jim_takeover_mode import resolve_price_photo_full_takeover  # noqa: E402

LogFn = TypingCallable[[str], None]
SensitiveReviewFn = TypingCallable[[dict], bool]


def _today_iso() -> str:
    return time.strftime("%Y-%m-%d")


def _derive_greeting_customer_key(anchor_nick: str | None, buyer_text: str) -> str:
    """
    v1.6.28：派生稳定的客户标识，用于「同日已问候」去重。

    优先用昵称锚定 anchor_nick；为空时取 buyer_text 中首个中文字符之前的拉丁/数字段
    （千牛把买家昵称放在留言前，如 "kid_betsy 是实木吗" → "kid_betsy"）。
    greeting_log 内部再做去空格+小写归一化，故 OCR 的 "kid _betsy"/"kid_betsy" 空格差异不影响匹配。
    """
    src = (anchor_nick or "").strip()
    if not src:
        bt = (buyer_text or "").strip()
        m = re.match(r"^[\sA-Za-z0-9_.\-]{2,}", bt)
        src = m.group(0).strip() if m else ""
    return src


def _session_manual_hold(conn, session_id: str) -> bool:
    row = conn.execute("SELECT manual_hold FROM sessions WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
    return bool(row and int(row[0] or 0) == 1)


def _strong_reminder_active(pol: PolicyRow) -> bool:
    if not pol.strong_reminder:
        return False
    until = pol.strong_reminder_until
    if not until:
        return True
    try:
        return str(until)[:10] >= _today_iso()
    except Exception:
        return True


class PipelineOrchestrator:
    """
    Brain：非执行器线程内跑 OCR/LLM；通过 enqueue_* 向 SequentialExecutor 投递物理发送。
    """

    def __init__(
        self,
        *,
        shop_cfg_path: Path,
        source_id: str,
        session_id: str,
        pipeline: EventPipeline,
        db_path: Path | None = None,
        settings: BaseSettings | None = None,
        log: LogFn | None = None,
        few_shot_path: Path | None = None,
        sensitive_review_fn: SensitiveReviewFn | None = None,
    ) -> None:
        self._shop_cfg_path = shop_cfg_path
        self._source_id = source_id
        self._session_id = session_id
        self._pipeline = pipeline
        self._db_path = db_path or default_sqlite_db_path()
        self._settings = settings or load_base_settings()
        self._log = log or (lambda _m: None)
        self._few_shot_path = few_shot_path or default_few_shot_path()
        self._sensitive_review_fn: SensitiveReviewFn | None = sensitive_review_fn
        self._lock = threading.Lock()
        self._price_round: dict[str, int] = {}
        self._welcome_last_at: float = 0.0
        self._last_buyer_digest: str = ""
        self._last_buyer_handled_at: float = 0.0
        self._visual_quiet_until: float = 0.0
        # v1.6.14 同日跳欢迎语的内存兜底：session_id -> 最近发欢迎语日期(YYYY-MM-DD)
        self._today_greeted: dict[str, str] = {}
        # v1.6.25 防自身回声：最近发出的回复(规范化摘要)环形缓存。OCR 抽出的 buyer_text
        # 若与其中任一高度相似，判为"读到自己刚发的话"，丢弃本轮（见 _looks_like_own_echo）。
        self._recent_outbound: list[str] = []
        self._hosting_started_at: float = time.monotonic()
        self._pending_card: PendingCardContext | None = None
        self._pause_lock = threading.Lock()
        self._brain_paused = False
        self._session_list_prev_rgb: Any = None
        self._last_nonempty_buyer_monotonic: float = 0.0
        self._pending_retry_event: NewMessageEvent | None = None
        self._idle_stop = threading.Event()
        self._idle_thread = threading.Thread(
            target=self._idle_minimize_loop, name="QianniuIdleMinimize", daemon=True
        )
        self._idle_thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="BrainHeartbeat", daemon=True
        )
        self._heartbeat_thread.start()
        self._chat_rescan_thread = threading.Thread(
            target=self._chat_rescan_loop, name="ChatRescan", daemon=True
        )
        self._chat_rescan_thread.start()
        self._message_input_mode: str = "ocr"
        # v1.6.14 客服存档：注册"我方回复"出站钩子（买家入站在 OCR 确认后单点存）
        try:
            self._pipeline._archive_hook = self._archive_outbound_message
        except Exception:
            pass

    def _archive_message(self, direction: str, text: str) -> None:
        """v1.6.14 客服存档：把一条消息写入 messages 表（direction='in'买家 / 'out'我方）。
        软附加：任何异常只吞不抛，绝不影响接待。先 ensure_session_row 保证 FK。"""
        try:
            t = (text or "").strip()
            if not t:
                return
            # v1.6.25：记录我方回复，供防自身回声比对
            if direction == "out":
                _d = normalize_buyer_digest(t)
                if _d:
                    self._recent_outbound.append(_d)
                    if len(self._recent_outbound) > 8:
                        self._recent_outbound = self._recent_outbound[-8:]
            shop = load_shop_config(self._shop_cfg_path)
            brand_id = shop.brand_id
            shop_id = shop.shop_id or (shop.brand_id + ":" + shop.shop_code)
            conn = connect(self._db_path)
            init_db(conn)
            try:
                ensure_session_row(
                    conn,
                    session_id=self._session_id,
                    brand_id=brand_id,
                    shop_id=shop_id,
                    source_id=self._source_id,
                    shop_code=getattr(shop, "shop_code", ""),
                    shop_display_name=getattr(shop, "shop_display_name", ""),
                )
                insert_message(
                    conn,
                    session_id=self._session_id,
                    brand_id=brand_id,
                    shop_id=shop_id,
                    source_id=self._source_id,
                    direction=direction,
                    text=t,
                )
            finally:
                conn.close()
        except Exception:
            pass

    def _archive_outbound_message(self, _source_id: str, _session_id: str, text: str) -> None:
        """EventPipeline 出站钩子回调：记录我方回复。"""
        self._archive_message("out", text)

    def _looks_like_own_echo(self, buyer_text: str) -> bool:
        """
        v1.6.25：判断 OCR 抽出的 buyer_text 是否其实是「我方刚发出的回复」被又读了一遍。

        防止视觉哨兵/聊天区差分/定时重扫把我方刚发的气泡当成新买家消息，触发自循环。
        用最近发出的回复(规范化摘要)与之做字符级相似度比对，≥0.82 视为自身回声。
        """
        d = normalize_buyer_digest(buyer_text)
        if not d or not self._recent_outbound:
            return False
        try:
            import difflib

            for od in self._recent_outbound:
                if not od:
                    continue
                if d == od or (len(d) >= 6 and (d in od or od in d)):
                    return True
                if difflib.SequenceMatcher(None, d, od).ratio() >= 0.82:
                    return True
        except Exception:
            return False
        return False

    def _handle_price_kb_miss(
        self,
        conn,
        *,
        shop,
        buyer_text: str,
        cust_label: str,
        cycle_buyer_digest: str,
        trigger: str,
    ) -> None:
        """
        v1.6.25：询价/拍下走「知识库优先」，但 RAG 未命中(takeover/低置信)时的兜底：
        发可配置「活动期价格兜底话术」（query_rewrite.yaml inquiry_auto_reply.price_quote_template）
        + 跳强提醒（让主理人接手核价）+ 挂人工。不再额外发安抚语，避免重复回复。
        """
        try:
            from apps.core.ai.input_quality_gate import load_inquiry_templates

            price_tpl, _order_tpl = load_inquiry_templates()
        except Exception:
            price_tpl = ""
        phrase = (price_tpl or "").strip() or "您好，具体到手价请以购物车结算价为准呢~"
        self._pipeline.enqueue_send_text(
            self._source_id,
            self._session_id,
            text=phrase,
            buyer_digest=cycle_buyer_digest,
            chat_log_meta={
                "customer_label": cust_label,
                "intent_label": "询价(知识库未命中)兜底",
                "kb_node": "inquiry_auto_reply",
            },
        )
        self._log("询价/拍下：知识库未命中 → 发活动期兜底话术 + 跳强提醒 + 挂人工")
        try:
            preview = (buyer_text or "").strip().replace("\n", " ")[:400]
            if (
                self._settings.push_serverchan_sendkey.strip()
                or self._settings.push_pushplus_token.strip()
                or self._settings.push_wecom_webhook.strip()
                or self._settings.push_host_alert_url.strip()
            ):
                body = (
                    f"[询价待核价] trigger={trigger}\nsession={self._session_id}\n"
                    f"客户：{preview or '（无文字摘要）'}"
                )
                for r in push_all(self._settings, title="[强提醒] 询价待核价", body=body):
                    self._log(f"强提醒推送 {r.channel} ok={r.ok}")
        except Exception as e:
            self._log(f"询价强提醒推送异常（忽略）：{e!r}")
        try:
            set_manual_hold(conn, self._session_id, manual_hold=True)
        except Exception as e:
            self._log(f"询价挂人工异常（忽略）：{e!r}")
        self._last_buyer_digest = normalize_buyer_digest(buyer_text)
        self._last_buyer_handled_at = time.monotonic()
        self._visual_quiet_until = time.monotonic() + 25.0

    def _send_spec_clarify(
        self, *, buyer_text: str, cust_label: str, cycle_buyer_digest: str,
    ) -> None:
        """
        v1.6.27：泛问属性(材质/尺寸/颜色/规格)但知识库未命中、且没指定哪款产品时，
        反问澄清「您说的哪一款呢」（可在面板编辑），而不是直接转人工——对话不中断。
        """
        from apps.core.strategy.copy import load_fallback_phrases

        clarify = (load_fallback_phrases().spec_clarify or "").strip() or (
            "您说的哪一款呢？方便发下产品链接么？或者说一下产品名称，我来帮您看下呢~"
        )
        self._pipeline.enqueue_send_text(
            self._source_id,
            self._session_id,
            text=clarify,
            buyer_digest=cycle_buyer_digest,
            chat_log_meta={
                "customer_label": cust_label,
                "intent_label": "泛问属性-反问澄清",
                "kb_node": "fallback:spec_clarify",
            },
        )
        self._log("泛问属性(材质/尺寸/颜色等)但知识库未命中 → 反问澄清是哪款（不转人工）")
        self._last_buyer_digest = normalize_buyer_digest(buyer_text)
        self._last_buyer_handled_at = time.monotonic()
        self._visual_quiet_until = time.monotonic() + 20.0
        self._cycle_produced_reply = True

    def set_message_input_mode(self, mode: str) -> None:
        m = (mode or "ocr").strip().lower()
        self._message_input_mode = "db" if m == "db" else "ocr"

    def shutdown(self) -> None:
        """停止 Brain 时调用：结束空闲最小化 / 聊天重扫后台线程。"""
        self._idle_stop.set()
        t = getattr(self, "_idle_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=4.0)
        t2 = getattr(self, "_chat_rescan_thread", None)
        if t2 is not None and t2.is_alive():
            t2.join(timeout=4.0)

    def _heartbeat_loop(self) -> None:
        """每 15 秒打一条心跳日志，帮助确认 Brain 线程存活、是否处于暂停状态。"""
        while not self._idle_stop.wait(15.0):
            paused = self._brain_paused
            if paused:
                self._log("Brain 心跳：当前处于暂停/人工接管状态，等待恢复")
            else:
                self._log("Brain 心跳：待命中，等待声音/视觉触发")

    def _idle_minimize_loop(self) -> None:
        # v1.6.0：每 25s 心跳；用户活动检测；ShowWindow 连续失败 3 次→SetWindowPos 兜底
        minimize_fail_count = 0
        while not self._idle_stop.wait(25.0):
            try:
                shop = load_shop_config(self._shop_cfg_path)
                qn = shop.qianniu
                if qn is None:
                    continue
                sec = int(getattr(qn, "idle_auto_minimize_seconds", 0) or 0)
                if sec <= 0:
                    continue
                ref = float(self._last_nonempty_buyer_monotonic)
                if ref <= 0:
                    # 从未识别到买家消息：以托管启动时间为基准
                    ref = float(self._hosting_started_at)
                if ref <= 0:
                    continue
                idle_s = time.monotonic() - ref
                # 心跳：让主理人在日志里看得到这个机制在跑，没静默
                self._log(f"空闲最小化检查：距上次买家消息 {idle_s:.0f}s（阈值 {sec}s）")
                if idle_s < float(sec):
                    continue

                # v1.6.0 用户活动检测：主理人最近 30s 在动鼠标/键盘则不打断
                from apps.core.channels.qianniu.window_ops import (
                    minimize_qianniu_main,
                    force_hide_offscreen,
                    get_seconds_since_last_user_input,
                )
                user_idle_s = get_seconds_since_last_user_input()
                if 0 <= user_idle_s < 30.0:
                    self._log(
                        f"空闲最小化：检测到主理人最近 {user_idle_s:.0f}s 在动鼠标/键盘，"
                        f"本轮跳过（避免打断手操作）"
                    )
                    continue

                if minimize_qianniu_main(shop):
                    minimize_fail_count = 0
                    self._log(f"空闲 ≥{sec}s 未识别到买家新留言，已最小化千牛窗口")
                    self._last_nonempty_buyer_monotonic = time.monotonic()
                else:
                    minimize_fail_count += 1
                    self._log(
                        f"空闲最小化失败 ({minimize_fail_count}/3)：ShowWindow(SW_MINIMIZE) 未生效"
                    )
                    if minimize_fail_count >= 3:
                        # 兜底：把窗口挪到屏幕外
                        if force_hide_offscreen(shop):
                            self._log(
                                "空闲最小化兜底：SW_MINIMIZE 已连续失败 3 次，"
                                "已用 SetWindowPos 将千牛挪到屏幕外 (-2000,-2000)"
                            )
                            minimize_fail_count = 0
                            self._last_nonempty_buyer_monotonic = time.monotonic()
                        else:
                            self._log(
                                "空闲最小化兜底也失败：SetWindowPos 未生效；"
                                "请检查千牛窗口是否被关闭/进程已退出"
                            )
            except Exception as e:
                self._log(f"空闲最小化检查异常：{e!r}")

    def _chat_rescan_loop(self) -> None:
        """每 N 秒 OCR 当前聊天区，若检测到未回复的买家新消息则触发处理。"""
        # 启动后先等一个完整周期再开始（避免和 hosting grace 冲突）
        if self._idle_stop.wait(65.0):
            return
        while not self._idle_stop.is_set():
            try:
                from apps.core.ai.input_quality_gate import load_session_detection_settings

                sd = load_session_detection_settings()
                interval = max(10.0, float(sd.chat_rescan_interval_s))
            except Exception:
                interval = 60.0
            if self._idle_stop.wait(interval):
                return
            # 暂停状态下不扫
            with self._pause_lock:
                if self._brain_paused:
                    continue
            # 仅 OCR 模式下执行
            if self._message_input_mode != "ocr":
                continue
            # 千牛最小化 / 不在前台时不扫（不干扰用户，且防止抓到盖在上面的其它窗口）
            try:
                import ctypes

                shop = load_shop_config(self._shop_cfg_path)
                # v1.6.21 根治"读到 Claude/本程序窗口文字"：
                # 定时重扫用 capture_chat_rgb() 抓的是固定屏幕矩形 ocr_chat_rect（绝对像素），
                # 千牛被其它窗口（如 Claude Code / 本程序）遮挡时，会抓到盖在上面的窗口，
                # OCR 把别的窗口文字误当买家消息 → 触发幻影 chat_rescan → 给真实客户发错回复。
                # 因此截图前必须确认：千牛 HWND == 当前前台窗口（与聊天区哨兵 _tick 一致）。
                if shop.qianniu is None:
                    continue
                from apps.core.channels.qianniu.win_hwnd import (
                    find_qianniu_main_hwnd_best_effort,
                )

                hwnd = find_qianniu_main_hwnd_best_effort()
                if not hwnd:
                    continue
                _u32 = ctypes.windll.user32
                if _u32.IsIconic(hwnd):
                    continue
                if int(_u32.GetForegroundWindow()) != hwnd:
                    continue
            except Exception:
                pass
            # 快速检查：截图 + OCR → 比较摘要
            try:
                shop = load_shop_config(self._shop_cfg_path)
                img = capture_chat_rgb(shop)
                ocr = get_dual_ocr_engine()
                result = ocr.recognize(img)
                # v1.3.98：必须用 effective_chat_rect（含 bottom 自动扩展），
                # 否则 roi_height 与实际截图不一致，会把最新消息切掉
                eff_rect = effective_chat_rect(shop)
                h = int(eff_rect.height())
                w = int(eff_rect.width())
                buyer_text = extract_buyer_message_block(
                    result.spans, roi_height=h, roi_width=w
                )
                if not (buyer_text or "").strip():
                    continue
                from apps.core.orchestrator.reply_guards import (
                    normalize_buyer_digest,
                    is_echo_or_noise_buyer_text,
                )

                digest = normalize_buyer_digest(buyer_text)
                if digest == self._last_buyer_digest:
                    continue
                if is_echo_or_noise_buyer_text(buyer_text):
                    continue
                self._log(
                    f"[定时重扫] 检测到未处理买家消息：{buyer_text!r:.60}，"
                    f"触发 chat_rescan"
                )
                ev = NewMessageEvent(
                    source_id=self._source_id,
                    session_id=self._session_id,
                    trigger="chat_rescan",
                )
                self.handle_new_message_event(ev)
            except Exception as e:
                self._log(f"[定时重扫] 异常：{e!r}")

    def pause_brain_cycle(self) -> None:
        with self._pause_lock:
            self._brain_paused = True
        self._log("Brain 已暂停（人工观测模式）")

    def resume_brain_cycle(self) -> None:
        with self._pause_lock:
            self._brain_paused = False
        self._log("Brain 已恢复自动接待")

    def is_brain_paused(self) -> bool:
        with self._pause_lock:
            return bool(self._brain_paused)

    def _take_pending_card_if_valid(self) -> PendingCardContext | None:
        p = self._pending_card
        self._pending_card = None
        if p is None or p.is_expired():
            return None
        return p

    def _set_pending_card(self, ctx: PendingCardContext) -> None:
        self._pending_card = ctx

    def _read_few_shot(self) -> str:
        p = self._few_shot_path
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8")[:8000]
            except OSError:
                pass
        return "语气亲切简短，口语化；不要做超长段落；少用客服腔模板句。"

    def _enqueue_routed_plan_outbound(
        self,
        *,
        plan,
        fluff: list[str],
        log_meta: dict,
        cust_label: str,
        brand_id: str,
        shop_id: str,
        buyer_digest: str = "",
    ) -> None:
        blist = fluff
        for path_str, img_id in plan.image_send_items:
            pth = Path(path_str)
            if not pth.is_file():
                self._log(f"图库自动发图跳过（文件不存在）：{path_str!r}")
                continue
            self._pipeline.enqueue_send_image(
                self._source_id,
                self._session_id,
                image_path=str(pth.resolve()),
                priority=88,
                chat_log_meta={
                    "customer_label": cust_label,
                    "intent_label": plan.intent_label,
                    "kb_node": f"图库自动:{img_id}",
                    "image_library_id": img_id,
                },
            )
        for seg in plan.segments:
            oc = check_outbound_text(seg, extra_banned_phrases=blist)
            if not oc.allowed:
                self._log(f"风控拦截发送：{oc.reason}")
                companion_health.record_health_event(
                    "risk_intercept_outbound",
                    {"reason": oc.reason[:400], "segment_preview": seg[:160]},
                    brand_id=brand_id,
                    shop_id=shop_id,
                )
                continue
            self._pipeline.enqueue_send_text(
                self._source_id,
                self._session_id,
                text=seg,
                buyer_digest=buyer_digest,
                chat_log_meta=log_meta,
            )

    def handle_new_message_event(self, ev: NewMessageEvent) -> None:
        with self._pause_lock:
            if self._brain_paused:
                self._log("观测模式：不触发自动接待")
                return
        # 注意：首句问候「您好，在的呢～」不得在 Brain 线程启动前入队——否则会与窗口置前竞态，
        # SOOTHE_WAIT 往往在 maybe_prepare_window_for_capture 之前就执行，表现为听到叮咚却界面不弹出。
        threading.Thread(target=self._run_cycle_wrapped, args=(ev,), name="BrainCycle", daemon=True).start()

    def _run_cycle_wrapped(self, ev: NewMessageEvent) -> None:
        with self._pause_lock:
            if self._brain_paused:
                return
        if not self._lock.acquire(blocking=False):
            self._log("跳过本轮：上一轮尚未结束，已排队等待")
            self._pending_retry_event = ev
            return
        self._cycle_produced_reply = False
        try:
            self._execute(ev)
        except Exception as e:
            # 捕获所有未处理异常并打到日志，防止线程静默崩溃
            self._log(f"[致命] Brain 本轮异常崩溃：{e!r}  trigger={ev.trigger}")
        finally:
            self._lock.release()
        self._drain_pending()
        if self._cycle_produced_reply:
            self._post_cycle_sweep(ev)

    def _drain_pending(self) -> None:
        """处理在上一轮忙碌期间排队的事件（仅保留最新一条）。"""
        pending = self._pending_retry_event
        if pending is None:
            return
        self._pending_retry_event = None
        with self._pause_lock:
            if self._brain_paused:
                return
        self._log(f"补处理排队事件 trigger={pending.trigger}")
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._execute(pending)
        except Exception as e:
            self._log(f"[致命] 排队事件处理异常：{e!r}  trigger={pending.trigger}")
        finally:
            self._lock.release()

    def _post_cycle_sweep(self, ev: NewMessageEvent) -> None:
        """主处理完毕后：扫描剩余未读会话 → 逐个发送安抚话术（「稍等」）。

        v1.3.93 安全门：ManualHold 状态下不扫（Jim 兜底转人工后绝不再批量发"稍等"，
        否则会对其他会话刷屏被淘宝风控）。
        """
        if ev.trigger in ("batch_soothe", "chat_rescan", "chat_area_diff"):
            return
        try:
            from apps.core.ai.input_quality_gate import load_session_detection_settings

            sd = load_session_detection_settings()
            if not sd.batch_soothe_enabled:
                return
            shop = load_shop_config(self._shop_cfg_path)
            if shop.qianniu is None or not shop.qianniu.unread_session_switch:
                return

            # 安全门：当前会话若已 ManualHold（被 Jim 兜底），不再批量安抚
            try:
                conn_check = connect(self._db_path)
                init_db(conn_check)
                try:
                    if _session_manual_hold(conn_check, self._session_id):
                        self._log(
                            "批量安抚已停：当前会话处于人工接管（ManualHold），"
                            "不再对其他未读会话发送'稍等'，避免刷屏触发风控"
                        )
                        return
                finally:
                    conn_check.close()
            except Exception as e:
                self._log(f"批量安抚 ManualHold 检查异常（保险起见跳过）：{e!r}")
                return

            from apps.core.channels.qianniu.session_list_unread import (
                batch_soothe_remaining_unread,
            )

            soothe_text = load_fallback_phrases().batch_soothe
            sent = batch_soothe_remaining_unread(
                shop,
                soothe_text,
                self._log,
                max_sessions=sd.batch_soothe_max_sessions,
            )
            if sent > 0:
                self._log(f"批量安抚完成：已向 {sent} 个未读会话发送「{soothe_text}」")
        except Exception as e:
            self._log(f"批量安抚异常：{e!r}")

    def _jim_takeover(
        self,
        conn,
        *,
        shop,
        buyer_text: str,
        reason: str,
        policy: PolicyRow,
        customer_display: str,
        hitl_context: str = "",
        hitl_query: str = "",
        hitl_pos: list[str] | None = None,
        hitl_neg: list[str] | None = None,
        full_takeover: bool = True,
    ) -> None:
        brand_id = shop.brand_id
        shop_id = shop.shop_id or (shop.brand_id + ":" + shop.shop_code)
        companion_health.record_health_event(
            "strategy_takeover",
            {"reason": reason[:500]},
            brand_id=brand_id,
            shop_id=shop_id,
        )
        try:
            from apps.core.ai.input_quality_gate import is_metadata_noise
            from apps.core.logging.pending_qa import append_pending_qa

            append_pending_qa(
                query=buyer_text,
                noise=is_metadata_noise(buyer_text),
                reason=reason,
                session_id=self._session_id,
            )
        except Exception:
            pass
        self._settings = load_base_settings()
        if policy.jim_intercept_push and (
            self._settings.push_serverchan_sendkey.strip()
            or self._settings.push_pushplus_token.strip()
            or self._settings.push_wecom_webhook.strip()
            or self._settings.push_host_alert_url.strip()
        ):
            title = f"[Jim介入] {reason}"
            body = f"session={self._session_id}\n买家摘要：{buyer_text[:500]}"
            for r in push_all(self._settings, title=title, body=body):
                self._log(f"推送 {r.channel} ok={r.ok} {r.detail[:120]}")
        insert_session_event(
            conn,
            SessionEvent(
                event_id="",
                brand_id=brand_id,
                shop_id=shop_id,
                session_id=self._session_id,
                source_id=self._source_id,
                event_type="jim_intercept",
                payload={"reason": reason, "buyer_preview": buyer_text[:800]},
                evidence_confidence=1.0,
            ),
            force=True,
        )
        lab = (customer_display or "").strip() or self._session_id
        try:
            append_panse_hitl_record(
                query=(hitl_query or buyer_text).strip()[:2000],
                pos=list(hitl_pos or []),
                neg=list(hitl_neg or []),
                meta={
                    "event": "human_intervention",
                    "reason": reason[:500],
                    "session_id": self._session_id,
                    "context_excerpt": (hitl_context or "")[:4000],
                },
            )
        except Exception as e:
            self._log(f"HITL JSONL 写入失败：{e!r}")
        if full_takeover:
            soothe = (policy.handoff_soothe_line or "").strip() or load_fallback_phrases().handoff_soothe
            self._pipeline.enqueue_soothe_wait(
                self._source_id,
                self._session_id,
                text=soothe,
                buyer_digest=normalize_buyer_digest(buyer_text),
                chat_log_meta={
                    "customer_label": lab,
                    "intent_label": "Jim介入:兜底安抚",
                    "kb_node": reason[:240],
                },
            )
            set_manual_hold(conn, self._session_id, manual_hold=True)
            self._log(f"Jim 介入：{reason}，已发送兜底话术并排 ManualHold")
            self._cycle_produced_reply = True
        else:
            self._log(f"Jim 介入（仅推送）：{reason}，未发安抚、未设 ManualHold（策略为「仅推送」）")

    def _execute(self, ev: NewMessageEvent) -> None:
        from apps.core.orchestrator.reply_guards import (
            hosting_visual_grace_active,
            is_echo_or_noise_buyer_text,
            is_only_opening_after_strip,
            normalize_buyer_digest,
            should_send_welcome,
            should_skip_duplicate_buyer,
            visual_scan_in_quiet_period,
            HOSTING_VISUAL_GRACE_S,
        )

        self._log(f"Brain 本轮开始 trigger={ev.trigger}")
        if self._message_input_mode == "db" and ev.trigger != "db_message":
            self._log(
                f"当前为本地 DB 消息源模式，跳过声音/视觉/OCR 触发（trigger={ev.trigger}）"
            )
            return
        if self._message_input_mode != "db" and ev.trigger == "db_message":
            self._log("当前为 OCR 消息源，忽略已过期的 DB 消息事件")
            return
        if hosting_visual_grace_active(
            trigger=ev.trigger, hosting_started_at=self._hosting_started_at
        ):
            self._log(
                f"跳过本轮：托管启动后 {HOSTING_VISUAL_GRACE_S:.0f}s 内 visual_scan 不接待"
                f"（列表静止也会误触发）；请等待叮咚 audio_peak"
            )
            return
        if (
            ev.trigger != "db_message"
            and visual_scan_in_quiet_period(
                trigger=ev.trigger, quiet_until=self._visual_quiet_until
            )
        ):
            self._log(
                "跳过本轮：视觉哨兵冷却中（上轮刚结束，避免选中会话/列表高亮反复误触）"
            )
            return

        shop = load_shop_config(self._shop_cfg_path)
        brand_id = shop.brand_id
        shop_id = shop.shop_id or (shop.brand_id + ":" + shop.shop_code)

        self._settings = load_base_settings()

        # chat_rescan / chat_area_diff 触发：千牛已在前台且当前会话已选中，跳过窗口准备和会话切换
        _skip_window_and_switch = ev.trigger in ("chat_rescan", "chat_area_diff")

        # --- P0 #2：audio_peak 先验双判 ---
        # audio_peak 触发且千牛已在前台时，先 OCR 当前聊天区：
        # 如果当前会话有未处理的新消息 → 直接处理，跳过步骤 2（避免盲点红标切走）
        _pre_ocr_img = None
        _pre_ocr_result = None
        _pre_ocr_buyer = ""
        if (
            ev.trigger == "audio_peak"
            and shop.qianniu is not None
            and not _skip_window_and_switch
        ):
            try:
                from apps.core.channels.qianniu.window_ops import _is_qianniu_foreground

                if _is_qianniu_foreground(shop):
                    _pre_img = capture_chat_rgb(shop)
                    _pre_ocr_eng = get_dual_ocr_engine()
                    _pre_result = _pre_ocr_eng.recognize(_pre_img)
                    # v1.3.98：用 effective_chat_rect 保证 roi 尺寸与截图一致
                    _eff_pre = effective_chat_rect(shop)
                    _h_pre = int(_eff_pre.height())
                    _w_pre = int(_eff_pre.width())
                    _pre_buyer = extract_buyer_message_block(
                        _pre_result.spans, roi_height=_h_pre, roi_width=_w_pre
                    )
                    _pre_digest = normalize_buyer_digest(_pre_buyer)

                    if (
                        (_pre_buyer or "").strip()
                        and _pre_digest != self._last_buyer_digest
                        and not is_echo_or_noise_buyer_text(
                            _pre_buyer,
                            seller_display_name=getattr(shop, "shop_display_name", ""),
                        )
                    ):
                        self._log(
                            f"先验OCR：当前会话有新消息 {_pre_buyer!r:.60}，"
                            f"跳过会话切换（仅决策，稍后走正规延时重读）"
                        )
                        # v1.6.10：先验OCR 只用于"是否跳过切会话"的决策，
                        # 不再把这张"抢拍"的截图/OCR 结果带到下游复用。
                        # 抢拍发生在 audio_peak 一响的瞬间，无 capture_delay、画面常未渲染完，
                        # 易把"这个有现货么"误读成"·回茶"等碎片 → 直接喂 LLM → 回"嗯嗯好的"。
                        # 现仅置 skip 标志，让流程落到正规 else 分支
                        # （capture_delay + 稳定帧 + 重新 OCR），保证喂给 LLM 的是高质文本。
                        _skip_window_and_switch = True
                    else:
                        self._log(
                            "先验OCR：当前会话无新内容（digest相同或噪声），"
                            "继续正常会话切换"
                        )
            except Exception as e:
                self._log(f"先验OCR异常（不影响后续流程）：{e!r}")

        # --- P0 #4：前台直通车 ---
        # 千牛已在前台且非最小化时，跳过完整的 bring_to_front 流程
        # 步骤1/2：须在 ManualHold 判断之前执行——人工接管时仍要把千牛弹出并尽量点黄条会话
        if shop.qianniu is not None and not _skip_window_and_switch:
            try:
                import ctypes as _ctypes_step1

                from apps.core.channels.qianniu.window_ops import (
                    _is_qianniu_foreground,
                    maybe_prepare_window_for_capture,
                )
                from apps.core.channels.qianniu.win_hwnd import (
                    find_qianniu_main_hwnd_best_effort,
                )

                _hwnd_check = find_qianniu_main_hwnd_best_effort()
                _qn_foreground = (
                    _hwnd_check
                    and _is_qianniu_foreground(shop)
                    and not _ctypes_step1.windll.user32.IsIconic(_hwnd_check)
                )

                if _qn_foreground:
                    self._log("步骤1：千牛已在前台，跳过窗口准备 ✓")
                else:
                    self._log(f"步骤1：准备千牛窗口（{ev.trigger}）")
                    maybe_prepare_window_for_capture(shop, ev.trigger, self._log)
                    from apps.core.channels.qianniu.bring_to_front import (
                        wait_window_ready,
                    )

                    hwnd_ready = find_qianniu_main_hwnd_best_effort()
                    if hwnd_ready:
                        if wait_window_ready(hwnd_ready, log=self._log, shop=shop):
                            self._log("步骤1b：千牛窗口已就绪（前台且非最小化）")
                        else:
                            self._log(
                                "步骤1b：窗口就绪超时，仍尝试会话切换（坐标可能落空）"
                            )
                    self._log("步骤1完成")
            except Exception as e:
                self._log(f"千牛窗口准备失败：{e!r}")

        session_switched = False
        if (
            ev.trigger != "db_message"
            and not _skip_window_and_switch
            and shop.qianniu is not None
            and shop.qianniu.unread_session_switch
        ):
            try:
                from apps.core.channels.qianniu.session_list_unread import (
                    maybe_switch_unread_session,
                )

                self._log(
                    "步骤2：切换未读会话（两次黄条扫描，第2次优先；无黄条再红标）…"
                )
                self._session_list_prev_rgb, switched = maybe_switch_unread_session(
                    shop,
                    self._session_list_prev_rgb,
                    ev,
                    self._log,
                )
                session_switched = bool(switched)
                self._log(f"步骤2完成：switched={switched}")
                if not switched:
                    self._log(
                        "步骤2：未点击会话列表（无黄条/红标或已在当前会话）；"
                        "将对当前聊天区 OCR（已选中会话常无黄条）"
                    )
                elif ev.trigger == "audio_peak":
                    self._log(
                        "步骤2提示：已点选列表行；若界面本来就在该会话，"
                        "属正常（新消息在选中会话上不一定出现黄条）"
                    )
            except Exception as e:
                self._log(f"未读会话切换流程异常：{e!r}")
        elif ev.trigger != "db_message" and not _skip_window_and_switch and shop.qianniu is not None:
            sl = shop.qianniu.session_list_rect
            if sl is None or sl.width() < 8:
                self._log(
                    "步骤2：已跳过未读切换（未配置 session_list_rect，无法检测左侧黄条）"
                )
            elif not shop.qianniu.unread_session_switch:
                self._log(
                    "步骤2：已跳过未读切换（店铺 YAML 中 unread_session_switch: false）"
                )

        conn = connect(self._db_path)
        init_db(conn)
        try:
            ensure_session_row(
                conn,
                session_id=self._session_id,
                brand_id=brand_id,
                shop_id=shop_id,
                source_id=self._source_id,
                shop_code=getattr(shop, "shop_code", ""),
                shop_display_name=getattr(shop, "shop_display_name", ""),
            )
            ensure_policy_row(conn, brand_id=brand_id, shop_id=shop_id)
            policy = get_policy(conn, brand_id=brand_id, shop_id=shop_id)

            if _session_manual_hold(conn, self._session_id):
                self._log(
                    "ManualHold：跳过自动回复（千牛已尝试置前/点选会话）；"
                    "请在控制台点「恢复 AI 托管」或处理完本单后再测"
                )
                return
        finally:
            conn.close()

        img = None
        result = None
        buyer_text = ""
        card_hint = False
        h = 600
        w = None
        # v1.6.1 修 UnboundLocalError：在函数顶部初始化，所有分支都能安全用；
        # 空串时 SequentialPipeline._budget_check_and_record 会直接放行
        cycle_buyer_digest = ""

        if ev.trigger == "mobile":
            # --- 手机端千牛通道（trigger="mobile"）---
            # 跳过 OCR / HWND / 窗口准备 / 会话切换；buyer_text 由适配器直接传入。
            # 发送动作由 MobileBrainBridge._mobile_executor_loop() 拦截执行。
            payload = dict(ev.payload) if ev.payload else {}
            buyer_text = str(payload.get("buyer_text") or "").strip()
            self._log(f"步骤 Mobile：手机端消息 buyer_text={buyer_text!r:.80}")
            if not buyer_text:
                self._log("跳过本轮：mobile buyer_text 为空")
                return
            # buyer_text 已就绪，h/w/card_hint 设默认值后直通到意图路由段
            h = 600
            w = None
            card_hint = False
            cycle_buyer_digest = normalize_buyer_digest(buyer_text)  # v1.6.1

        elif ev.trigger == "db_message":
            payload = dict(ev.payload) if ev.payload else {}
            buyer_text = str(payload.get("buyer_text") or "").strip()
            self._log(f"步骤 DB：本地库消息 buyer_text={buyer_text!r:.80}")
            cycle_buyer_digest = normalize_buyer_digest(buyer_text)  # v1.6.1
            if should_skip_duplicate_buyer(
                buyer_text=buyer_text,
                last_digest=self._last_buyer_digest,
                last_handled_at=self._last_buyer_handled_at,
            ):
                self._log("跳过本轮：与上一条 DB 消息相同")
                return
        elif _pre_ocr_img is not None and _pre_ocr_result is not None:
            # --- 先验 OCR 快速通道：复用 audio_peak 先验双判的截图和 OCR 结果 ---
            from apps.core.ai.input_quality_gate import (
                load_session_detection_settings,
                load_time_alignment_settings,
                resolve_capture_delay_s,
            )
            from apps.core.automation.vision import maybe_save_debug_chat_snapshot
            from apps.core.ocr.chat_time_align import assess_chat_time_alignment_from_spans

            sd = load_session_detection_settings()
            img = _pre_ocr_img
            result = _pre_ocr_result
            buyer_text = _pre_ocr_buyer
            capture_ts = datetime.now()
            snap_path = maybe_save_debug_chat_snapshot(img, "pre_ocr_reuse")
            if snap_path is not None:
                self._log(f"步骤4-5：复用先验OCR截图 {snap_path}")
            self._log(
                f"步骤4-5：复用先验OCR结果 buyer_text={buyer_text!r:.80}"
            )
            # v1.6.1：先验复用分支也算 digest，享受 SessionReplyBudget 保护
            cycle_buyer_digest = normalize_buyer_digest(buyer_text)
        else:
            from apps.core.ai.input_quality_gate import (
                load_session_detection_settings,
                load_time_alignment_settings,
                resolve_capture_delay_s,
            )
            from apps.core.automation.vision import maybe_save_debug_chat_snapshot
            from apps.core.ocr.chat_time_align import assess_chat_time_alignment_from_spans

            sd = load_session_detection_settings()
            capture_delay = resolve_capture_delay_s(
                float(getattr(self._settings, "capture_delay_s", 1.5))
            )
            if session_switched and sd.post_switch_extra_delay_s > 0:
                capture_delay += sd.post_switch_extra_delay_s
                self._log(
                    f"步骤3：会话已切换，额外等待 {sd.post_switch_extra_delay_s:.1f}s "
                    f"（合计 {capture_delay:.1f}s）后截图…"
                )
            elif capture_delay > 0:
                self._log(f"步骤3：等待 {capture_delay:.1f}s 后截图…")
            if capture_delay > 0:
                time.sleep(capture_delay)

            # v1.6.21 防御纵深：截图前最后确认千牛在前台。
            # capture_chat_rgb() 抓固定屏幕矩形 ocr_chat_rect（绝对像素），若等待期间
            # 焦点被抢（千牛被 Claude/本程序遮挡），会抓到盖在上面的窗口 → OCR 误读为买家消息。
            # 不在前台：非跳过类触发补一次置前；仍失败则跳过本轮（绝不截错窗口）。
            try:
                from apps.core.channels.qianniu.window_ops import _is_qianniu_foreground

                if shop.qianniu is not None and not _is_qianniu_foreground(shop):
                    if not _skip_window_and_switch:
                        from apps.core.channels.qianniu.window_ops import (
                            maybe_prepare_window_for_capture,
                        )

                        self._log("步骤4前：千牛不在前台，补一次置前以防抓到其它窗口…")
                        maybe_prepare_window_for_capture(shop, ev.trigger, self._log)
                        time.sleep(0.2)
                    if not _is_qianniu_foreground(shop):
                        self._log(
                            "步骤4：千牛仍不在前台，跳过本轮截图"
                            "（避免把 Claude/其它窗口文字误当买家消息）"
                        )
                        return
            except Exception as _e_fg:
                self._log(f"步骤4前台校验异常（忽略，继续截图）：{_e_fg!r}")

            self._log("步骤4：截图中…")
            capture_ts = datetime.now()
            try:
                img = capture_chat_rgb(shop)
            except Exception as e:
                self._log(f"截图失败：{e!r}")
                return

            # --- P1 #6：稳定帧确认 ---
            # 连续截图比较，间隔 80ms，确认画面稳定后再 OCR（消除滚动/动画/图片加载）
            import numpy as np

            _STABILITY_MAX_WAIT = 0.6
            _STABILITY_THRESHOLD = 2.0
            _stability_start = time.monotonic()
            for _si in range(3):
                if time.monotonic() - _stability_start > _STABILITY_MAX_WAIT:
                    break
                time.sleep(0.08)
                try:
                    img2 = capture_chat_rgb(shop)
                except Exception:
                    break
                diff = float(np.mean(np.abs(img.astype(np.int32) - img2.astype(np.int32))))
                if diff < _STABILITY_THRESHOLD:
                    break  # 稳定
                img = img2  # 用更新的帧继续比较
            capture_ts = datetime.now()

            snap_path = maybe_save_debug_chat_snapshot(img, ev.trigger or "capture")
            if snap_path is not None:
                self._log(f"步骤4：调试截图已保存 {snap_path}")

            self._log("步骤5：OCR 识别中…")
            ocr = get_dual_ocr_engine()
            result = ocr.recognize(img)
            # v1.3.98：用 effective_chat_rect（含 bottom 自动扩展），
            # 否则 roi_height 与实际截图不一致，extract_buyer_message_block
            # 会把最新消息当成"在工具栏区"剪掉，留下一堆历史卖家消息
            eff_rect = effective_chat_rect(shop)
            h = int(eff_rect.height())
            w = int(eff_rect.width())
            buyer_text = extract_buyer_message_block(
                result.spans, roi_height=h, roi_width=w
            )
            card_hint = heuristic_product_card_like(result.spans, roi_height=h)
            self._log(
                f"步骤5完成：buyer_text={buyer_text!r:.80}  card_hint={card_hint}"
            )
            # v1.6.0：本 cycle 的 buyer_digest，给 SessionReplyBudget 用
            # （所有 enqueue_send_text/enqueue_soothe_wait 调用都传这个）
            cycle_buyer_digest = normalize_buyer_digest(buyer_text)

            if should_skip_duplicate_buyer(
                buyer_text=buyer_text,
                last_digest=self._last_buyer_digest,
                last_handled_at=self._last_buyer_handled_at,
            ):
                self._log(
                    f"跳过本轮：与上一条 OCR 内容相同（{buyer_text[:48]!r}），"
                    f"疑为视觉哨兵/列表高亮重复触发"
                )
                self._visual_quiet_until = time.monotonic() + 20.0
                return

            if not (buyer_text or "").strip() and session_switched:
                retry_wait = 0.6
                self._log(
                    f"步骤5重试：切换会话后 OCR 为空，再等 {retry_wait:.1f}s 后重截…"
                )
                time.sleep(retry_wait)
                try:
                    capture_ts = datetime.now()
                    img = capture_chat_rgb(shop)
                    snap_retry = maybe_save_debug_chat_snapshot(
                        img, f"{ev.trigger or 'capture'}_retry"
                    )
                    if snap_retry is not None:
                        self._log(f"步骤5重试：调试截图已保存 {snap_retry}")
                    result = ocr.recognize(img)
                    buyer_text = extract_buyer_message_block(
                        result.spans, roi_height=h, roi_width=w
                    )
                    card_hint = heuristic_product_card_like(
                        result.spans, roi_height=h
                    )
                    self._log(
                        f"步骤5重试完成：buyer_text={buyer_text!r:.80}  card_hint={card_hint}"
                    )
                except Exception as e:
                    self._log(f"步骤5重试失败：{e!r}")

            ta_cfg = load_time_alignment_settings()
            align = None
            if ta_cfg.enabled and result is not None:
                align = assess_chat_time_alignment_from_spans(
                    result.spans,
                    roi_height=h,
                    capture_ts=capture_ts,
                    max_skew_minutes=ta_cfg.max_skew_minutes,
                    warn_skew_minutes=ta_cfg.warn_skew_minutes,
                    stale_discard_minutes=ta_cfg.stale_discard_minutes,
                    capture_skew_discard_minutes=ta_cfg.capture_skew_discard_minutes,
                    on_missing=ta_cfg.on_missing_timestamp,
                )
                if align.chat_time is not None and align.skew_seconds is not None:
                    self._log(
                        f"步骤5c：时间校对 OCR={align.chat_time:%Y-%m-%d %H:%M:%S} "
                        f"偏差={align.skew_seconds / 60.0:.1f}min "
                        f"候选={align.candidate_count} ok={align.ok}"
                        f" stale={align.stale_discard} warn={align.warn_skew}"
                    )
                else:
                    self._log(f"步骤5c：时间校对 {align.message}")

                # v1.6.2 方式B：日期字符串双保险（与方式A绝对时差互为独立判据）。
                # 方式A ok 但日期≠今天时，强制按 stale 处理，复用下游"补救切换→丢弃"管线。
                if align.ok and result is not None:
                    try:
                        from apps.core.ocr.chat_time_align import (
                            latest_date_differs_from_today,
                        )
                        from dataclasses import replace as _dc_replace

                        date_stale, date_msg = latest_date_differs_from_today(
                            result.spans, roi_height=h
                        )
                        if date_stale:
                            self._log(f"步骤5c方式B：{date_msg}")
                            align = _dc_replace(
                                align, ok=False, stale_discard=True, message=date_msg
                            )
                    except Exception as _e_dateb:
                        self._log(f"步骤5c方式B 异常（忽略）：{_e_dateb!r}")

                if (
                    not align.ok
                    and ev.trigger == "audio_peak"
                ):
                    from apps.core.channels.qianniu.session_list_unread import (
                        retry_switch_top_unread_session,
                    )

                    self._log(
                        f"步骤5c补救：时间戳异常（stale={align.stale_discard}），"
                        "尝试点顶部红色未读再 OCR…"
                    )
                    if retry_switch_top_unread_session(shop, self._log):
                        wait_retry = max(
                            0.8, float(sd.post_switch_click_sleep_s) + 0.35
                        )
                        time.sleep(wait_retry)
                        try:
                            capture_ts = datetime.now()
                            img = capture_chat_rgb(shop)
                            result = ocr.recognize(img)
                            buyer_text = extract_buyer_message_block(
                                result.spans, roi_height=h, roi_width=w
                            )
                            card_hint = heuristic_product_card_like(
                                result.spans, roi_height=h
                            )
                            align = assess_chat_time_alignment_from_spans(
                                result.spans,
                                roi_height=h,
                                capture_ts=capture_ts,
                                max_skew_minutes=ta_cfg.max_skew_minutes,
                                warn_skew_minutes=ta_cfg.warn_skew_minutes,
                                stale_discard_minutes=ta_cfg.stale_discard_minutes,
                                capture_skew_discard_minutes=ta_cfg.capture_skew_discard_minutes,
                                on_missing=ta_cfg.on_missing_timestamp,
                            )
                            self._log(
                                f"步骤5c补救完成：buyer_text={buyer_text!r:.60} "
                                f"ok={align.ok} stale={align.stale_discard}"
                            )
                            session_switched = True
                        except Exception as e:
                            self._log(f"步骤5c补救失败：{e!r}")

                if align is not None and not align.ok:
                    self._log(f"⚠ {align.message}")
                    if align.stale_discard:
                        # v1.6.13 Fix A：时间戳判旧前，先看会话列表是否真有未读红标/黄条。
                        # 用户要求：只要存在未读红标就必须回复（未读优先于时间戳）。
                        # 偶发 OCR 坏帧/粘连会误读时间戳→误判 stale；若列表确有未读，
                        # 说明是真实新消息，绝不能丢：重截一帧后放行，下游 noise/budget 仍兜底。
                        _has_unread = False
                        try:
                            from apps.core.channels.qianniu.session_list_unread import (
                                has_unread_badge,
                            )
                            _has_unread = has_unread_badge(shop, self._log)
                        except Exception as _e_unread:
                            self._log(f"Fix A 未读检测异常（忽略）：{_e_unread!r}")

                        if _has_unread:
                            self._log(
                                "时间戳判旧，但会话列表存在未读红标/黄条 → 判定真实新消息，"
                                "重截一帧后放行（Fix A：未读优先于时间戳）"
                            )
                            try:
                                time.sleep(0.5)
                                capture_ts = datetime.now()
                                img = capture_chat_rgb(shop)
                                result = ocr.recognize(img)
                                buyer_text = extract_buyer_message_block(
                                    result.spans, roi_height=h, roi_width=w
                                )
                                card_hint = heuristic_product_card_like(
                                    result.spans, roi_height=h
                                )
                                cycle_buyer_digest = normalize_buyer_digest(buyer_text)
                                self._log(
                                    f"Fix A 重截后：buyer_text={buyer_text!r:.60}"
                                )
                            except Exception as _e_recap:
                                self._log(f"Fix A 重截失败（忽略，仍放行）：{_e_recap!r}")
                            # 不 return：往下继续走正常意图路由/回复流程
                        else:
                            self._log(
                                "时间戳偏差大且会话列表无未读：跳过本轮不发送任何消息，"
                                "等待视觉哨兵重新扫描正确的未读会话"
                            )
                            # 更新摘要：防止 chat_rescan 反复对同一条旧消息触发
                            self._last_buyer_digest = normalize_buyer_digest(buyer_text)
                            # 不设长静默期——让视觉哨兵尽快扫描到真正在闪烁的客户
                            self._visual_quiet_until = time.monotonic() + 3.0
                            return
                    else:
                        conn_align = connect(self._db_path)
                        init_db(conn_align)
                        try:
                            ensure_session_row(
                                conn_align,
                                session_id=self._session_id,
                                brand_id=brand_id,
                                shop_id=shop_id,
                                source_id=self._source_id,
                                shop_code=getattr(shop, "shop_code", ""),
                                shop_display_name=getattr(shop, "shop_display_name", ""),
                            )
                            ensure_policy_row(
                                conn_align, brand_id=brand_id, shop_id=shop_id
                            )
                            policy_align = get_policy(
                                conn_align, brand_id=brand_id, shop_id=shop_id
                            )
                            cust_align = get_session_customer_display_name(
                                conn_align, self._session_id
                            )
                            self._jim_takeover(
                                conn_align,
                                shop=shop,
                                buyer_text=(buyer_text or "").strip() or align.message,
                                reason=align.message,
                                policy=policy_align,
                                customer_display=cust_align,
                                full_takeover=False,
                            )
                            self._log(
                                "时间校对未通过（非历史丢弃）：已推送提醒，本轮不自动回复"
                            )
                        finally:
                            conn_align.close()
                        self._visual_quiet_until = time.monotonic() + 30.0
                        return

        # --- P0 #5：昵称锚定 ---
        # OCR 完成后立刻提取当前聊天窗口的买家昵称，用于发送前身份校验
        anchor_nick: str | None = None
        if ev.trigger != "db_message":
            try:
                from apps.core.ocr.buyer_nick_extract import extract_buyer_nickname

                anchor_nick = extract_buyer_nickname(shop)
                if anchor_nick:
                    self._log(f"步骤5d：昵称锚定 nick={anchor_nick!r}")
            except Exception as e:
                self._log(f"昵称提取异常（不影响后续）：{e!r}")

        # v1.6.17 修：以下两步必须在「商品卡片快速通道」之前算好，否则卡片分支
        # 引用 _already_talked_today 时会 UnboundLocalError（定义在更后面）。
        # ① 客服存档：有效买家留言入站（卡片场景也要存，否则卡片消息漏档）
        if ev.trigger != "db_message" and (buyer_text or "").strip():
            self._archive_message("in", buyer_text)

        # ② 同日跳欢迎语：该会话今日已对话过则不发"您好在的呢"，直接答问题。
        #    依据优先 DB（messages 表，刚已存入站→今日>1 条说明早先聊过），
        #    DB 异常时退回内存 _today_greeted 兜底。
        _already_talked_today = False
        try:
            _conn_chk = connect(self._db_path)
            try:
                _today = _today_iso()
                _cnt = _conn_chk.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=? "
                    "AND substr(captured_at,1,10)=?",
                    (self._session_id, _today),
                ).fetchone()
                _already_talked_today = bool(_cnt and int(_cnt[0]) > 1)
            finally:
                _conn_chk.close()
        except Exception:
            _already_talked_today = self._today_greeted.get(
                self._session_id, ""
            ) == _today_iso()

        # v1.6.26：持久化「同日已问候」去重。session_id 每次启动随机生成，重启托管后
        # 上面按 session_id 查 messages 表必然落空 → 对老客户重发"您好，在的呢～" → 触发
        # 千牛"服务态度提醒/重复消息"风控。改用稳定的买家昵称跨重启判定（昵称取不到则不去重）。
        _greeting_cust_key = _derive_greeting_customer_key(anchor_nick, buyer_text)
        if not _already_talked_today and _greeting_cust_key:
            try:
                from apps.core.crm.greeting_log import has_greeted_today

                if has_greeted_today(shop_id, _greeting_cust_key, _today_iso()):
                    _already_talked_today = True
                    self._log(
                        f"步骤5b：持久库判定今日已问候过该客户({_greeting_cust_key!r}) → 跳欢迎语"
                    )
            except Exception:
                pass

        # --- v1.3.94：商品卡片快速通道 ---
        # 买家分享商品链接时，OCR 通常只看到商品图 + "当前用户来自商品详情页"
        # 这种情形下 buyer_text 不是真实问句，但 card_hint=True
        # 走专用路径：发欢迎语 + 商品卡片追问，跳过话术库/Jim 兜底/批量安抚
        if (
            ev.trigger != "db_message"
            and card_hint
            and (
                not (buyer_text or "").strip()
                or is_echo_or_noise_buyer_text(buyer_text, trigger=ev.trigger)
            )
        ):
            fb = load_fallback_phrases()
            self._log(
                f"商品卡片场景：buyer_text={buyer_text!r:.40} card_hint=True，"
                f"走专用追问路径（欢迎语 + 追问规格）"
            )

            # v1.6.14 咨询宝贝读编码（默认关，需 base_settings 开启 + 坐标已标定）：
            # 点咨询宝贝→悬停→OCR 浮层读 PFG 编码→查产品库，命中则发"具体尺寸"。
            if bool(getattr(self._settings, "card_consult_lookup_enabled", False)):
                try:
                    from apps.core.channels.qianniu.consult_product_lookup import (
                        lookup_product_by_consult,
                    )
                    _hit = lookup_product_by_consult(
                        shop,
                        brand_id=brand_id,
                        shop_id=shop_id,
                        db_path=self._db_path,
                        log=self._log,
                    )
                except Exception as _e_consult:
                    _hit = None
                    self._log(f"咨询宝贝读编码流程异常（已忽略，退回固定话术）：{_e_consult!r}")
                if _hit is not None and (_hit.size_details or "").strip():
                    _reply = f"这款「{_hit.name}」的尺寸是：{_hit.size_details.strip()}"
                    if not _already_talked_today and should_send_welcome(
                        trigger=ev.trigger, welcome_last_at=self._welcome_last_at
                    ):
                        self._pipeline.enqueue_soothe_wait(
                            self._source_id, self._session_id,
                            text=fb.welcome_greeting, buyer_digest=cycle_buyer_digest,
                        )
                        self._welcome_last_at = time.monotonic()
                    self._pipeline.enqueue_send_text(
                        self._source_id, self._session_id, text=_reply,
                        chat_log_meta={
                            "customer_label": "",
                            "intent_label": "商品卡片:尺寸(咨询宝贝编码)",
                            "kb_node": f"product:{_hit.product_code}",
                        },
                        buyer_digest=cycle_buyer_digest,
                    )
                    self._log(f"商品卡片：咨询宝贝读编码命中，已答尺寸（{_hit.product_code}）")
                    self._last_buyer_digest = normalize_buyer_digest(f"__card__{buyer_text}")
                    self._last_buyer_handled_at = time.monotonic()
                    self._last_nonempty_buyer_monotonic = time.monotonic()
                    self._visual_quiet_until = time.monotonic() + 25.0
                    self._cycle_produced_reply = True
                    return

            # 第 1 句：欢迎语（仅 audio_peak 且未在冷却内才发；同日已聊则跳过）
            if not _already_talked_today and should_send_welcome(
                trigger=ev.trigger, welcome_last_at=self._welcome_last_at
            ):
                self._pipeline.enqueue_soothe_wait(
                    self._source_id,
                    self._session_id,
                    text=fb.welcome_greeting,
                    buyer_digest=cycle_buyer_digest,
                )
                self._welcome_last_at = time.monotonic()
            # 第 2 句：商品卡片追问
            self._pipeline.enqueue_send_text(
                self._source_id,
                self._session_id,
                text=fb.product_card_inquiry,
                chat_log_meta={
                    "customer_label": "",
                    "intent_label": "商品卡片:追问规格",
                    "kb_node": "fallback:product_card_inquiry",
                },
                buyer_digest=cycle_buyer_digest,
            )
            self._last_buyer_digest = normalize_buyer_digest(
                f"__card__{buyer_text}"
            )
            self._last_buyer_handled_at = time.monotonic()
            self._last_nonempty_buyer_monotonic = time.monotonic()
            self._visual_quiet_until = time.monotonic() + 25.0
            self._cycle_produced_reply = True
            return

        if ev.trigger != "db_message" and is_echo_or_noise_buyer_text(
            buyer_text,
            trigger=ev.trigger,
            seller_display_name=getattr(shop, "shop_display_name", ""),
        ):
            self._log(
                f"noise=true，跳过本轮：OCR 非有效买家留言（{buyer_text!r}），"
                f"多为时间戳/寒暄/商品碎片"
            )
            if session_switched and (card_hint or not (buyer_text or "").strip()):
                self._log(
                    "提示：已点击黄条但 OCR 仍是卡片/时间戳/空，"
                    "可能点错会话或列表未刷新；请核对 session_list_rect、"
                    "调大 session_list_settle_wait_s / yellow_bar_confirm_frames"
                )
            # 更新摘要：防止 chat_rescan 反复对同一条噪声文本触发
            self._last_buyer_digest = normalize_buyer_digest(buyer_text)
            self._visual_quiet_until = time.monotonic() + 15.0
            return

        # v1.6.17：客服存档 + 同日判定已提前到「商品卡片快速通道」之前统一计算，
        # 此处不再重复（避免买家消息存两次、避免 _already_talked_today 重复定义）。

        # v1.6.25 防自身回声：OCR 抽出的内容若与最近发出的回复高度相似，判为读到自己刚发的话，
        # 丢弃本轮并记一条日志（mobile/db 来源不经 OCR，不做此判定）。
        if ev.trigger not in ("mobile", "db_message") and self._looks_like_own_echo(buyer_text):
            self._log(
                f"已过滤自身回声：buyer_text 与最近发出的回复高度相似 → 丢弃本轮 "
                f"({(buyer_text or '')[:32]!r})"
            )
            self._last_buyer_digest = normalize_buyer_digest(buyer_text)
            self._last_buyer_handled_at = time.monotonic()
            self._visual_quiet_until = time.monotonic() + 15.0
            return

        welcome_just_sent = False
        if (
            should_send_welcome(trigger=ev.trigger, welcome_last_at=self._welcome_last_at)
            and not _already_talked_today
        ):
            _fb = load_fallback_phrases()
            self._pipeline.enqueue_soothe_wait(
                self._source_id,
                self._session_id,
                text=_fb.welcome_greeting,
                buyer_digest=cycle_buyer_digest,
            )
            self._welcome_last_at = time.monotonic()
            self._today_greeted[self._session_id] = _today_iso()
            # v1.6.26：持久化记录"今日已问候该客户"，跨重启生效，防重启后重发欢迎语
            try:
                from apps.core.crm.greeting_log import mark_greeted_today

                if _greeting_cust_key:
                    mark_greeted_today(shop_id, _greeting_cust_key, _today_iso())
            except Exception:
                pass
            welcome_just_sent = True
            self._log("步骤5b：OCR 确认有效留言后，首句问候已入队（仅 audio_peak）")
        elif _already_talked_today and ev.trigger == "audio_peak":
            self._log(
                "步骤5b：同一自然日已与该客户对话过 → 跳过欢迎语，直接回答问题（v1.6.14）"
            )
        elif ev.trigger == "visual_scan":
            self._log("步骤5b：视觉触发不发送首句问候（等待叮咚/audio_peak）")

        # v1.5.8 修复重复回复：welcome 刚发完，且 OCR 出来的 buyer_text 清洗掉
        # 店铺名 / 买家 ID / hi 填充词后，剩下只是 "在么/您好/在的呢" 这种纯寒暄，
        # 则不再调 LLM/quick_reply 生成第二句，避免「您好，在的呢～ + 您好，我在的～」
        # 这种连发两句问候 → 触发淘宝"重复无意义话术"风控。
        if welcome_just_sent and ev.trigger != "db_message":
            seller_name = ""
            try:
                seller_name = (shop.shop_display_name or "").strip()
            except Exception:
                seller_name = ""
            # v1.6.25：先剥掉买家昵称前缀（OCR 常把"kid_betsy 在吗"连昵称一起抓出来），
            # 否则昵称被当成实质内容 → 误判为"非纯寒暄" → welcome 之后又发第二句问候。
            _bt_open = (buyer_text or "").strip()
            if anchor_nick and _bt_open.startswith(anchor_nick):
                _bt_open = _bt_open[len(anchor_nick):].strip()
            if is_only_opening_after_strip(_bt_open, seller_display_name=seller_name):
                self._log(
                    "步骤5b：清洗后剩下只剩开场寒暄，welcome 一句已够，"
                    "不再调 LLM/quick_reply（防重复回复+防风控）"
                )
                self._last_buyer_digest = normalize_buyer_digest(buyer_text)
                self._last_buyer_handled_at = time.monotonic()
                self._last_nonempty_buyer_monotonic = time.monotonic()
                self._visual_quiet_until = time.monotonic() + 25.0
                self._cycle_produced_reply = True
                return

        from apps.core.ai.input_quality_gate import check_buyer_input

        ocr_gate = check_buyer_input(buyer_text)
        if ocr_gate.action == "discard_log":
            self._log(f"noise=true，跳过本轮（OCR 门控：{ocr_gate.rule_name}）")
            self._visual_quiet_until = time.monotonic() + 15.0
            return
        if ocr_gate.action == "quick_reply":
            self._log(f"noise=true，引导回复（{ocr_gate.rule_name}）")
            self._pipeline.enqueue_send_text(
                self._source_id,
                self._session_id,
                text=ocr_gate.reply,
                buyer_digest=cycle_buyer_digest,
                chat_log_meta={
                    "customer_label": "",
                    "intent_label": f"门控引导:{ocr_gate.rule_name}",
                    "kb_node": "input_quality_gate",
                },
            )
            self._cycle_produced_reply = True
            return
        if card_hint:
            self._log(
                "步骤5提示：OCR 判定为商品卡片/足迹类文案（含价格或商品名特征），"
                "可能不是买家聊天文字；若未先切换黄条会话，容易在旧会话里误回复"
            )
        if not (buyer_text or "").strip():
            if card_hint:
                buyer_text = "（暂无文字，仅有商品卡片）"
            else:
                self._log("OCR未识别到对方留言，跳过本轮（可调大「截图等待时间」或检查 ocr_chat_rect 配置）")
                return

        self._last_nonempty_buyer_monotonic = time.monotonic()

        intent = classify_buyer_text(buyer_text)

        conn = connect(self._db_path)
        init_db(conn)
        try:
            ensure_session_row(
                conn,
                session_id=self._session_id,
                brand_id=brand_id,
                shop_id=shop_id,
                source_id=self._source_id,
                shop_code=getattr(shop, "shop_code", ""),
                shop_display_name=getattr(shop, "shop_display_name", ""),
            )
            ensure_policy_row(conn, brand_id=brand_id, shop_id=shop_id)
            policy = get_policy(conn, brand_id=brand_id, shop_id=shop_id)
            customer_display = get_session_customer_display_name(conn, self._session_id)
            chat_log_ctx = get_panse_customer_chat_log()
            ctx_block = chat_log_ctx.recent_context_text(self._session_id)

            mb = check_money_hard_block_buyer(buyer_text)
            if not mb.allowed:
                hitl_ctx = ctx_block.strip()
                self._jim_takeover(
                    conn,
                    shop=shop,
                    buyer_text=buyer_text,
                    reason=mb.reason or "资金硬拦",
                    policy=policy,
                    customer_display=customer_display,
                    hitl_context=hitl_ctx,
                    hitl_query=buyer_text,
                )
                return

            if _strong_reminder_active(policy):
                preview = (buyer_text or "").strip().replace("\n", " ")[:400]
                insert_session_event(
                    conn,
                    SessionEvent(
                        event_id="",
                        brand_id=brand_id,
                        shop_id=shop_id,
                        session_id=self._session_id,
                        source_id=self._source_id,
                        event_type="strong_reminder_ping",
                        payload={"trigger": ev.trigger, "buyer_preview": preview},
                        evidence_confidence=1.0,
                    ),
                    force=True,
                )
                if (
                    self._settings.push_serverchan_sendkey.strip()
                    or self._settings.push_pushplus_token.strip()
                    or self._settings.push_wecom_webhook.strip()
                    or self._settings.push_host_alert_url.strip()
                ):
                    body = (
                        f"trigger={ev.trigger}\nsession={self._session_id}\n"
                        f"客户：{preview or '（无文字摘要）'}"
                    )
                    for r in push_all(
                        self._settings,
                        title="[强提醒] 新消息",
                        body=body,
                    ):
                        self._log(f"强提醒推送 {r.channel} ok={r.ok}")

            stolen_pending = self._take_pending_card_if_valid()
            pending_chunk = ""
            if stolen_pending:
                pending_chunk = (
                    format_card_context_for_prompt(
                        card_json=stolen_pending.card_json,
                        match=stolen_pending.match,
                    )
                    + "\n\n"
                )

            if intent.ad_noise:
                insert_session_event(
                    conn,
                    SessionEvent(
                        event_id="",
                        brand_id=brand_id,
                        shop_id=shop_id,
                        session_id=self._session_id,
                        source_id=self._source_id,
                        event_type="ad_noise_skipped",
                        payload={"text_preview": buyer_text[:200]},
                        evidence_confidence=float(result.avg_confidence),
                    ),
                    force=True,
                )
                try:
                    get_panse_customer_chat_log().append_row(
                        session_id=self._session_id,
                        customer_label=customer_display,
                        sender="客户",
                        raw_message=buyer_text,
                        intent_label="跳过:广告/系统噪声",
                        kb_node="",
                    )
                except Exception:
                    pass
                self._log("判定为广告/系统噪声风格，跳过回复")
                if stolen_pending:
                    self._pending_card = stolen_pending
                return

            from apps.core.ai.input_quality_gate import load_jim_min_buyer_len

            # v1.6.25：询价/拍下改「知识库优先」——不再在此短路转人工。
            # jim_price 仅作为标记往下传：落到下方 RAG 主路径检索产品库/话术库，
            #   - KB 命中 → 正常回答具体价格/规格；
            #   - KB 未命中(plan.takeover / 低置信) → 改发活动期兜底话术 + 跳强提醒 + 挂人工
            #     （见 _handle_price_kb_miss）。
            # 「实拍/发图」仍需人工核实，照旧短路。
            jim_price = bool(policy.price_sensitive_handoff) and (
                intent.price_quote or intent.order_placed
            )
            if jim_price and len((buyer_text or "").strip()) < load_jim_min_buyer_len():
                jim_price = False
            jim_photo = bool(policy.real_photo_jim_intercept) and intent.real_photo
            if jim_photo:
                hitl_ctx = (ctx_block + "\n\n" + pending_chunk).strip() if pending_chunk else ctx_block
                photo_full = int(policy.jim_photo_full_takeover) == 1
                full_takeover = resolve_price_photo_full_takeover(
                    jim_price=False,
                    jim_photo=True,
                    price_full=False,
                    photo_full=photo_full,
                )
                self._jim_takeover(
                    conn,
                    shop=shop,
                    buyer_text=buyer_text,
                    reason="实拍/发图命中",
                    policy=policy,
                    customer_display=customer_display,
                    hitl_context=hitl_ctx,
                    hitl_query=buyer_text,
                    full_takeover=full_takeover,
                )
                return

            if intent.replenish:
                rep_line = retrieve_replenish_answer(conn, brand_id=brand_id, shop_id=shop_id)
                rep_line = (rep_line or "").strip() or load_fallback_phrases().replenish_reply
                try:
                    chat_log_ctx.append_row(
                        session_id=self._session_id,
                        customer_label=customer_display,
                        sender="客户",
                        raw_message=buyer_text,
                        intent_label="补单",
                        kb_node="replenish",
                    )
                except Exception as e:
                    self._log(f"对话 CSV 记录失败：{e!r}")
                self._pipeline.enqueue_send_text(
                    self._source_id,
                    self._session_id,
                    text=rep_line,
                    buyer_digest=cycle_buyer_digest,
                    chat_log_meta={
                        "customer_label": customer_display,
                        "intent_label": "补单话术",
                        "kb_node": "replenish",
                    },
                )
                self._log("补单话术已入队")
                self._cycle_produced_reply = True
                return

            anger, follow_pending = session_get_state(conn, self._session_id)
            if intent.anger:
                anger += 1
            else:
                anger = 0
            session_set_anger(conn, self._session_id, anger)
            thr = max(1, min(4, int(policy.anger_hit_threshold)))
            if anger >= thr:
                hitl_ctx = (ctx_block + "\n\n" + pending_chunk).strip() if pending_chunk else ctx_block
                self._jim_takeover(
                    conn,
                    shop=shop,
                    buyer_text=buyer_text,
                    reason=f"愤怒累计≥{thr}",
                    policy=policy,
                    customer_display=customer_display,
                    hitl_context=hitl_ctx,
                    hitl_query=buyer_text,
                )
                session_set_anger(conn, self._session_id, 0)
                return

            card_context_append = pending_chunk
            product_query_boost = ""

            if card_hint:
                cj = extract_card_json_from_rgb(self._settings, img)
                if cj:
                    m = match_product_by_card(conn, brand_id=brand_id, shop_id=shop_id, card_json=cj)
                    if m.ambiguous:
                        insert_session_event(
                            conn,
                            SessionEvent(
                                event_id="",
                                brand_id=brand_id,
                                shop_id=shop_id,
                                session_id=self._session_id,
                                source_id=self._source_id,
                                event_type="product_card_ambiguous",
                                payload={
                                    "candidates": list(m.candidates)[:12],
                                    "card_title": str(cj.get("truncated_title", ""))[:200],
                                },
                                evidence_confidence=float(m.best_score),
                            ),
                            force=True,
                        )
                        amb_hitl = (ctx_block + "\n\n" + card_context_append).strip() if card_context_append else ctx_block
                        self._jim_takeover(
                            conn,
                            shop=shop,
                            buyer_text=buyer_text,
                            reason="商品卡片匹配多款相似，需人工确认",
                            policy=policy,
                            customer_display=customer_display,
                            hitl_context=amb_hitl,
                            hitl_query=f"{buyer_text}\n卡片:{cj}",
                            hitl_pos=list(m.candidates),
                            hitl_neg=[],
                        )
                        return
                    if m.product_id:
                        if buyer_message_is_substantive(buyer_text):
                            card_context_append += format_card_context_for_prompt(
                                card_json=cj,
                                match=m,
                            ) + "\n\n"
                            product_query_boost = m.product_name
                        else:
                            self._set_pending_card(PendingCardContext(card_json=cj, match=m))
                            insert_session_event(
                                conn,
                                SessionEvent(
                                    event_id="",
                                    brand_id=brand_id,
                                    shop_id=shop_id,
                                    session_id=self._session_id,
                                    source_id=self._source_id,
                                    event_type="product_card_context_cached",
                                    payload={
                                        "product_code": m.product_code,
                                        "product_name": m.product_name[:200],
                                    },
                                    evidence_confidence=float(m.best_score),
                                ),
                                force=True,
                            )
                            self._log("商品卡片已识别并缓存，等待客户补充提问后再答复")
                            return

            pq = (buyer_text + " " + product_query_boost).strip()
            products = retrieve_product_snippets(conn, brand_id=brand_id, shop_id=shop_id, query=pq)
            if any(customization_requires_jim(row[3]) for row in products):
                hitl_ctx = (ctx_block + "\n\n" + pending_chunk).strip() if pending_chunk else ctx_block
                self._jim_takeover(
                    conn,
                    shop=shop,
                    buyer_text=buyer_text,
                    reason="可定制范围需人工/主管确认（产品库标记）",
                    policy=policy,
                    customer_display=customer_display,
                    hitl_context=hitl_ctx,
                    hitl_query=buyer_text,
                )
                return

            camps = retrieve_active_campaigns(conn, brand_id=brand_id, shop_id=shop_id)
            gallery = retrieve_gallery_hints(conn, brand_id=brand_id, shop_id=shop_id)

            if intent.price_quote or intent.likely_price_discussion:
                self._price_round[self._session_id] = self._price_round.get(self._session_id, 0) + 1
            round_idx = max(1, self._price_round.get(self._session_id, 1))
            disc_hint = discount_round_hint(round_idx)

            prod_blk = format_product_block(products)
            camp_blk = format_campaign_block(camps)
            gal_blk = format_gallery_block(gallery)
            fluff = load_phrase_blacklist(conn, brand_id=brand_id, shop_id=shop_id)
            fluff_txt = format_blacklist_for_prompt(fluff)

            used_follow_cycle = bool(follow_pending)
            extra_instr = ""
            if follow_pending:
                extra_instr = (
                    "上一轮你已口头答应客户「可以」，请用单独一条短消息追问对方规格/尺寸/颜色等关键细节（一句话）。"
                )
            if (card_context_append or "").strip():
                extra_instr = (
                    (extra_instr + "\n\n" + card_context_append.strip()).strip()
                    if extra_instr.strip()
                    else card_context_append.strip()
                )

            closing_note = (
                "【收尾礼仪】若客户表达谢谢、再见或不需要了，只用极简口吻收尾（如「嗯嗯」「好的」或一个小表情），不要展开长篇。"
            )

            chat_log = chat_log_ctx

            self._settings = load_base_settings()
            plan = build_routed_reply_plan(
                settings=self._settings,
                conn=conn,
                brand_id=brand_id,
                shop_id=shop_id,
                buyer_text=buyer_text,
                context_block=ctx_block,
                few_shot=self._read_few_shot(),
                phrase_blacklist=fluff_txt,
                product_block=prod_blk,
                campaign_block=camp_blk,
                gallery_block=gal_blk,
                discount_round_hint=disc_hint,
                closing_etiquette=closing_note,
                extra_instructions=extra_instr,
                discount_round_index=round_idx,
            )

            cust_label = customer_display

            if plan.takeover:
                # v1.6.25：询价/拍下且知识库未命中 → 活动期兜底话术 + 强提醒 + 挂人工
                if jim_price:
                    try:
                        chat_log.append_row(
                            session_id=self._session_id,
                            customer_label=cust_label,
                            sender="客户",
                            raw_message=buyer_text,
                            intent_label=f"{plan.intent_label}|询价KB未命中兜底",
                            kb_node=plan.kb_node,
                        )
                    except Exception as e:
                        self._log(f"对话 CSV 记录失败：{e!r}")
                    self._handle_price_kb_miss(
                        conn,
                        shop=shop,
                        buyer_text=buyer_text,
                        cust_label=cust_label,
                        cycle_buyer_digest=cycle_buyer_digest,
                        trigger=ev.trigger,
                    )
                    return
                # v1.6.27：泛问属性(材质/尺寸/颜色等)但KB未命中 → 反问澄清是哪款，不转人工
                from apps.core.orchestrator.reply_guards import (
                    is_generic_attribute_question,
                )

                if is_generic_attribute_question(buyer_text):
                    self._send_spec_clarify(
                        buyer_text=buyer_text,
                        cust_label=cust_label,
                        cycle_buyer_digest=cycle_buyer_digest,
                    )
                    return
                try:
                    chat_log.append_row(
                        session_id=self._session_id,
                        customer_label=cust_label,
                        sender="客户",
                        raw_message=buyer_text,
                        intent_label=f"{plan.intent_label}|Jim:{plan.takeover_reason[:120]}",
                        kb_node=plan.kb_node,
                    )
                except Exception as e:
                    self._log(f"对话 CSV 记录失败：{e!r}")
                if plan.kb_diagnosis_lines:
                    self._log(f"兜底原因：{plan.takeover_reason}")
                    for line in plan.kb_diagnosis_lines:
                        self._log(line)
                self._jim_takeover(
                    conn,
                    shop=shop,
                    buyer_text=buyer_text,
                    reason=plan.takeover_reason,
                    policy=policy,
                    customer_display=cust_label,
                    hitl_context=plan.hitl_context or ctx_block,
                    hitl_query=plan.hitl_query or buyer_text,
                    hitl_pos=list(plan.hitl_pos),
                    hitl_neg=list(plan.hitl_neg),
                )
                self._last_buyer_digest = normalize_buyer_digest(buyer_text)
                self._last_buyer_handled_at = time.monotonic()
                self._visual_quiet_until = time.monotonic() + 25.0
                return

            unc_thr = float(policy.unknown_topic_threshold)
            if plan.confidence_for_policy < unc_thr:
                # v1.6.25：询价/拍下且置信度不足 → 活动期兜底话术 + 强提醒 + 挂人工
                if jim_price:
                    try:
                        chat_log.append_row(
                            session_id=self._session_id,
                            customer_label=cust_label,
                            sender="客户",
                            raw_message=buyer_text,
                            intent_label=f"{plan.intent_label}|询价低置信兜底",
                            kb_node=plan.kb_node,
                        )
                    except Exception as e:
                        self._log(f"对话 CSV 记录失败：{e!r}")
                    self._handle_price_kb_miss(
                        conn,
                        shop=shop,
                        buyer_text=buyer_text,
                        cust_label=cust_label,
                        cycle_buyer_digest=cycle_buyer_digest,
                        trigger=ev.trigger,
                    )
                    return
                # v1.6.27：泛问属性(材质/尺寸/颜色等)且置信度不足 → 反问澄清是哪款，不转人工
                from apps.core.orchestrator.reply_guards import (
                    is_generic_attribute_question,
                )

                if is_generic_attribute_question(buyer_text):
                    self._send_spec_clarify(
                        buyer_text=buyer_text,
                        cust_label=cust_label,
                        cycle_buyer_digest=cycle_buyer_digest,
                    )
                    return
                try:
                    chat_log.append_row(
                        session_id=self._session_id,
                        customer_label=cust_label,
                        sender="客户",
                        raw_message=buyer_text,
                        intent_label=f"{plan.intent_label}|Jim:低置信度",
                        kb_node=plan.kb_node,
                    )
                except Exception as e:
                    self._log(f"对话 CSV 记录失败：{e!r}")
                self._jim_takeover(
                    conn,
                    shop=shop,
                    buyer_text=buyer_text,
                    reason=f"模型置信度 {plan.confidence_for_policy:.2f} < 阈值 {unc_thr:.2f}",
                    policy=policy,
                    customer_display=cust_label,
                    hitl_context=ctx_block,
                    hitl_query=plan.hitl_query or buyer_text,
                    hitl_pos=list(plan.hitl_pos),
                    hitl_neg=list(plan.hitl_neg),
                )
                return

            chk = check_llm_segments(plan.segments)
            if not chk.allowed:
                self._log(f"风控拦截 LLM：{chk.reason}")
                companion_health.record_health_event(
                    "risk_intercept_llm",
                    {"reason": chk.reason[:800]},
                    brand_id=brand_id,
                    shop_id=shop_id,
                )
                try:
                    chat_log.append_row(
                        session_id=self._session_id,
                        customer_label=cust_label,
                        sender="客户",
                        raw_message=buyer_text,
                        intent_label=f"{plan.intent_label}|Jim:风控拦截LLM",
                        kb_node=plan.kb_node,
                    )
                except Exception as e:
                    self._log(f"对话 CSV 记录失败：{e!r}")
                return

            try:
                chat_log.append_row(
                    session_id=self._session_id,
                    customer_label=cust_label,
                    sender="客户",
                    raw_message=buyer_text,
                    intent_label=plan.intent_label,
                    kb_node=plan.kb_node,
                )
            except Exception as e:
                self._log(f"对话 CSV 记录失败：{e!r}")

            blist = fluff
            log_meta = {
                "customer_label": cust_label,
                "intent_label": plan.intent_label,
                "kb_node": plan.kb_node,
            }

            use_gate = (
                plan.sensitivity_tier == "sensitive"
                and int(policy.outbound_preview_enabled) == 1
                and self._sensitive_review_fn is not None
            )
            delay_s = max(5, min(20, int(policy.outbound_preview_delay_seconds or 8)))

            def _commit_outbound() -> None:
                self._enqueue_routed_plan_outbound(
                    plan=plan,
                    fluff=blist,
                    log_meta=log_meta,
                    cust_label=cust_label,
                    brand_id=brand_id,
                    shop_id=shop_id,
                    buyer_digest=cycle_buyer_digest,
                )

            def _abort_hold() -> None:
                c2 = connect(self._db_path)
                init_db(c2)
                try:
                    set_manual_hold(c2, self._session_id, manual_hold=True)
                finally:
                    c2.close()

            # --- P0 #5：发送前昵称锚定校验 ---
            if anchor_nick:
                try:
                    from apps.core.ocr.buyer_nick_extract import (
                        extract_buyer_nickname,
                        nickname_matches,
                    )

                    current_nick = extract_buyer_nickname(shop)
                    if current_nick and not nickname_matches(anchor_nick, current_nick):
                        self._log(
                            f"⚠ 昵称锚定不匹配：锚定={anchor_nick!r} 现在={current_nick!r}，"
                            f"熔断发送（可能已切到其他买家会话）"
                        )
                        companion_health.record_health_event(
                            "nickname_anchor_mismatch",
                            {
                                "anchor": anchor_nick[:100],
                                "current": current_nick[:100],
                                "trigger": ev.trigger,
                            },
                            brand_id=brand_id,
                            shop_id=shop_id,
                        )
                        self._last_buyer_digest = normalize_buyer_digest(buyer_text)
                        self._last_buyer_handled_at = time.monotonic()
                        self._visual_quiet_until = time.monotonic() + 5.0
                        return
                except Exception as e:
                    self._log(f"发送前昵称校验异常（不阻断发送）：{e!r}")

            if use_gate:
                ok = bool(
                    self._sensitive_review_fn(
                        {
                            "buyer_preview": buyer_text,
                            "segments": list(plan.segments),
                            "image_items": list(plan.image_send_items),
                            "delay_seconds": delay_s,
                            "on_commit": _commit_outbound,
                            "on_abort_hold": _abort_hold,
                        }
                    )
                )
                if not ok:
                    self._log("敏感预览：已中断，未自动发送")
                    return
            else:
                self._enqueue_routed_plan_outbound(
                    plan=plan,
                    fluff=blist,
                    log_meta=log_meta,
                    cust_label=cust_label,
                    brand_id=brand_id,
                    shop_id=shop_id,
                    buyer_digest=cycle_buyer_digest,
                )

            if any("可以" in s for s in plan.segments):
                session_set_followup(conn, self._session_id, 1)
            elif used_follow_cycle:
                session_set_followup(conn, self._session_id, 0)

            self._log(
                f"已入队 {len(plan.segments)} 条（trigger={ev.trigger} conf={plan.confidence_for_policy:.2f}）"
            )
            self._last_buyer_digest = normalize_buyer_digest(buyer_text)
            self._last_buyer_handled_at = time.monotonic()
            self._visual_quiet_until = time.monotonic() + 25.0
            self._cycle_produced_reply = True
        except Exception as e:
            self._log(f"Brain 异常：{e!r}")
            try:
                companion_health.record_health_event(
                    "pipeline_exception",
                    {"error": repr(e)[:900]},
                    brand_id=brand_id,
                    shop_id=shop_id,
                )
            except Exception:
                pass
        finally:
            conn.close()
