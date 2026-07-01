"""Phase 4 ModelAdapter — Python API usage examples.

Demonstrates:
  1. Config + Provider construction
  2. Direct chat() and chat_stream() calls
  3. Bus integration via connect_to_bus()
"""

import asyncio
from arf import Bus, NodeId
from arf import (
    DeepSeekConfig,
    DeepSeekProvider,
    ModelAdapterNode,
    ModelMessage,
    ModelParams,
    ToolDef,
)


async def example_direct_chat():
    """Synchronous (non-streaming) chat — for testing/debugging."""
    print("=== Direct Chat ===")

    config = DeepSeekConfig(
        api_key="sk-placeholder",
        models=["deepseek-v4-flash"],
    )
    provider = DeepSeekProvider(config)
    print(f"Provider: {provider.name}, models: {provider.supported_models}")

    messages = [
        ModelMessage(role="system", content="You are a helpful assistant."),
        ModelMessage(role="user", content="What is 2+2?"),
    ]
    params = ModelParams(temperature=0.7, max_tokens=256, thinking_enabled=False)
    tools = [
        ToolDef(
            name="calculator",
            description="Evaluate a math expression",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression to evaluate",
                    }
                },
                "required": ["expression"],
            },
        )
    ]

    # NOTE: requires real API key to work
    try:
        response = await provider.chat("deepseek-v4-flash", messages, tools, params)
        print(f"Response: {response.message.content}")
        print(
            f"Finish: {response.finish_reason}, "
            f"Tokens: {response.usage.total_tokens if response.usage else 'N/A'}"
        )
        if response.tool_calls:
            for tc in response.tool_calls:
                print(f"  Tool call: {tc.name}({tc.arguments})")
    except Exception as e:
        print(f"Chat failed (expected with placeholder key): {e}")


async def example_streaming_chat():
    """Streaming chat — demonstrates chunk iteration."""
    print("\n=== Streaming Chat ===")

    config = DeepSeekConfig(
        api_key="sk-placeholder",
        models=["deepseek-v4-flash"],
    )
    provider = DeepSeekProvider(config)

    messages = [ModelMessage(role="user", content="Count from 1 to 5.")]
    params = ModelParams(temperature=0.7, max_tokens=128, thinking_enabled=False)

    try:
        chunks, response = await provider.chat_stream(
            "deepseek-v4-flash", messages, [], params
        )
        for chunk in chunks:
            if chunk.chunk_type == "text":
                print(chunk.content, end="", flush=True)
            elif chunk.chunk_type == "reasoning":
                print(f"[Reasoning: {chunk.reasoning[:50]}...]")
        print(f"\nUsage: {response.usage}")
    except Exception as e:
        print(f"Streaming failed (expected with placeholder key): {e}")


async def example_bus_integration():
    """Connect a provider to the Bus as a ModelAdapterNode."""
    print("\n=== Bus Integration ===")

    bus = Bus()
    print(f"Bus created, uptime: {bus.uptime_ms}ms")

    config = DeepSeekConfig(
        api_key="sk-placeholder",
        models=["deepseek-v4-flash"],
    )
    provider = DeepSeekProvider(config)

    # Connect as a Bus node — the node now listens for model_call messages
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))
    print(f"Node connected: {node}")

    # Verify node appears in bus graph
    graph = bus.graph()
    for n in graph.nodes:
        if n.node_id == NodeId("model/deepseek"):
            print(f"  Found in graph: {n}")
            print(f"  Capabilities: {n.capabilities}")
            break

    # Shutdown
    await node.shutdown()
    print("Node shut down")

    await bus.shutdown()
    print("Bus shut down")

    # Verify safety: double-shutdown raises
    try:
        await node.shutdown()
    except RuntimeError as e:
        print(f"Double-shutdown correctly rejected: {e}")


async def main():
    await example_bus_integration()
    # The following require real API keys:
    # await example_direct_chat()
    # await example_streaming_chat()


if __name__ == "__main__":
    asyncio.run(main())
