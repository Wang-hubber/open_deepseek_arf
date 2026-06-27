"""
[J] Resource leak & process residue — memory leak, zombie entry, resource cleanup.

Mirrors the 7 Rust leak-detection tests (L1-L7) at the Python binding level,
plus additional Python-specific GC/stress/concurrent-cleanup tests.

PyO3 note: #[pyclass] objects do not support weakref.ref(), so Python-side
leak verification uses repeated create/destroy cycles + graph inspection instead.

Test angles: [泄漏] [清理] [压力] [并发]
"""
import asyncio
import gc
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ═══════════════════════════════════════════════════════════════════════
# J1-J2: Zombie entry lifecycle (Python mirrors of Rust L1-L2)
# ═══════════════════════════════════════════════════════════════════════


# ── J1: Handle drop 不调用 disconnect → zombie entry ─────────────────

async def test_handle_drop_without_disconnect_leaves_zombie():
    """[泄漏] Python drop handle without disconnect → zombie entry remains in nodes map.

    Python mirror of Rust L1. A Python user may let a NodeHandle go out of scope
    without awaiting disconnect(). The NodeEntry survives in the nodes map and
    blocks reconnection with the same NodeId.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)

    async def connect_and_drop():
        h = await bus.connect(
            NodeInfo("crash-victim", "test", {}),
            MessageFilter(),
        )
        # h dropped here — no disconnect()

    await connect_and_drop()

    # NodeEntry still present in graph
    g = bus.graph()
    zombie = [n for n in g.nodes if str(n.node_id) == "crash-victim"]
    assert len(zombie) == 1, (
        "BUG: dropped handle was immediately removed — "
        "zombie entry should persist until heartbeat timeout"
    )

    # Reconnect with same NodeId → rejected
    with pytest.raises(Exception, match="already connected"):
        await bus.connect(
            NodeInfo("crash-victim", "test", {}),
            MessageFilter(),
        )

    await bus.shutdown()


# ── J2: zombie 被心跳超时清理 ────────────────────────────────────────

async def test_zombie_cleaned_by_heartbeat_timeout():
    """[泄漏] Zombie entry cleaned by heartbeat timeout; node_offline broadcast; reconnect allowed.

    Python mirror of Rust L2. Uses fast heartbeat parameters to speed up the test.
    After timeout, the zombie is evicted and the same NodeId can reconnect.
    """
    bus = Bus(heartbeat_interval_ms=20, heartbeat_timeout_ms=60, channel_capacity=16)

    # Watcher node to observe node_offline broadcast
    watcher = await bus.connect(
        NodeInfo("watcher", "test", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    # Create zombie (drop without disconnect)
    async def create_zombie():
        _zombie = await bus.connect(
            NodeInfo("zombie", "test", {}),
            MessageFilter(),
        )
        # _zombie dropped here — no disconnect()

    await create_zombie()

    # Drain zombie's node_online from watcher
    drain_msg = await watcher.recv()
    assert drain_msg is not None

    # Wait for heartbeat timeout to evict zombie.
    # With interval=20ms, timeout=60ms, eviction happens around tick 4 (~80ms).
    # We poll with timeout up to ~500ms to be safe.
    saw_offline = False
    for _ in range(30):
        try:
            msg = await asyncio.wait_for(watcher.recv(), timeout=0.1)
            if msg.msg_type == "node_offline" and str(msg.sender) == "zombie":
                saw_offline = True
                break
        except asyncio.TimeoutError:
            continue

    assert saw_offline, (
        "BUG: zombie node should be cleaned by heartbeat timeout "
        "and broadcast node_offline"
    )

    # Graph no longer contains zombie
    g = bus.graph()
    zombie_nodes = [n for n in g.nodes if str(n.node_id) == "zombie"]
    assert len(zombie_nodes) == 0, "BUG: zombie entry not cleaned from nodes map"

    # Same NodeId can now reconnect
    h = await bus.connect(
        NodeInfo("zombie", "test", {}),
        MessageFilter(),
    )
    assert h is not None

    await watcher.disconnect()
    await h.disconnect()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J3-J4: Connect/disconnect accumulation (Python mirrors of Rust L6-L7)
# ═══════════════════════════════════════════════════════════════════════


# ── J3: 反复 connect/disconnect 同 NodeId — nodes map 不累积 ─────────

async def test_repeated_connect_disconnect_no_accumulation():
    """[泄漏] 50 rounds of disconnect→reconnect same NodeId, graph never accumulates.

    Python mirror of Rust L6. Each disconnect must immediately remove the entry
    so the next connect is a fresh insertion, not a duplicate.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    for round_num in range(50):
        h = await bus.connect(
            NodeInfo("flappy", "worker", {"round": round_num}),
            MessageFilter(),
        )

        # Graph has exactly 1 "flappy" entry
        g = bus.graph()
        flappy_entries = [n for n in g.nodes if str(n.node_id) == "flappy"]
        assert len(flappy_entries) == 1, (
            f"BUG round {round_num}: expected 1 flappy entry, got {len(flappy_entries)}"
        )

        await h.disconnect()

        # After disconnect, flappy is gone
        g2 = bus.graph()
        assert not any(str(n.node_id) == "flappy" for n in g2.nodes), (
            f"BUG round {round_num}: flappy not removed after disconnect"
        )

    # Final graph is empty
    assert len(bus.graph().nodes) == 0

    await bus.shutdown()


