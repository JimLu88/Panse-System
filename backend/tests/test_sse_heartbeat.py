"""SSE 保活心跳 (2026-06-23 降 NAS 负载): subscribe(heartbeat_s) 空闲时发 _heartbeat, 不丢订阅。"""
import asyncio

from app.services import sse_bus


def test_subscribe_emits_heartbeat_when_idle():
    """空闲超过 heartbeat_s 没事件 → yield {event:_heartbeat}(让上层发保活注释)。"""
    async def go():
        agen = sse_bus.subscribe(heartbeat_s=0.05)
        try:
            return await asyncio.wait_for(agen.__anext__(), timeout=2.0)
        finally:
            await agen.aclose()
    assert asyncio.run(go()) == {"event": "_heartbeat"}


def test_subscribe_delivers_event_and_keeps_subscription_after_heartbeat():
    """真实事件正常送达; 且心跳后订阅仍在(能继续收到下一个事件)。"""
    async def go():
        agen = sse_bus.subscribe(heartbeat_s=0.05)
        fut = asyncio.ensure_future(agen.__anext__())   # 驱动生成器注册队列
        await asyncio.sleep(0.01)
        sse_bus.publish("alert", {"x": 1})
        first = await asyncio.wait_for(fut, timeout=2.0)
        hb = await asyncio.wait_for(agen.__anext__(), timeout=2.0)   # 空闲 → 心跳
        await asyncio.sleep(0.01)
        sse_bus.publish("order", {"y": 2})
        second = await asyncio.wait_for(agen.__anext__(), timeout=2.0)  # 心跳后仍能收事件
        await agen.aclose()
        return first, hb, second
    first, hb, second = asyncio.run(go())
    assert first == {"event": "alert", "data": {"x": 1}}
    assert hb == {"event": "_heartbeat"}
    assert second == {"event": "order", "data": {"y": 2}}


def test_subscribe_without_heartbeat_is_unchanged():
    """不传 heartbeat_s → 旧行为: 不发心跳, 直接送事件。"""
    async def go():
        agen = sse_bus.subscribe()
        fut = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.01)
        sse_bus.publish("alert", {"a": 1})
        msg = await asyncio.wait_for(fut, timeout=2.0)
        await agen.aclose()
        return msg
    assert asyncio.run(go()) == {"event": "alert", "data": {"a": 1}}
