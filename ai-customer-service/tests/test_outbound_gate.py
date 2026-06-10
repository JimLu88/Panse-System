from __future__ import annotations

import threading
import time

import pytest

from apps.core.orchestrator.outbound_gate import OutboundGateLoop


@pytest.fixture
def gate() -> OutboundGateLoop:
    g = OutboundGateLoop()
    yield g
    g.shutdown()


def test_outbound_gate_runs_delayed_callback(gate: OutboundGateLoop) -> None:
    done = threading.Event()

    def fire() -> None:
        done.set()

    gate.schedule(0.08, fire)
    assert done.wait(timeout=2.0)


def test_outbound_gate_cancel(gate: OutboundGateLoop) -> None:
    done = threading.Event()

    def fire() -> None:
        done.set()

    fut = gate.schedule(1.0, fire)
    fut.cancel()
    time.sleep(0.15)
    assert not done.is_set()


def test_outbound_gate_schedule_runs_on_asyncio_loop(gate: OutboundGateLoop) -> None:
    """回调在独立线程的 asyncio 循环上执行（与 OutboundGateLoop 实现一致）。"""
    seen: list[str] = []

    def mark() -> None:
        seen.append(threading.current_thread().name)

    fut = gate.schedule(0.05, mark)
    fut.result(timeout=2.0)
    assert seen and seen[0] == "OutboundGateLoop"


def test_outbound_gate_serial_delays(gate: OutboundGateLoop) -> None:
    order: list[int] = []

    gate.schedule(0.06, lambda: order.append(1))
    gate.schedule(0.12, lambda: order.append(2))
    time.sleep(0.35)
    assert order == [1, 2]
