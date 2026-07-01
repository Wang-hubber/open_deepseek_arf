"""[E2E] py-arf Bus lifecycle: connect/disconnect/shutdown + filters.

[构造] [方法] [边界] [类型]

Mirrors py-arf/tests/test_lifecycle.py and test_filters.py — these are
the primary E2E scenarios. This module re-tests them under the e2e/
directory with the e2e conftest fixtures.
"""
import asyncio

import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


def test_bus_create_with_defaults():
    """[构造] Bus() with no args creates successfully."""
    bus = Bus()
    g = bus.graph()
    assert g.nodes == []
    assert g.message_count == 0


@pytest.mark.asyncio
async def test_bus_connect_single_node(live_bus):
    """[方法] Single node connect shows in graph."""
    h = await live_bus.connect(
        NodeInfo("engine/main", "engine", {"session": "s1"}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    g = live_bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "engine/main"
    assert g.nodes[0].node_type == "engine"
    _ = h  # keep handle alive


@pytest.mark.asyncio
async def test_bus_broadcast_reaches_all_nodes(live_bus):
    """[方法] Broadcast (to=[]) reaches all nodes subscribed via All / BroadcastAndDirectedToMe.

    Mirrors test_lifecycle.py::test_send_broadcast_and_recv — two nodes,
    one sender, one receiver. Drains any pending `node_online` lifecycle
    messages first so the asserted `broadcast` payload is unambiguous.
    """
    sender = await live_bus.connect(
        NodeInfo("engine/sender", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    receiver_a = await live_bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    receiver_b = await live_bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # Drain any pending node_online messages from the queue.
    for h in (sender, receiver_a, receiver_b):
        while True:
            try:
                m = h.try_recv()
            except Exception:
                m = None
            if m is None:
                break

    await sender.send("broadcast", [], {"payload": "ping"})

    # Now collect from each receiver until we see our broadcast payload.
    async def _recv_until(handle, want_type, timeout=2.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                m = await asyncio.wait_for(handle.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if m.msg_type == want_type:
                return m
        return None

    msg_a = await _recv_until(receiver_a, "broadcast")
    msg_b = await _recv_until(receiver_b, "broadcast")
    assert msg_a is not None, "receiver_a never saw 'broadcast'"
    assert msg_b is not None, "receiver_b never saw 'broadcast'"
    assert msg_a.payload == {"payload": "ping"}
    assert msg_b.payload == {"payload": "ping"}


@pytest.mark.asyncio
async def test_bus_filter_excludes_other_types(live_bus):
    """[边界] types=[...] filter excludes messages of other types.

    Mirrors test_filters.py::test_filter_types_restricts — node with
    filter types=["action"] should not receive "ping" or "pong".
    """
    sender = await live_bus.connect(
        NodeInfo("engine/sender", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    filtered = await live_bus.connect(
        NodeInfo("engine/filtered", "engine", {}),
        MessageFilter(
            types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe
        ),
    )

    await sender.send("ping", [], {"n": 1})
    await sender.send("action", [], {"n": 2})
    await sender.send("pong", [], {"n": 3})

    # filtered only sees "action"
    msg = await asyncio.wait_for(filtered.recv(), timeout=2.0)
    assert msg.msg_type == "action"
    assert msg.payload == {"n": 2}

    # Verify no further messages arrive within 200ms.
    try:
        extra = await asyncio.wait_for(filtered.recv(), timeout=0.2)
        pytest.fail(
            f"expected no more messages, got msg_type={extra.msg_type!r}"
        )
    except asyncio.TimeoutError:
        pass


@pytest.mark.asyncio
async def test_bus_shutdown_disconnects_node(live_bus):
    """[边界] bus.shutdown() prevents subsequent send.

    Mirrors test_lifecycle.py::test_connect_after_shutdown_raises — after
    shutdown, connecting/operating on the bus should fail. We verify that
    a node registered before shutdown cannot send afterwards.
    """
    h = await live_bus.connect(
        NodeInfo("engine/late", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await live_bus.shutdown()

    # Subsequent send should fail with an exception.
    with pytest.raises(Exception):
        await h.send("after_shutdown", [], {})