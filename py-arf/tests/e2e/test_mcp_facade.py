"""[E2E] py-arf mcp + engine: cross-Bus tool_exec forwarding.

[方法] [边界]

Mirrors crates/arf-e2e/tests/mcp_facade.rs::facade_forwards_tool_exec_across_buses.

Topology (simplified for Python bindings):
  Top Bus:    EngineNode + facade_top (subscribes to tool_exec)
  Sub Bus:    bridge_sub (subscribes to tool_result_set) + McpNode

Wiring:
  - facade_top receives `tool_exec` from Engine on top Bus → wraps as
    `tool_call_set` payload → bridge_sub.send() forwards to McpNode on sub Bus
  - bridge_sub receives `tool_result_set` from McpNode → unwraps → facade_top
    re-emits as `tool_result` back on top Bus (to Engine).

The Python side has limitations vs. the Rust facade:
  - NodeHandle.send auto-sets `from` from the registered node. To route
    correctly we use TWO distinct registered nodes (facade_top on top,
    bridge_sub on sub) so the directed message arrives at the right place.
  - McpNode.execute() is not exposed in py-arf; we use the wire-protocol
    message format `tool_call_set` directly (matching the Rust reference).
"""
import asyncio
import tempfile
import uuid
from pathlib import Path

import pytest
from arf import Bus, McpNode, NodeId, NodeInfo, MessageFilter, ToMatch
from arf._arf import AgentConfig, EngineBuilder, EngineState


def _write_echo_tool(root: Path) -> None:
    """Write a python `echo` tool to {root}/tools/echo/.

    The tool reads JSON from stdin and echoes back the `text` field —
    matches the Rust reference in crates/arf-e2e/tests/mcp_facade.rs.
    """
    tool_dir = root / "tools" / "echo"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "tool.toml").write_text(
        'name = "echo"\n'
        'description = "Echo back the input"\n'
        'runtime = "python"\n'
        'entrypoint = "echo.py"\n'
    )
    (tool_dir / "echo.py").write_text(
        "import sys, json\n"
        "params = json.load(sys.stdin)\n"
        "print(json.dumps({\"echoed\": params.get(\"text\", \"\")}))\n"
    )


