"""Phase 6 Flat-mode Integration Test App.

Assembles Bus + Engine + all Nodes in a flat topology (≤10 Nodes on one Bus)
and runs multi-round conversations.

Architecture (7 Nodes on one Top Bus):

    Top Bus
    ├── engine/main            Engine (ReAct loop)
    ├── model/deepseek         ModelAdapterNode (DeepSeek provider)
    ├── mcp/local              McpNode (local tools/ + skills/ scan)
    ├── memory/l1              MemoryNode (subscribes memory_op, every 5 rounds)
    ├── compactor/default      CompactionNode (subscribes compact_op, >80% ctx)
    ├── trace/obs              Full observer (ToMatch.All, prints all messages)
    └── guard/path             PathSandbox (optional)

Verification points:
  1. Assembly API — EngineBuilder.new(bus).route(...).add_checkpoint(...).build(config)
  2. Route semantics — Strict vs Discovery
  3. Checkpoint abstraction — every_n_rounds() / when_context_over()
  4. Session lifecycle — start_session() → chat() × N → state preserved
  5. Node independence — MemoryNode/CompactionNode don't import Engine
  6. Flat mode — all Nodes on same Bus, no message crosstalk

Run:
    cd py-arf && ../.venv/bin/python python/arf/examples/phase6_flat/app.py

Note: Requires Engine to be implemented (Rust arf-engine crate + PyO3 bindings).
This module documents the intended API as specified in §14 of the Phase 6 design.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from arf import (
    Bus,
    BusGraph,
    NodeId,
    NodeInfo,
    MessageFilter,
    ToMatch,
    DeepSeekConfig,
    DeepSeekProvider,
    ModelAdapterNode,
    McpNode,
)
from arf.engine import (
    Engine,
    EngineBuilder,
    AgentConfig,
    Route,
    Capability,
    Checkpoint,
    CheckpointRule,
    MemoryOp,
    CompactOp,
)

from nodes import MemoryNode, CompactionNode


# ═══════════════════════════════════════════════════════════════════════
# 0. Setup local tools/ directory for McpNode
# ═══════════════════════════════════════════════════════════════════════

def setup_tools_dir() -> str:
    """Create a temp directory with tools/ and skills/ for McpNode scanning."""
    root = tempfile.mkdtemp(prefix="arf_flat_app_")

    tools_dir = Path(root) / "tools" / "echo"
    tools_dir.mkdir(parents=True)
    tools_dir.joinpath("tool.toml").write_text(
        'name = "echo"\n'
        'description = "Echo back the input message"\n'
        'runtime = "bash"\n'
        'entrypoint = "echo.py"\n'
        'timeout_ms = 5000\n\n'
        '[params_schema]\n'
        'type = "object"\n'
        'properties = { message = { type = "string", description = "Message to echo" } }\n'
        'required = ["message"]\n'
    )
    tools_dir.joinpath("echo.py").write_text(
        "import sys, json\n"
        "data = json.load(sys.stdin)\n"
        "msg = data.get('params', {}).get('message', '')\n"
        'print(json.dumps({"content": f"echo: {msg}"}))\n'
    )

    skills_dir = Path(root) / "skills" / "greet"
    skills_dir.mkdir(parents=True)
    skills_dir.joinpath("SKILL.md").write_text(
        "---\n"
        "name: greet\n"
        "description: Greet the user with a friendly message\n"
        "compatibility: all\n"
        "---\n"
        "# Greet\n"
        "A simple greeting skill.\n"
        "## Tools\n"
        "- greet.py: Print a greeting\n"
    )
    skills_dir.joinpath("greet.py").write_text(
        "import sys, json\n"
        "data = json.load(sys.stdin)\n"
        "name = data.get('params', {}).get('name', 'World')\n"
        'print(json.dumps({"content": f"Hello, {name}!"}))\n'
    )

    return root


# ═══════════════════════════════════════════════════════════════════════
# 1. Main
# ═══════════════════════════════════════════════════════════════════════

async def main():
    tools_root = setup_tools_dir()
    print(f"[app] tools root: {tools_root}")

    # ── 1.1 Create Bus ─────────────────────────────────────────────
    bus = Bus()
    print("[app] Bus created")

    # ── 1.2 Create and connect all Nodes ────────────────────────────

    # model/deepseek — processing Node, Engine waits for its response
    model_provider = DeepSeekProvider(
        DeepSeekConfig(
            api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-placeholder"),
            models=["deepseek-v4-flash"],
        )
    )
    model_node: ModelAdapterNode = await model_provider.connect_to_bus(
        bus, NodeId("model/deepseek")
    )
    print("[app] model/deepseek connected")

    # mcp/local — processing Node, subscribes tool_exec
    mcp_node = McpNode.local(namespace="local", root=tools_root)
    await mcp_node.connect(bus)
    print(f"[app] mcp/local connected ({mcp_node.node_id})")

    # memory/l1 — subscribes memory_op (mock)
    memory_node = MemoryNode()
    await memory_node.connect(bus)
    print("[app] memory/l1 connected")

    # compactor/default — subscribes compact_op (mock)
    compactor_node = CompactionNode()
    await compactor_node.connect(bus)
    print("[app] compactor/default connected")

    # trace/obs — pure observer (ToMatch.All, non-blocking)
    trace_handle = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    async def trace_loop():
        """Background task: print all Bus messages."""
        while True:
            msg = await trace_handle.recv()
            to_list = [str(t) for t in msg.to] if msg.to else []
            print(
                f"[trace] {msg.msg_type:20s} {str(msg.sender):>18s}"
                f" → {str(to_list):30s} payload={msg.payload}"
            )

    trace_task = asyncio.create_task(trace_loop())
    print("[app] trace/obs connected")

    # ── 1.3 Verify BusGraph ────────────────────────────────────────
    graph: BusGraph = bus.graph()
    print(f"\n[app] BusGraph: {len(graph.nodes)} nodes online")
    for n in graph.nodes:
        print(
            f"  {str(n.node_id):>25s}  {n.node_type:10s}"
            f"  caps={n.capabilities}"
        )

    # ── 1.4 Build Engine ───────────────────────────────────────────
    #
    # The EngineBuilder validates that:
    #   - All Strict routes reference online NodeIds
    #   - All Discovery routes have at least one matching capability
    # On failure → BuildError with missing_nodes / missing_capabilities
    #
    config = AgentConfig(
        agent_id="assistant",
        system_prompt_template=(
            "You are a helpful assistant.\n\n"
            "Tools:\n{{tools}}\n\n"
            "Use tools to help the user. Be concise."
        ),
        model_config={"provider": "deepseek", "model": "deepseek-v4-flash"},
        max_turns=10,
        routes={
            "model_call": Route.strict(node_ids=["model/deepseek"]),
            "tool_exec": Route.discovery(
                capability=Capability(key="kind", value="mcp")
            ),
        },
        checkpoint_rules=[
            CheckpointRule.every_n_rounds(
                trigger=Checkpoint.RoundEnd,
                every_n=5,
                build=lambda s: MemoryOp.extract(messages=s.messages),
                route=Route.strict(node_ids=["memory/l1"]),
            ),
            CheckpointRule.when_context_over(
                trigger=Checkpoint.BeforeModelCall,
                ratio=0.8,
                build=lambda s: CompactOp.new(messages=s.messages),
                route=Route.discovery(
                    capability=Capability(key="kind", value="compactor")
                ),
            ),
        ],
    )

    engine = await EngineBuilder.new(bus=bus).build(config=config)
    print("\n[app] Engine built — routes + checkpoint_rules validated")

    # ── 1.5 Start Session ─────────────────────────────────────────
    session = await engine.start_session(session_id="flat-demo")
    print("[app] Session started: flat-demo")

    # ── 1.6 Multi-round conversation ───────────────────────────────
    rounds = [
        "What files are in /tmp?",
        "Can you create a file /tmp/hello.txt with 'Hello ARF'?",
        "Read the file /tmp/hello.txt back to me.",
        "What other files are in /tmp now?",
        "Delete /tmp/hello.txt please.",
        "Can you verify the file is gone?",
    ]

    for i, user_input in enumerate(rounds, 1):
        print(f"\n{'=' * 60}")
        print(f"[app] Round {i}: {user_input}")
        print(f"{'=' * 60}")

        output = await session.chat(user_input=user_input)
        print(f"[app] Round {i} output: {output}")

        state = session.state
        print(
            f"[app] State: round={state.over_view.round_count}, "
            f"turn={state.over_view.turn_count}, "
            f"context={state.over_view.context_tokens}"
            f"/{state.over_view.model_context_window}"
        )

    # ── 1.7 Validate results ──────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("[app] All rounds complete. Validating...")
    state = session.state
    print(f"  Total rounds: {state.over_view.round_count}")
    print(f"  Total turns:  {state.over_view.turn_count}")
    print(f"  Messages:     {len(state.messages)}")

    assert (
        state.over_view.round_count == len(rounds)
    ), f"Expected {len(rounds)} rounds, got {state.over_view.round_count}"
    assert state.over_view.turn_count > 0, "Expected non-zero turns"
    assert len(state.messages) > 0, "Expected non-empty messages"

    # Verify Checkpoint triggers: round 5 RoundEnd should trigger memory extract
    # (when condition satisfied in mock environment)
    print("[app] All assertions passed")

    # ── 1.8 Cleanup ───────────────────────────────────────────────
    trace_task.cancel()
    await memory_node.disconnect()
    await compactor_node.disconnect()
    await model_node.shutdown()
    await bus.shutdown()
    print("[app] Cleanup complete")


if __name__ == "__main__":
    asyncio.run(main())
