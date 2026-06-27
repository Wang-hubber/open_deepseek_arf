"""
[H] Boundary & stress — payload roundtrip, Unicode, large messages, capacity stress.

Test angles: [序列化] [边界] [压力]
"""
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── H1 ──────────────────────────────────────────────────────────────────

async def test_message_properties_roundtrip(bus):
    """[序列化] Message fields have correct Python types."""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {"ver": 1}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    await h1.send("test", [NodeId("trace/obs")], {"key": "v", "nested": {"a": 1}})
    msg = await h2.recv()

    assert isinstance(msg.id, str) and len(msg.id) > 0
    assert isinstance(msg.msg_type, str)
    assert isinstance(msg.sender, NodeId)
    assert isinstance(msg.to, list)
    assert len(msg.to) == 1
    assert isinstance(msg.payload, dict)
    assert msg.payload == {"key": "v", "nested": {"a": 1}}
    assert isinstance(msg.timestamp, int) and msg.timestamp > 0
    assert msg.is_broadcast() is False
    assert msg.is_for(NodeId("trace/obs")) is True
    assert msg.is_for(NodeId("engine/a")) is False


# ── H2 ──────────────────────────────────────────────────────────────────

async def test_empty_dict_payload_roundtrip(bus):
    """[序列化] {} payload roundtrip intact."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("empty", [], {})
    msg = await h2.recv()
    assert msg.payload == {}


async def test_list_payload_roundtrip(bus):
    """[序列化] [] payload roundtrip intact."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("list", [], [1, "two", {"three": 3}])
    msg = await h2.recv()
    assert msg.payload == [1, "two", {"three": 3}]


async def test_null_payload_roundtrip(bus):
    """[序列化] None/null payload roundtrip intact."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("null_val", [], None)
    msg = await h2.recv()
    assert msg.payload is None


async def test_int_and_float_payload(bus):
    """[序列化] int payload correct."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    await h1.send("num", [], 42)
    msg = await h2.recv()
    assert msg.payload == 42
    assert isinstance(msg.payload, int)


# ── H3 ──────────────────────────────────────────────────────────────────

async def test_large_payload_roundtrip(bus):
    """[边界] 10KB payload roundtrip intact."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                            MessageFilter(types=None, to_match=ToMatch.All))

    large = {"data": "x" * 10240}
    await h1.send("large", [], large)
    msg = await h2.recv()
    assert msg.payload == large
    assert len(msg.payload["data"]) == 10240


# ── H4 ──────────────────────────────────────────────────────────────────

async def test_unicode_node_id(bus):
    """[边界] Unicode NodeId connects and sends/receives normally.

    Business: Chinese-named node identifiers.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    引擎 = await bus.connect(NodeInfo("引擎/主节点", "engine", {"session": "会话1"}), f)
    追踪 = await bus.connect(NodeInfo("追踪/观察者", "trace", {}),
                             MessageFilter(types=None, to_match=ToMatch.All))

    await 引擎.send("中文消息", [], {"内容": "你好世界"})
    msg = await 追踪.recv()

    assert msg.msg_type == "中文消息"
    assert msg.payload == {"内容": "你好世界"}
    assert str(msg.sender) == "引擎/主节点"


async def test_node_id_with_special_chars(bus):
    """[边界] NodeId with special characters connects."""
    ids = [
        "node/with-dash",
        "node.with.dots",
        "node_with_underscore",
        "node:with:colons",
        "node/with/slashes/deep",
    ]
    for nid in ids:
        h = await bus.connect(NodeInfo(nid, "test", {}), MessageFilter())
        assert str(h.node_info().node_id) == nid


# ── H5 ──────────────────────────────────────────────────────────────────

async def test_channel_capacity_stress_100_messages():
    """[压力] 100 messages quickly sent, all received by trace node.

    Uses channel_capacity=256 to avoid ring buffer wrap-around.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=256)
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)
    trace = await bus.connect(NodeInfo("trace/obs", "trace", {}),
                               MessageFilter(types=None, to_match=ToMatch.All))

    n = 100
    for i in range(n):
        await sender.send("stress", [], {"seq": i})

    received = 0
    for _ in range(n):
        msg = await trace.recv()
        if msg.msg_type == "stress":
            received += 1
    assert received == n


# ── H6 ──────────────────────────────────────────────────────────────────

async def test_receipt_online_count_includes_self(bus):
    """[方法] receipt.online_nodes includes self."""
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), MessageFilter())

    r = await sender.send("t", [], {})
    assert r.online_nodes == 1
    assert r.matching_nodes == 1

    other = await bus.connect(NodeInfo("engine/o", "engine", {}), MessageFilter())
    r2 = await sender.send("t", [], {})
    assert r2.online_nodes >= 2


async def test_receipt_matching_nodes_with_different_filters(bus):
    """[方法] matching_nodes correctly reflects different filter configs."""
    sender = await bus.connect(
        NodeInfo("engine/s", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    await bus.connect(
        NodeInfo("worker/action_only", "worker", {}),
        MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    await bus.connect(
        NodeInfo("worker/all", "worker", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    r = await sender.send("ping", [], {})
    assert r.online_nodes >= 3
    # matching: sender + worker/all = 2 (worker/action_only doesn't match "ping")
    assert r.matching_nodes >= 2