# ── J4: 全部 disconnect 后 graph 为空 ─────────────────────────────────

async def test_graph_empty_after_all_disconnected():
    """[泄漏] After all 10 nodes disconnect, graph is empty.

    Python mirror of Rust L7. Ensures no stale entries remain.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    handles = []
    for i in range(10):
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        handles.append(h)

    assert len(bus.graph().nodes) == 10

    # Disconnect all
    for h in handles:
        await h.disconnect()

    g = bus.graph()
    assert len(g.nodes) == 0, (
        f"BUG: nodes map not empty after all disconnected: {len(g.nodes)} nodes remain"
    )

    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J5-J6: Shutdown & drop cleanup (Python mirrors of Rust L3, L5)
# ═══════════════════════════════════════════════════════════════════════


# ── J5: signal_shutdown 关闭 broadcast channel → 所有 handle recv 报错 ─

async def test_signal_shutdown_closes_broadcast_channel_for_all():
    """[清理] signal_shutdown closes broadcast channel; all handles get Closed error.

    Python mirror of Rust L3. After bus.shutdown() (which calls signal_shutdown),
    the broadcast_tx Sender is dropped, closing the channel for all receivers.
    After draining buffered messages, every connected handle's recv() raises.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=64)

    handles = []
    for i in range(5):
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        handles.append(h)

    # Send messages so ring buffer has data
    await handles[0].send("pre_shutdown", [], {"n": 1})
    await handles[0].send("pre_shutdown", [], {"n": 2})

    await bus.shutdown()

    # Every handle: after draining buffered messages, recv raises
    for h in handles:
        # Drain buffered messages
        while True:
            try:
                m = h.try_recv()
                if m is None:
                    break
            except Exception:
                break  # channel already closed
        # Now recv should raise (buffer empty, channel closed)
        with pytest.raises(Exception):
            await h.recv()


# ── J6: Bus drop 不调用 shutdown → spawned task 正常退出 ─────────────

async def test_bus_drop_without_shutdown_no_hang():
    """[泄漏] Bus GC'd without explicit shutdown — spawned task exits cleanly, no hang.

    Python mirror of Rust L5. When the Bus object is garbage collected
    (cmd_tx dropped → message loop exits), the process must not hang.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)
    h = await bus.connect(
        NodeInfo("n", "test", {}),
        MessageFilter(),
    )
    await h.disconnect()

    # Drop bus without shutdown — simulate GC
    del bus
    gc.collect()

    # Give spawned task time to exit
    await asyncio.sleep(0.1)

    # No hang, no panic — test passes
    assert True


# ═══════════════════════════════════════════════════════════════════════
# J7-J8: Python GC & object lifecycle
# ═══════════════════════════════════════════════════════════════════════


# ── J7: Bus drop 后 tokio runtime 释放，新 Bus 可正常创建 ────────────

async def test_new_bus_after_previous_bus_garbage_collected():
    """[泄漏] After Bus is GC'd (no explicit shutdown), a new Bus can be created.

    PyO3 objects do not support weakref. Instead, verify that the tokio runtime
    is properly released when the Bus is dropped, so a fresh Bus works correctly.
    """
    for i in range(5):
        bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=32)
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        await h.disconnect()
        # No shutdown — let Python GC handle it
        del bus
        gc.collect()
        await asyncio.sleep(0.01)

    # All 5 Bus instances created and GC'd without issue
    assert True


# ── J8: Handle disconnect 后可立即 drop 无副作用 ─────────────────────

async def test_handle_drop_after_disconnect_no_side_effects():
    """[泄漏] After disconnect, dropping the handle does not affect other handles.

    PyO3 objects do not support weakref. Instead, verify that dropping a
    disconnected handle does not interfere with other handles on the same Bus.
    """
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=64)

    # Connect multiple handles
    h1 = await bus.connect(NodeInfo("node-1", "test", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("node-2", "test", {}), MessageFilter())
    h3 = await bus.connect(NodeInfo("node-3", "test", {}), MessageFilter())

    assert len(bus.graph().nodes) == 3

    # Disconnect h1 and drop it
    await h1.disconnect()
    del h1
    gc.collect()

    # Other handles still work — graph has 2 nodes
    assert len(bus.graph().nodes) == 2

    # h3 drain h1's node_offline before receiving application message
    offline_msg = await h3.recv()
    assert offline_msg.msg_type == "node_offline"
    assert str(offline_msg.sender) == "node-1"

    await h2.send("msg", [], {"to": "h3"})
    msg = await h3.recv()
    assert msg.payload == {"to": "h3"}

    await h2.disconnect()
    await h3.disconnect()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J9-J10: Stress — rapid cycles
# ═══════════════════════════════════════════════════════════════════════


# ── J9: 100 轮快速断连重连 ────────────────────────────────────────────

async def test_rapid_connect_disconnect_stress_100_rounds():
    """[压力] 100 rounds of rapid connect→disconnect, no crash, no resource exhaustion.

    More aggressive than F5 (3 rounds) and J3 (50 rounds with single NodeId).
    Uses multiple NodeIds in rotation to stress the full connect/disconnect path.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    for round_num in range(100):
        node_id = f"node-{round_num % 5}"
        h = await bus.connect(
            NodeInfo(node_id, "worker", {"seq": round_num}),
            MessageFilter(),
        )
        await h.disconnect()

    # Final graph should be empty
    g = bus.graph()
    assert len(g.nodes) == 0, (
        f"BUG: {len(g.nodes)} nodes remain after 100 disconnect rounds"
    )

    await bus.shutdown()


