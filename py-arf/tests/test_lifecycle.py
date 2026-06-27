"""
[B+C] Bus lifecycle + send/recv — create/connect/send/recv/disconnect basics.

Test angles: [构造] [方法] [边界] [序列化]
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ═══════════════════════════════════════════════════════════════════════
# B — Bus Lifecycle
# ═══════════════════════════════════════════════════════════════════════


# ── B1 ──────────────────────────────────────────────────────────────────

def test_create_bus_defaults():
    """[构造] Default Bus() creates, graph() empty, message_count=0."""
    bus = Bus()
    g = bus.graph()
    assert g.nodes == []
    assert g.message_count == 0
    assert g.uptime_ms >= 0


# ── B2 ──────────────────────────────────────────────────────────────────

def test_create_bus_custom_params():
    """[构造] Custom params Bus creation."""
    bus = Bus(heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=128)
    g = bus.graph()
    assert g.message_count == 0


# ── B3 ──────────────────────────────────────────────────────────────────

async def test_connect_single_node(bus):
    """[方法] Single node connect → graph contains it."""
    h = await bus.connect(
        NodeInfo("engine/main", "engine", {"session": "s1"}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "engine/main"
    assert g.nodes[0].node_type == "engine"


# ── B4 ──────────────────────────────────────────────────────────────────

async def test_connect_multiple_nodes(bus):
    """[方法] Three nodes connect → graph contains all."""
    await bus.connect(NodeInfo("engine/main", "engine", {}), MessageFilter())
    await bus.connect(NodeInfo("mcp/fs", "mcp", {}), MessageFilter())
    await bus.connect(NodeInfo("trace/obs", "trace", {}),
                      MessageFilter(types=None, to_match=ToMatch.All))

    g = bus.graph()
    assert len(g.nodes) == 3
    ids = {str(n.node_id) for n in g.nodes}
    assert ids == {"engine/main", "mcp/fs", "trace/obs"}
    # message_count only counts app messages (BusCommand::Send), not lifecycle
    assert bus.message_count == 0


# ── B5: duplicate NodeId ────────────────────────────────────────────────

async def test_connect_duplicate_node_id_rejected(bus):
    """[边界] Duplicate NodeId connect → Exception, graph unchanged.

    Business: user may accidentally define duplicate node names.
    Bus rejects duplicate connections.
    """
    await bus.connect(NodeInfo("engine/main", "engine", {}), MessageFilter())

    dup = NodeInfo("engine/main", "engine", {})
    with pytest.raises(Exception, match="already connected"):
        await bus.connect(dup, MessageFilter())

    assert len(bus.graph().nodes) == 1
    assert bus.message_count == 0  # lifecycle messages not counted


# ── B6: connect after shutdown ──────────────────────────────────────────

async def test_connect_after_shutdown_raises(bus):
    """[边界] connect after shutdown raises Exception."""
    await bus.shutdown()
    with pytest.raises(Exception):
        await bus.connect(NodeInfo("late", "engine", {}), MessageFilter())


# ═══════════════════════════════════════════════════════════════════════
# C — Send / Recv
# ═══════════════════════════════════════════════════════════════════════


# ── C1 ──────────────────────────────────────────────────────────────────

async def test_send_broadcast_and_recv(bus):
    """[方法] Broadcast (to=[]) received by another node's recv()."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    receipt = await h1.send("action", [], {"cmd": "greet"})
    assert receipt.online_nodes >= 2
    assert len(receipt.message_id) > 0

    msg = await h2.recv()
    assert msg.msg_type == "action"
    assert msg.payload == {"cmd": "greet"}
    assert str(msg.sender) == "engine/a"
    assert msg.is_broadcast() is True


# ── C2 ──────────────────────────────────────────────────────────────────

async def test_send_multiple_messages_ordered(bus):
    """[序列化] 5 messages received FIFO."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    for i in range(5):
        await h1.send("tick", [], {"seq": i})

    received = []
    for _ in range(5):
        msg = await h2.recv()
        received.append(msg.payload["seq"])
    assert received == [0, 1, 2, 3, 4]


# ── C3 ──────────────────────────────────────────────────────────────────

async def test_send_directed_message(bus):
    """[方法] Directed message only seen by target."""
    node_b = NodeId("engine/b")
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    receipt = await h1.send("whisper", [node_b], {"secret": 42})
    assert receipt.online_nodes >= 2

    msg = await h2.recv()
    assert msg.msg_type == "whisper"
    assert msg.is_broadcast() is False
    assert msg.is_for(node_b) is True
    assert msg.is_for(NodeId("engine/a")) is False


# ── C4: send to all-offline targets ─────────────────────────────────────

async def test_send_to_all_offline_targets_raises(bus):
    """[边界] All targets offline → Exception.

    Business: load balancing — primary node down, standby not yet active.
    """
    h = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())

    ghost = NodeId("ghost/node")
    with pytest.raises(Exception, match="target nodes offline"):
        await h.send("ping", [ghost], {})


# ── C5: send to partial offline targets ─────────────────────────────────

async def test_send_to_partial_offline_still_succeeds(bus):
    """[边界] Partial offline targets → still succeeds, online targets receive."""
    target_online = NodeId("engine/b")
    target_offline = NodeId("ghost/node")

    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())

    receipt = await h1.send("ping", [target_online, target_offline], {"x": 1})
    assert receipt.online_nodes >= 2

    msg = await h2.recv()
    assert msg.payload == {"x": 1}
    assert msg.is_for(target_online) is True


# ── C6 ──────────────────────────────────────────────────────────────────

async def test_try_recv_no_message_returns_none(bus):
    """[边界] No message → try_recv returns None."""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    result = h.try_recv()
    assert result is None
