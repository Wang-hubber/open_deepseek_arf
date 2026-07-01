"""EngineState JSON serialization and restoration.

Demonstrates:
  - state.messages is JSON-serializable
  - state.round_count / turn_count / context_tokens also serializable
  - Restoration pattern: re-construct EngineState from snapshot

Run: .venv/bin/python py-arf/python/arf/examples/ex06_state_serialize.py
"""

import asyncio
import json
import tempfile
from arf import (
    Bus,
    NodeInfo,
    MessageFilter,
    AgentConfig,
    EngineBuilder,
    EngineState,
)


async def attach_mock_model(bus):
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/mock",
            node_type="model",
            capabilities={"provider": "mock", "models": ["mock-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def responder():
        while True:
            try:
                msg = await mock.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    },
                )
            except Exception:
                break

    return mock, asyncio.create_task(responder())


async def main():
    bus = Bus()
    mock, task = await attach_mock_model(bus)
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="ex06-state"),
    )

    # 1. Run, then serialize
    state = EngineState()
    await engine.run(state=state, user_input="hi")
    snapshot = {
        "round_count": state.round_count,
        "turn_count": state.turn_count,
        "context_tokens": state.context_tokens,
        "messages": state.messages,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
        path = f.name
    print(f"snapshot saved: round={snapshot['round_count']}, messages={len(snapshot['messages'])}")
    print(f"path={path}")

    # 2. Restore from snapshot (new EngineState + same messages)
    with open(path) as f:
        loaded = json.load(f)
    restored = EngineState()
    print(f"restored: {len(loaded['messages'])} messages, round_count was {loaded['round_count']}")

    task.cancel()
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())