# ── J10: 同 NodeId 100 轮断连重连不累积 ──────────────────────────────

async def test_same_node_id_100_reconnect_cycles_no_accumulation():
    """[压力] Same NodeId 100 disconnect→reconnect cycles, graph never accumulates.

    Verifies that rapid reconnect with the same identity does not cause
    node map bloat or stale entries.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    for round_num in range(100):
        h = await bus.connect(
            NodeInfo("sole-node", "worker", {}),
            MessageFilter(),
        )

        g = bus.graph()
        sole_entries = [n for n in g.nodes if str(n.node_id) == "sole-node"]
        assert len(sole_entries) == 1, (
            f"BUG round {round_num}: expected 1 entry, got {len(sole_entries)}"
        )

        await h.disconnect()

        g2 = bus.graph()
        assert not any(str(n.node_id) == "sole-node" for n in g2.nodes), (
            f"BUG round {round_num}: entry not removed"
        )

    assert len(bus.graph().nodes) == 0
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# J11-J13: Multi-Bus lifecycle, shutdown consistency, concurrent cleanup
# ═══════════════════════════════════════════════════════════════════════


# ── J11: 多个 Bus 实例顺序创建销毁 ───────────────────────────────────

async def test_multiple_bus_lifecycle_no_accumulation():
    """[泄漏] 5 Bus instances created and shutdown sequentially, no resource accumulation.

    Ensures each Bus fully releases its tokio runtime resources before
    the next one is created.
    """
    for i in range(5):
        bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=32)
        h = await bus.connect(
            NodeInfo(f"node-{i}", "test", {}),
            MessageFilter(),
        )
        await h.send("msg", [], {"bus": i})
        await h.disconnect()
        await bus.shutdown()

        # Allow resources to release
        await asyncio.sleep(0.01)

    # No crash, no hang — test passes
    assert True


# ── J12: shutdown 后所有内部状态一致 ─────────────────────────────────

async def test_shutdown_state_consistency():
    """[清理] After shutdown: graph accessible, no send/recv possible,
    message_count frozen, all connected handles broken consistently.
    """
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=500, channel_capacity=32)

    h1 = await bus.connect(
        NodeInfo("node-a", "test", {}),
        MessageFilter(),
    )
    h2 = await bus.connect(
        NodeInfo("node-b", "test", {}),
        MessageFilter(),
    )

    await bus.shutdown()

    # All handles are broken — drain buffered messages first, then recv raises
    for h in [h1, h2]:
        # Drain buffered messages (node_online from peer)
        while True:
            try:
                m = h.try_recv()
                if m is None:
                    break
            except Exception:
                break  # channel already closed
        # Now recv should raise (buffer empty, channel closed)
        with pytest.raises(Exception):
            await h.recv()
        with pytest.raises(Exception):
            await h.send("msg", [], {})

    # graph() is still callable post-shutdown
    g = bus.graph()
    assert g is not None

    # message_count is frozen (no more messages)
    count_after = bus.message_count
    await asyncio.sleep(0.05)
    assert bus.message_count == count_after


# ── J13: concurrent disconnect — 无竞态无泄漏 ────────────────────────

async def test_concurrent_disconnect_no_race():
    """[并发] Multiple handles disconnect concurrently, graph ends empty, no race.

    Verifies that concurrent disconnects do not corrupt the nodes map.
    """
    bus = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=128)

    # Connect 20 nodes
    handles = []
    for i in range(20):
        h = await bus.connect(
            NodeInfo(f"concurrent-{i}", "test", {}),
            MessageFilter(),
        )
        handles.append(h)

    assert len(bus.graph().nodes) == 20

    # Disconnect all concurrently
    async def disconnect_one(h):
        await h.disconnect()

    await asyncio.gather(*[disconnect_one(h) for h in handles])

    # Graph must be empty — all entries removed
    g = bus.graph()
    assert len(g.nodes) == 0, (
        f"BUG: {len(g.nodes)} nodes remain after concurrent disconnect"
    )

    await bus.shutdown()
