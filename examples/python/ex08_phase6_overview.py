"""Full phase 1-6 stack: Bus + ModelAdapterPool + McpNode + Engine + Checkpoint + Route.

Demonstrates:
  phase 1   - Bus message bus
  phase 4   - ModelAdapterProvider pattern (MiniMaxProvider.connect_to_bus)
  phase 5   - McpNode.local for tool registration
  phase 6.10 - EngineBuilder + AgentConfig + EngineState
  phase 6.5  - Checkpoint + CheckpointRule + ActionMessage (RoundEnd snapshot)
  phase 6.2  - Route.strict + Route.discovery
  phase 6.22 - ModelAdapterPool (3 providers, max=2, queue=2)

Prerequisite: Set MINIMAX_API_KEY environment variable.
   The script will fail fast if the key is missing.

Run: MINIMAX_API_KEY=sk-... .venv/bin/python py-arf/python/arf/examples/ex08_phase6_overview.py
"""

import asyncio
import os
import time

from arf import (
    # phase 1
    Bus,
    NodeId,
    # phase 4 / 6.20
    MiniMaxConfig,
    MiniMaxProvider,
    # phase 5
    McpNode,
    # phase 6.10
    AgentConfig,
    EngineBuilder,
    EngineState,
    # phase 6.5
    Checkpoint,
    CheckpointRule,
    ActionMessage,
    # phase 6.2
    Route,
    # phase 6.22
    ModelAdapterPool,
    ModelAdapterResource,
    PoolConfig,
    Overflow,
)


def ensure_tool_manifest():
    """Create a minimal local MCP tool manifest for McpNode.local to scan."""
    tool_dir = "./tools/get_weather"
    os.makedirs(tool_dir, exist_ok=True)
    manifest = os.path.join(tool_dir, "manifest.yaml")
    if not os.path.exists(manifest):
        with open(manifest, "w") as f:
            f.write("name: get_weather\ndescription: Get current weather for a city\n")


async def main():
    if not os.environ.get("MINIMAX_API_KEY"):
        raise SystemExit("MINIMAX_API_KEY not set — this example requires a real provider.")

    ensure_tool_manifest()
    t0 = time.perf_counter()
    bus = Bus()

    # ── phase 6.22: 3 providers in a pool, max=2 + queue=2 (capacity 4) ──
    resources = [
        ModelAdapterResource.from_provider(
            provider=MiniMaxProvider(config=MiniMaxConfig.from_env()),
        )
        for _ in range(3)
    ]
    pool = ModelAdapterPool.with_resources(
        config=PoolConfig(max_size=2, overflow=Overflow.Queue(n=2)),
        resources=resources,
    )
    print(f"pool: max=2 queue=2, total={await pool.total_count()}")

    # ── phase 4: register 3 model nodes (capabilities.kind=model for discovery) ──
    for i in range(3):
        provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
        await provider.connect_to_bus(bus, NodeId(f"model/pool-{i}"))

    # ── phase 5: McpNode scans ./tools/ for tool manifests ──
    mcp = McpNode.local(namespace="tools", root="./tools")
    await mcp.connect(bus)
    print(f"mcp: {mcp}")

    # ── phase 6.10/6.5/6.2: Engine + Checkpoint + Route ──
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="ex08-full-demo",
            system_prompt_template="You are a helpful assistant. Use available tools when appropriate.",
            max_turns=8,
            routes={
                # Discovery: any node advertising provider="minimax"
                "model_call": Route.discovery(requirements=[("provider", "minimax")]),
                # Strict: only the local MCP node
                "tool_exec": Route.strict(ids=[NodeId("mcp/tools")]),
                # Strict: route the RoundEnd snapshot to a model node (it ignores)
                "snapshot_state": Route.strict(ids=[NodeId("model/pool-0")]),
            },
            checkpoint_rules=[
                CheckpointRule(
                    name="snapshot_round_end",
                    trigger=Checkpoint.RoundEnd,
                    actions=[
                        ActionMessage(
                            msg_type="snapshot_state",
                            payload={"reason": "round_end"},
                        ),
                    ],
                ),
            ],
        ),
    )

    # ── multi-turn: state accumulates across engine.run() ──
    state = EngineState()
    out1 = await engine.run(state=state, user_input="用一句话介绍北京。")
    print(f"out1={out1!r}")

    out2 = await engine.run(state=state, user_input="上海呢?")
    print(f"out2={out2!r}")
    print(f"round_count={state.round_count}, turn_count={state.turn_count}")
    print(f"messages={len(state.messages)}")
    print(f"elapsed={(time.perf_counter() - t0) * 1000:.0f}ms")

    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())