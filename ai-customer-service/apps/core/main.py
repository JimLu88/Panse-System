from __future__ import annotations

import time
import uuid

from apps.core.automation.actions.driver import DryRunDriver
from apps.core.automation.actions.send_image import execute_send_image
from apps.core.automation.actions.send_text import execute_send_text
from apps.core.orchestrator.action_queue import ActionQueue
from apps.core.orchestrator.event_pipeline import EventPipeline
from apps.core.orchestrator.models import ActionItem, ActionKind
from apps.core.orchestrator.sequential_executor import SequentialExecutor


def _handle_noop(item: ActionItem) -> None:
    # Placeholder for debugging wiring.
    print(f"[executor] NOOP action_id={item.action_id} source_id={item.source_id} session_id={item.session_id}")


_driver = DryRunDriver()


def _handle_send_text(item: ActionItem) -> None:
    text = str(item.payload.get("text") or "")
    plan = execute_send_text(_driver, text)
    print(f"[executor] SEND_TEXT action_id={item.action_id} segments={len(plan.segments)}")


def _handle_soothe_wait(item: ActionItem) -> None:
    text = str(item.payload.get("text") or "")
    plan = execute_send_text(_driver, text)
    print(f"[executor] SOOTHE_WAIT action_id={item.action_id} segments={len(plan.segments)}")


def _handle_send_image(item: ActionItem) -> None:
    path = str(item.payload.get("image_path") or "")
    execute_send_image(_driver, path)
    print(f"[executor] SEND_IMAGE action_id={item.action_id} path={path!r}")


def _handle_reacquire(item: ActionItem) -> None:
    # 无 UI / 无千牛：头less 占位，避免 KeyError；真实环境由 PyQt 进程注册完整 handler。
    print(f"[executor] REACQUIRE_CONTEXT noop action_id={item.action_id} payload_keys={list(item.payload.keys())}")


def main() -> None:
    q = ActionQueue()
    pipeline = EventPipeline(q)

    executor = SequentialExecutor(
        action_queue=q,
        handlers={
            ActionKind.NOOP: _handle_noop,
            ActionKind.SEND_TEXT: _handle_send_text,
            ActionKind.SOOTHE_WAIT: _handle_soothe_wait,
            ActionKind.SEND_IMAGE: _handle_send_image,
            ActionKind.REACQUIRE_CONTEXT: _handle_reacquire,
        },
    )
    executor.start()

    # Minimal demo flow: enqueue some actions.
    source_id = "qianniu/account1/demo"
    session_id = "demo-session-" + str(uuid.uuid4())[:8]
    pipeline.enqueue_send_text(source_id, session_id, text="您好，在的呢～")
    pipeline.enqueue_soothe_wait(source_id, session_id)
    pipeline.enqueue_noop(source_id, session_id)

    time.sleep(1.5)
    executor.stop()


if __name__ == "__main__":
    main()

