"""独立线程 asyncio 循环：可取消的延迟后再执行回调（不阻塞 SequentialExecutor）。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future


class OutboundGateLoop:
    """
    在后台线程运行 ``asyncio`` 事件循环。
    ``schedule`` 返回的 Future 可 ``cancel()`` 以取消尚未触发的延迟任务。
    """

    def __init__(self) -> None:
        self._thr: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thr and self._thr.is_alive():
            return

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            loop.run_forever()

        self._ready.clear()
        self._thr = threading.Thread(target=_runner, name="OutboundGateLoop", daemon=True)
        self._thr.start()
        if not self._ready.wait(timeout=8.0):
            raise RuntimeError("OutboundGateLoop 启动超时")

    def schedule(self, delay_s: float, action: Callable[[], None]) -> Future[None]:
        if not self._loop or not self._thr or not self._thr.is_alive():
            self.start()
        assert self._loop is not None

        async def _job() -> None:
            await asyncio.sleep(max(0.0, float(delay_s)))
            action()

        return asyncio.run_coroutine_threadsafe(_job(), self._loop)

    def shutdown(self, timeout_s: float = 4.0) -> None:
        """停止后台事件循环（测试或进程退出时调用）。"""
        loop = self._loop
        thr = self._thr
        if loop is not None and thr is not None and thr.is_alive():

            def _stop() -> None:
                loop.stop()

            loop.call_soon_threadsafe(_stop)
            thr.join(timeout=timeout_s)
        self._thr = None
        self._loop = None
        self._ready.clear()
