from __future__ import annotations

import sqlite3
import threading
import time

from apps.core.context.memory import MemoryStore
from apps.core.crm.db import connect, init_db
from apps.core.crm.events import SessionEvent, ensure_session_row, insert_session_event
from apps.core.orchestrator.action_queue import ActionQueue
from apps.core.orchestrator.event_pipeline import EventPipeline
from apps.core.orchestrator.models import ActionItem, ActionKind
from apps.core.orchestrator.sequential_executor import SequentialExecutor


def test_priority_queue_order() -> None:
    """Lower priority value runs before higher (SOOTHE 10 vs SEND 100)."""
    results: list[ActionKind] = []
    q = ActionQueue()

    def handle(item: ActionItem) -> None:
        results.append(item.kind)

    ex = SequentialExecutor(
        q,
        {
            ActionKind.NOOP: handle,
            ActionKind.SEND_TEXT: handle,
            ActionKind.SOOTHE_WAIT: handle,
            ActionKind.REACQUIRE_CONTEXT: handle,
        },
    )
    ex.start()
    try:
        pl = EventPipeline(q)
        pl.enqueue_send_text("s", "sess", text="later", priority=100)
        pl.enqueue_soothe_wait("s", "sess")
        time.sleep(0.5)
        assert len(results) >= 2
        assert results[0] == ActionKind.SOOTHE_WAIT
        assert results[1] == ActionKind.SEND_TEXT
    finally:
        ex.stop()


def test_flood_noop_throughput() -> None:
    n = 3000
    count = {"v": 0}
    q = ActionQueue()

    def handle(item: ActionItem) -> None:
        if item.action_id != "__wake__":
            count["v"] += 1

    ex = SequentialExecutor(q, {ActionKind.NOOP: handle})
    ex.start()
    try:
        pl = EventPipeline(q)
        for _ in range(n):
            pl.enqueue_noop("src", "sess")
        deadline = time.time() + 120
        while count["v"] < n and time.time() < deadline:
            time.sleep(0.02)
        assert count["v"] >= n
        assert ex.stats().last_error is None
    finally:
        ex.stop()


def test_concurrent_enqueue_threads() -> None:
    q = ActionQueue()
    count = {"v": 0}

    def handle(item: ActionItem) -> None:
        if item.action_id != "__wake__":
            count["v"] += 1

    ex = SequentialExecutor(q, {ActionKind.NOOP: handle})
    ex.start()
    pl = EventPipeline(q)
    total = 2000
    per_thread = 400

    def worker() -> None:
        for _ in range(per_thread):
            pl.enqueue_noop("x", "y")

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        deadline = time.time() + 180
        while count["v"] < total and time.time() < deadline:
            time.sleep(0.02)
        assert count["v"] == total
    finally:
        ex.stop()


def test_memory_store_concurrent() -> None:
    m = MemoryStore()
    errs: list[BaseException] = []

    def work(i: int) -> None:
        try:
            sid = f"s{i % 10}"
            m.set_patch(sid, "x" * 100)
            m.set_last_ai_snippet(sid, "锚点测试")
            _ = m.get_patch(sid)
            _ = m.get_last_ai_snippet(sid)
        except BaseException as e:
            errs.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(500)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs


def test_sqlite_wal_session_writes(tmp_path) -> None:
    db = tmp_path / "stress.db"
    conn = connect(db)
    init_db(conn)
    # 单线程先建好 brand/shop/channel，避免并发 INSERT brands 触发 UNIQUE 竞态
    ensure_session_row(
        conn,
        session_id="__stress_bootstrap__",
        brand_id="b",
        shop_id="b:shop",
        source_id="src",
        shop_code="shop",
        shop_display_name="demo",
    )
    conn.close()

    errs: list[BaseException] = []

    def writer(i: int) -> None:
        last_err: BaseException | None = None
        for attempt in range(25):
            try:
                c = connect(db)
                init_db(c)
                ensure_session_row(
                    c,
                    session_id=f"stress-{i}",
                    brand_id="b",
                    shop_id="b:shop",
                    source_id="src",
                    shop_code="shop",
                    shop_display_name="demo",
                )
                insert_session_event(
                    c,
                    SessionEvent(
                        event_id="",
                        brand_id="b",
                        shop_id="b:shop",
                        session_id=f"stress-{i}",
                        source_id="src",
                        event_type="stress_write",
                        payload={"i": i},
                        evidence_confidence=1.0,
                    ),
                )
                c.close()
                return
            except sqlite3.OperationalError as e:
                last_err = e
                if "locked" in str(e).lower():
                    time.sleep(0.02 * (attempt + 1))
                    continue
                errs.append(e)
                return
            except BaseException as e:
                errs.append(e)
                return
        if last_err is not None:
            errs.append(last_err)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
