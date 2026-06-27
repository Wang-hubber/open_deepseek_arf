"""
[I] Concurrency — try_recv/recv lock conflict & concurrent send/recv.

Test angles: [并发]
"""
import asyncio
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── I1 ──────────────────────────────────────────────────────────────────

async def test_try_recv_during_recv_lock_conflict(bus):
    """[并发] try_recv while recv holds lock → RuntimeError 'concurrent recv'."""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())

    # h.recv() returns a Future (pyo3 future_into_py), not a coroutine.
    # asyncio.create_task only accepts coroutines, so use ensure_future.
    recv_fut = asyncio.ensure_future(h.recv())
    await asyncio.sleep(0.05)

    with pytest.raises(RuntimeError, match="concurrent recv"):
        h.try_recv()

    # Send a message to unblock the recv future
    helper = await bus.connect(NodeInfo("engine/helper", "engine", {}), MessageFilter())
    await helper.send("wakeup", [], {})
    await recv_fut


# ── I2 ──────────────────────────────────────────────────────────────────

async def test_concurrent_send_recv_no_lost_messages(bus):
    """[并发] Concurrent send + recv — no lost messages.

    Uses asyncio.gather for concurrent send and recv.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)
    receiver = await bus.connect(NodeInfo("engine/r", "engine", {}), f)

    async def send_batch(start, count):
        for i in range(start, start + count):
            await sender.send("msg", [], {"seq": i})

    async def recv_batch(count):
        received = []
        for _ in range(count):
            msg = await receiver.recv()
            received.append(msg.payload["seq"])
        return received

    send_task = asyncio.create_task(send_batch(0, 20))
    recv_task = asyncio.create_task(recv_batch(20))

    await send_task
    result = await recv_task

    assert len(result) == 20
    assert result == list(range(20))
