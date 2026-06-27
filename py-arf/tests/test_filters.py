"""
[E] Filter behavior — MessageFilter types and to_match control receiving.

Test angles: [类型] [覆盖]
"""
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── E1 ──────────────────────────────────────────────────────────────────

async def test_filter_types_restricts(bus):
    """[类型] types=["action"] only receives action, others filtered."""
    h1 = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=["action"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    h2 = await bus.connect(
        NodeInfo("engine/b", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await h2.send("ping", [], {"n": 1})
    await h2.send("action", [], {"n": 2})
    await h2.send("pong", [], {"n": 3})

    msg = await h1.recv()
    assert msg.msg_type == "action"
    assert msg.payload == {"n": 2}


# ── E2 ──────────────────────────────────────────────────────────────────

async def test_filter_directed_to_me(bus):
    """[类型] DirectedToMe does not receive broadcasts."""
    target = NodeId("mcp/fs")
    worker = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {}),
        MessageFilter(types=None, to_match=ToMatch.DirectedToMe),
    )
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    await engine.send("broadcast_msg", [], {"x": 1})
    await engine.send("direct_msg", [target], {"x": 2})

    msg = await worker.recv()
    assert msg.msg_type == "direct_msg"
    assert msg.payload == {"x": 2}

    assert worker.try_recv() is None


# ── E3 ──────────────────────────────────────────────────────────────────

async def test_filter_all_trace_node(bus):
    """[覆盖] ToMatch.All + types=None trace node sees everything."""
    trace = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )
    a = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # trace sees a's node_online (trace.rx created before a, so it catches the broadcast)
    while trace.try_recv() is not None:
        pass

    await a.send("broadcast", [], {"to": "all"})
    await a.send("directed", [NodeId("trace/obs")], {"to": "b"})

    received = set()
    for _ in range(2):
        msg = await trace.recv()
        received.add(msg.msg_type)
    assert "broadcast" in received
    assert "directed" in received


# ── E4: multiple same-filter nodes independent ──────────────────────────

async def test_filter_multiple_same_config_independent(bus):
    """[类型] 3 nodes same filter but different NodeId — independent filtering.

    Bus runs filter.matches() independently per node; same filter config
    does not share state.
    """
    f = MessageFilter(types=["job"], to_match=ToMatch.BroadcastOnly)
    engine = await bus.connect(
        NodeInfo("engine/a", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    w1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    w2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    w3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)

    await engine.send("job", [], {"id": 1})
    await engine.send("job", [NodeId("worker/1")], {"id": 2})

    # All three workers only receive broadcast job (id=1), not directed (id=2)
    for w in [w1, w2, w3]:
        msg = await w.recv()
        assert msg.payload == {"id": 1}
        assert w.try_recv() is None
