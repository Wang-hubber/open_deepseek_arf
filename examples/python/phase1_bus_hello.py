"""
Phase 1 Bus teaching example: multi-node chat room on a shared message bus.

Demonstrates the full Bus API lifecycle:
  - Bus creation with custom heartbeat/channel parameters
  - Node connection with different types and filters
  - Broadcast and directed messaging
  - Message receipt verification (id, sender, payload, is_broadcast, is_for)
  - Filter behavior (types whitelist, ToMatch modes)
  - Bus health graph inspection
  - Node disconnect and reconnect
  - Graceful shutdown

Run:
    cd py-arf && ../.venv/bin/python python/arf/examples/phase1_bus_hello.py
"""
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


async def main():
    # ── Create Bus ───────────────────────────────────────────────────
    bus = Bus(
        heartbeat_interval_ms=5000,   # 5s heartbeat tick
        heartbeat_timeout_ms=15000,   # 15s timeout → node considered offline
        channel_capacity=64,          # ring buffer for 64 messages
    )
    print("[Bus] created")

    # ── Connect nodes ────────────────────────────────────────────────
    # engine/main: orchestrator — sends and receives everything
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {"session": "demo", "role": "orchestrator"}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    print("[engine/main] connected")

    # mcp/fs: filesystem tool — only listens for "tool_call" messages
    fs_worker = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write"]}),
        MessageFilter(types=["tool_call"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    print("[mcp/fs] connected")

    # trace/obs: observer — sees everything (ToMatch.All)
    trace = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )
    print("[trace/obs] connected (ToMatch.All — sees everything)")

    # ── Inspect graph ────────────────────────────────────────────────
    g = bus.graph()
    print(f"\n[graph] {len(g.nodes)} nodes online, {g.message_count} messages, uptime {g.uptime_ms}ms")
    for n in g.nodes:
        print(f"  {n.node_id} ({n.node_type}) caps={n.capabilities}")

    # ── Broadcast message ────────────────────────────────────────────
    receipt = await engine.send("job", [], {"task": "compress", "file": "data.txt"})
    print(f"\n[engine] broadcast 'job' → receipt online={receipt.online_nodes} matching={receipt.matching_nodes}")

    # fs_worker receives the broadcast (type "job" passes its types=None effectively... wait,
    # fs_worker filter is types=["tool_call"], so it WON'T receive "job")
    # trace receives everything via ToMatch.All
    trace_msg = await trace.recv()
    print(f"[trace] recv: type={trace_msg.msg_type} sender={trace_msg.sender} "
          f"is_broadcast={trace_msg.is_broadcast()} payload={trace_msg.payload}")

    # engine also receives it (its own broadcast — broadcast rx includes self)
    # Drain node_online messages first, then the broadcast
    engine_msg = await engine.recv()
    # May be node_online from fs_worker or trace (depending on rx creation timing)
    print(f"[engine] recv: type={engine_msg.msg_type} sender={engine_msg.sender}")

    # ── Directed message ─────────────────────────────────────────────
    target = NodeId("mcp/fs")
    receipt2 = await engine.send(
        "tool_call", [target],
        {"tool": "read", "path": "/tmp/data.txt"},
    )
    print(f"\n[engine] directed 'tool_call' to mcp/fs → receipt online={receipt2.online_nodes} matching={receipt2.matching_nodes}")

    # Only mcp/fs receives this directed message
    fs_msg = await fs_worker.recv()
    # fs_worker may need to drain node_online first
    if fs_msg.msg_type != "tool_call":
        # drain lifecycle message
        print(f"[mcp/fs] drain lifecycle: type={fs_msg.msg_type}")
        fs_msg = await fs_worker.recv()
    print(f"[mcp/fs] recv: type={fs_msg.msg_type} sender={fs_msg.sender} "
          f"is_for(fs)={fs_msg.is_for(target)} payload={fs_msg.payload}")

    # ── Try receive (non-blocking) ───────────────────────────────────
    nothing = fs_worker.try_recv()
    print(f"\n[mcp/fs] try_recv → {nothing}  (no pending messages)")

    # ── Disconnect & reconnect ───────────────────────────────────────
    await fs_worker.disconnect()
    print(f"\n[mcp/fs] disconnected — graph now has {len(bus.graph().nodes)} nodes")

    # Reconnect with same NodeId
    fs_v2 = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write", "delete"]}),
        MessageFilter(types=["tool_call"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    print(f"[mcp/fs] reconnected with upgraded capabilities")

    # ── Bus stats ────────────────────────────────────────────────────
    g2 = bus.graph()
    print(f"\n[graph] {len(g2.nodes)} nodes, {bus.message_count} messages, uptime {bus.uptime_ms}ms")

    # ── Shutdown ─────────────────────────────────────────────────────
    await bus.shutdown()
    print(f"\n[Bus] shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
