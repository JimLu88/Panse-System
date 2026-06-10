from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from apps.core.automation.actions.driver import PhysicalDriver
from apps.core.automation.actions.send_image import execute_send_image
from apps.core.automation.actions.send_text import execute_send_text
from apps.core.channels.qianniu.driver import QianniuDriver
from apps.core.configs.loader import load_shop_config
from apps.core.context.memory import MemoryStore
from apps.core.crm.events import SessionEvent, ensure_session_row, insert_session_event, set_manual_hold
from apps.core.logging.image_library import bump_send_count
from apps.core.logging.panse_chat_log import get_panse_customer_chat_log
from apps.core.orchestrator.models import ActionItem, ActionKind
from apps.core.orchestrator.reacquire import run_reacquire_physical
from apps.core.risk_guard.guard import check_outbound_text

LogAsync = Callable[[str], None]
DbConnFactory = Callable[[], sqlite3.Connection]


def build_executor_handler(
    *,
    driver: object,
    memory: MemoryStore,
    log_async: LogAsync,
    db_conn_factory: DbConnFactory,
) -> Callable[[ActionItem], None]:
    """构造 SequentialExecutor 使用的唯一物理线程 handler（与 Legacy MVP 行为一致）。"""

    def handle(item: ActionItem) -> None:
        if item.kind == ActionKind.SEND_TEXT:
            text = str(item.payload.get("text") or "")
            chk = check_outbound_text(text)
            if not chk.allowed:
                log_async(f"BLOCK SEND_TEXT action_id={item.action_id} reason={chk.reason}")
                return
            log_async(f"EXEC SEND_TEXT action_id={item.action_id} text={text!r:.80}")
            try:
                plan = execute_send_text(driver, text)  # type: ignore[arg-type]
                anchor = plan.segments[-1] if plan.segments else text[:200]
                memory.set_last_ai_snippet(item.session_id, anchor)
                log_async(f"SEND_TEXT 发送成功 segments={len(plan.segments)}")
            except Exception as e:
                log_async(f"SEND_TEXT 发送失败：{e!r}（请检查千牛窗口焦点 / 剪贴板）")
            meta = item.payload.get("chat_log_meta")
            if isinstance(meta, dict):
                try:
                    get_panse_customer_chat_log().append_row(
                        session_id=item.session_id,
                        customer_label=str(meta.get("customer_label") or item.session_id),
                        sender="AI",
                        raw_message=text,
                        intent_label=str(meta.get("intent_label") or ""),
                        kb_node=str(meta.get("kb_node") or ""),
                    )
                except Exception as e:
                    log_async(f"对话 CSV 记录(AI)失败: {e!r}")
            return

        if item.kind == ActionKind.SOOTHE_WAIT:
            text = str(item.payload.get("text") or "")
            chk = check_outbound_text(text)
            if not chk.allowed:
                log_async(f"BLOCK SOOTHE_WAIT action_id={item.action_id} reason={chk.reason}")
                return
            log_async(f"EXEC SOOTHE_WAIT action_id={item.action_id} text={text!r}")
            try:
                plan = execute_send_text(driver, text, force_no_split=True)  # type: ignore[arg-type]
                anchor = plan.segments[-1] if plan.segments else text[:200]
                memory.set_last_ai_snippet(item.session_id, anchor)
                log_async(f"SOOTHE_WAIT 发送成功 segments={len(plan.segments)}")
            except Exception as e:
                log_async(f"SOOTHE_WAIT 发送失败：{e!r}（请检查千牛窗口焦点 / 剪贴板）")
            meta = item.payload.get("chat_log_meta")
            if isinstance(meta, dict):
                try:
                    get_panse_customer_chat_log().append_row(
                        session_id=item.session_id,
                        customer_label=str(meta.get("customer_label") or item.session_id),
                        sender="AI",
                        raw_message=text,
                        intent_label=str(meta.get("intent_label") or "Jim兜底"),
                        kb_node=str(meta.get("kb_node") or ""),
                    )
                except Exception as e:
                    log_async(f"对话 CSV 记录(AI soothe)失败: {e!r}")
            return

        if item.kind == ActionKind.SEND_IMAGE:
            path_str = str(item.payload.get("image_path") or "").strip()
            if not path_str:
                log_async(f"SKIP SEND_IMAGE action_id={item.action_id} empty path")
                return
            p = Path(path_str)
            if not p.is_file():
                log_async(f"SKIP SEND_IMAGE action_id={item.action_id} not a file: {path_str!r}")
                return
            if not isinstance(driver, PhysicalDriver):
                log_async(f"SKIP SEND_IMAGE action_id={item.action_id} driver not PhysicalDriver")
                return
            log_async(f"EXEC SEND_IMAGE action_id={item.action_id} path={path_str!r}")
            try:
                execute_send_image(driver, p)
            except Exception as e:
                log_async(f"SEND_IMAGE failed: {e!r}")
                return
            memory.set_last_ai_snippet(item.session_id, f"[图片]{p.name}")
            meta = item.payload.get("chat_log_meta")
            if isinstance(meta, dict):
                iid = str(meta.get("image_library_id") or "").strip()
                if iid:
                    try:
                        conn = db_conn_factory()
                        try:
                            bump_send_count(conn, image_id=iid)
                        finally:
                            conn.close()
                    except Exception as e:
                        log_async(f"图库 send_count 更新失败: {e!r}")
                try:
                    get_panse_customer_chat_log().append_row(
                        session_id=item.session_id,
                        customer_label=str(meta.get("customer_label") or item.session_id),
                        sender="AI",
                        raw_message=f"[图片]{p.name}",
                        intent_label=str(meta.get("intent_label") or ""),
                        kb_node=str(meta.get("kb_node") or ""),
                    )
                except Exception as e:
                    log_async(f"对话 CSV 记录(AI 图片)失败: {e!r}")
            return

        if item.kind == ActionKind.REACQUIRE_CONTEXT:
            if not isinstance(driver, QianniuDriver):
                log_async("EXEC REACQUIRE_CONTEXT skipped（需要千牛店铺配置启动 QianniuDriver）")
                return
            path_str = str(item.payload.get("shop_cfg_path") or "").strip()
            if not path_str:
                log_async("EXEC REACQUIRE_CONTEXT shop_cfg_path 为空")
                return
            try:
                shop_loaded = load_shop_config(Path(path_str))
                snippet = memory.get_last_ai_snippet(item.session_id)
                scroll_times = int(item.payload.get("scroll_times") or 8)
                log_async(
                    f"EXEC REACQUIRE_CONTEXT action_id={item.action_id} scroll={scroll_times} anchor_len={len(snippet)}"
                )
                log_async("（首次 OCR 若加载 Paddle 可能需数十秒，queue/busy 会保持直至完成）")
                res = run_reacquire_physical(
                    shop_loaded,
                    driver,
                    scroll_times=scroll_times,
                    last_ai_snippet=snippet or None,
                )
                memory.set_patch(item.session_id, res.patch_text)
                try:
                    conn = db_conn_factory()
                    ensure_session_row(
                        conn,
                        session_id=item.session_id,
                        brand_id=getattr(shop_loaded, "brand_id"),
                        shop_id=(
                            getattr(shop_loaded, "shop_id")
                            or (getattr(shop_loaded, "brand_id") + ":" + getattr(shop_loaded, "shop_code"))
                        ),
                        source_id=item.source_id,
                        shop_code=getattr(shop_loaded, "shop_code"),
                        shop_display_name=getattr(shop_loaded, "shop_display_name"),
                    )
                    set_manual_hold(conn, item.session_id, manual_hold=False)
                    insert_session_event(
                        conn,
                        SessionEvent(
                            event_id="",
                            brand_id=getattr(shop_loaded, "brand_id"),
                            shop_id=(
                                getattr(shop_loaded, "shop_id")
                                or (getattr(shop_loaded, "brand_id") + ":" + getattr(shop_loaded, "shop_code"))
                            ),
                            session_id=item.session_id,
                            source_id=item.source_id,
                            event_type="reacquire_completed",
                            payload={
                                "engine": res.engine,
                                "avg_conf": res.avg_conf,
                                "anchor_matched": res.anchor_matched,
                                "patch_chars": len(res.patch_text or ""),
                            },
                            evidence_confidence=float(res.avg_conf),
                        ),
                    )
                except Exception as e:
                    log_async(f"REACQUIRE DB 更新失败: {e!r}")
                log_async(
                    f"REACQUIRE ok engine={res.engine} anchor={res.anchor_matched} patch_chars={len(res.patch_text)}"
                )
            except Exception as e:
                log_async(f"REACQUIRE 失败: {e!r}")
            return

        text = str(item.payload.get("text") or "")
        log_async(f"EXEC {item.kind} action_id={item.action_id} text={text!r}")
        time.sleep(0.12)

    return handle
