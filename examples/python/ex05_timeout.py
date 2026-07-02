"""Timeout protection with asyncio.wait_for.

Demonstrates:
  - Engine.run() does not self-timeout - caller must wrap in wait_for
  - Mock model that connects (so build() validates) but never replies,
    simulating a hung model
  - asyncio.TimeoutError fires after the configured timeout

Run: .venv/bin/python py-arf/python/arf/examples/ex05_timeout.py
"""

import asyncio
import time
from arf import (
    Bus,
    NodeInfo,
    MessageFilter,
    AgentConfig,
    EngineBuilder,
    EngineState,
)


async def attach_silent_model(bus):
    """Mock model that connects (so build() validates) but never replies."""
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/silent",
            node_type="model",
            capabilities={"provider": "silent", "models": ["silent-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def silent_loop():
        # Drain messages but never reply - simulate a hung model.
        while True:
            try:
                await mock.recv()
            except Exception:
                break

    return mock, asyncio.create_task(silent_loop())


async def main():
    t0 = time.perf_counter()
    bus = Bus()
    mock, silent_task = await attach_silent_model(bus)

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            provider="silent",  # matches capabilities.provider
            model="silent-v1",
        ),
    )
    state = EngineState()

    try:
        out = await asyncio.wait_for(
            engine.run(state=state, user_input="hi"),
            timeout=1.0,
        )
        print(f"unexpectedly got: {out!r}")
    except asyncio.TimeoutError:
        print("engine.run() hung > 1s - model not responding")
    finally:
        silent_task.cancel()
        await bus.shutdown()
        print(f"elapsed={(time.perf_counter() - t0) * 1000:.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())