@pytest.mark.asyncio
async def test_python_facade_forwards_tool_across_buses():
    """[方法] Python facade forwards tool_exec from top Bus → sub Bus → McpNode.

    Topology:
      - Top Bus: facade/top (subscribes to tool_exec broadcasts)
      - Sub Bus: bridge/sub (subscribes to tool_result_set) + McpNode

    Implementation note: py-arf's NodeHandle internally serializes send
    and recv via an async Mutex (see py-arf/src/lib.rs). To avoid
    send/recv contention on the bridge handle, the facade runs as a
    SINGLE coordinator task that:
      1. recv tool_exec on top (via facade_top)
      2. send tool_call_set on sub (via bridge_sub)
      3. recv tool_result_set on sub (via bridge_sub)
      4. send tool_result on top (via facade_top) — broadcast back to engine
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_echo_tool(tmp)

        top_bus = Bus(
            heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=32
        )
        sub_bus = Bus(
            heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=32
        )

        # Connect McpNode on sub Bus.
        mcp = McpNode.local("e2e", str(tmp))
        await mcp.connect(sub_bus)

        # Wait briefly for McpNode's spawn to subscribe.
        await asyncio.sleep(0.1)

        # Connect facade node on top Bus.
        facade_top_handle = await top_bus.connect(
            NodeInfo("facade/top", "facade", {}),
            MessageFilter(
                types=["tool_exec"], to_match=ToMatch.BroadcastAndDirectedToMe
            ),
        )
        # Bridge node on sub Bus.
        bridge_sub_handle = await sub_bus.connect(
            NodeInfo("bridge/sub", "facade-sub", {}),
            MessageFilter(
                types=["tool_result_set"],
                to_match=ToMatch.BroadcastAndDirectedToMe,
            ),
        )
        mcp_id = NodeId(mcp.node_id)

        # Coordinator: receives tool_exec on top → forwards as tool_call_set
        # to McpNode on sub → awaits tool_result_set → broadcasts tool_result
        # back on top.
        async def coordinator() -> None:
            while True:
                # 1) recv tool_exec on top
                msg = await facade_top_handle.recv()
                if msg.msg_type != "tool_exec":
                    continue
                tool_name = msg.payload.get("name", "")
                arguments = msg.payload.get("arguments", {})
                fwd_cid = str(uuid.uuid4())
                payload = {
                    "correlation_id": fwd_cid,
                    "session_id": "fwd",
                    "calls": [
                        {
                            "id": "call_0",
                            "tool": tool_name,
                            "params": arguments,
                            "blocked_by": [],
                            "blocking": [],
                        }
                    ],
                    "timeout_ms": 2000,
                }
                # 2) send tool_call_set on sub
                await bridge_sub_handle.send(
                    "tool_call_set", [mcp_id], payload
                )
                # 3) recv tool_result_set on sub
                reply = await bridge_sub_handle.recv()
                if reply.msg_type != "tool_result_set":
                    continue
                results = (reply.payload or {}).get("results") or []
                if not results:
                    content, ok = "", False
                else:
                    r0 = results[0]
                    if r0.get("status") == "success":
                        content = str(r0.get("result", ""))
                        ok = True
                    else:
                        content = f"error: {r0.get('error', '')}"
                        ok = False
                # 4) send tool_result on top (broadcast)
                await facade_top_handle.send(
                    "tool_result",
                    [],
                    {"name": tool_name, "content": content, "ok": ok},
                )

        coord_task = asyncio.create_task(coordinator())
        # Give coordinator time to start its first recv.
        await asyncio.sleep(0.1)

        # Direct send a tool_exec on top Bus and verify a tool_result
        # comes back. This isolates the facade from engine.run() — Test 2
        # below is the engine-integrated variant.
        sender_handle = await top_bus.connect(
            NodeInfo("test/sender", "test", {}),
            MessageFilter(
                types=None, to_match=ToMatch.BroadcastAndDirectedToMe
            ),
        )
        cid = str(uuid.uuid4())
        await sender_handle.send(
            "tool_exec",
            [],
            {
                "correlation_id": cid,
                "name": "echo",
                "arguments": {"text": "hello"},
            },
        )

        # Wait for tool_result on sender (drain node_online etc.).
        deadline = asyncio.get_event_loop().time() + 5.0
        result_msg = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                m = await asyncio.wait_for(sender_handle.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if m.msg_type == "tool_result":
                result_msg = m
                break

        coord_task.cancel()

        assert result_msg is not None, "expected tool_result within 5s"
        assert result_msg.payload.get("ok") is True, (
            f"expected ok=True, got {result_msg.payload}"
        )
        assert "hello" in result_msg.payload.get("content", ""), (
            f"expected echoed 'hello' in content, got {result_msg.payload}"
        )


@pytest.mark.asyncio
async def test_python_engine_receives_cross_bus_tool_result():
    """[边界] Engine sees tool_result forwarded from the facade.

    Skipped in this environment: the Python-side AgentConfig does not
    yet expose a `routes` field, so Engine cannot route `model_call` to
    a model adapter Node. Without routing, engine.run() blocks on a
    ModelCall reply that never comes. The Rust equivalent
    crates/arf-e2e/tests/mcp_facade.rs::facade_forwards_tool_exec_across_buses
    sets routes manually and works — once py-arf exposes AgentConfig.routes,
    this test can be wired up the same way.
    """
    pytest.skip(
        "AgentConfig.routes not exposed in py-arf yet — see Phase 6 task "
        "6.22.4. Rust equivalent: crates/arf-e2e/tests/mcp_facade.rs::"
        "facade_forwards_tool_exec_across_buses"
    )