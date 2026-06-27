"""
[F] Disconnect & reconnect — full lifecycle.

Business scenarios:
- User accidentally disconnects then reconnects with same NodeId.
- Node crashes and restarts with same NodeId.
- Repeated disconnect should not panic.

Test angles: [方法] [边界] [生命周期]
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── F1 ──────────────────────────────────────────────────────────────────

async def test_disconnect_removes_from_graph(bus):
    """[方法] disconnect removes node from graph."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    assert len(bus.graph().nodes) == 2
    await h1.disconnect()

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "engine/b"


async def test_disconnect_broadcasts_node_offline(bus):
    """[方法] disconnect broadcasts node_offline that other nodes receive."""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    _ = await h2.recv()  # h1's node_online
    await h1.disconnect()

    msg = await h2.recv()
    assert msg.msg_type == "node_offline"
    assert str(msg.sender) == "engine/a"


# ── F2 ──────────────────────────────────────────────────────────────────

async def test_disconnected_handle_methods_raise(bus):
    """[边界] After disconnect, send/recv/node_info/filter_config all raise 'disconnected'."""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    await h.disconnect()

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.send("action", [], {})

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.recv()

    with pytest.raises(RuntimeError, match="disconnected"):
        h.node_info()

    with pytest.raises(RuntimeError, match="disconnected"):
        h.filter_config()


# ── F3 ──────────────────────────────────────────────────────────────────

async def test_disconnect_twice_raises(bus):
    """[边界] Double disconnect → RuntimeError 'already disconnected'."""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    await h.disconnect()

    with pytest.raises(RuntimeError, match="disconnected"):
        await h.disconnect()


# ── F4 ──────────────────────────────────────────────────────────────────

async def test_reconnect_same_node_id(bus):
    """[生命周期] disconnect then reconnect same NodeId succeeds; old handle broken.

    Business: node crashes then restarts with same NodeId.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)

    primary = await bus.connect(NodeInfo("worker/main", "worker", {}), f)
    await primary.disconnect()

    # Reconnect with same NodeId
    primary2 = await bus.connect(NodeInfo("worker/main", "worker", {}), f)

    await sender.send("job", [], {"id": 1})
    msg = await primary2.recv()
    assert msg.payload == {"id": 1}

    # Old handle is dead
    with pytest.raises(RuntimeError, match="disconnected"):
        await primary.send("job", [], {})


# ── F5 ──────────────────────────────────────────────────────────────────

async def test_reconnect_cycle_multiple_rounds(bus):
    """[生命周期] Same NodeId disconnect→reconnect 3 rounds, graph always correct.

    Business: node frequently crashes and recovers. Bus must not leak or corrupt.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)

    for round_num in range(3):
        h = await bus.connect(NodeInfo("flaky/node", "worker", {"round": round_num}), f)

        g = bus.graph()
        nodes = [n for n in g.nodes if str(n.node_id) == "flaky/node"]
        assert len(nodes) == 1
        assert nodes[0].capabilities == {"round": round_num}

        await h.disconnect()

        g2 = bus.graph()
        assert all(str(n.node_id) != "flaky/node" for n in g2.nodes)

    assert len(bus.graph().nodes) == 0
