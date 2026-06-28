"""
[L] Live API integration tests — DeepSeekProvider direct + Bus full-link.

These tests require a valid DeepSeek API key. Set DEEPSEEK_API_KEY env var.
Without it, all tests are skipped.

Mirrors the Rust integration tests:
  - deepseek_live.rs: 7 OpenAI + 3 Anthropic format
  - bus_integration.rs: 8 Bus full-link tests

Run:
  DEEPSEEK_API_KEY=sk-xxx python -m pytest tests/test_model_adapter_live.py -v
"""

import asyncio
import os
import pytest
from arf import Bus, NodeId
from arf import (
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    ModelAdapterNode, ModelMessage,
    ModelParams, ToolDef,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def require_api_key():
    """Return API key or skip the test."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        pytest.skip("DEEPSEEK_API_KEY not set")
    return key


def empty_params(**overrides):
    """ModelParams with neutral defaults."""
    kwargs = {
        "temperature": None,
        "max_tokens": None,
        "thinking_enabled": False,
        "extra": None,
    }
    kwargs.update(overrides)
    return ModelParams(**kwargs)


async def engine_call(bus, target_node_id, messages, tools, params, stream=False):
    """Minimal EngineStub: connect to Bus, send model_call, collect response(s).

    Returns (response_payload_dict, list_of_chunk_dicts).
    """
    from arf import NodeInfo, MessageFilter, ToMatch

    info = NodeInfo(
        node_id=f"engine/stub-{id(messages)}",
        node_type="engine",
        capabilities={},
    )
    flt = MessageFilter(
        types=["model_response", "model_response_chunk"],
        to_match=ToMatch.BroadcastAndDirectedToMe,
    )
    handle = await bus.connect(info, flt)

    payload = {
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                **({"tool_call_id": m.tool_call_id} if m.tool_call_id else {}),
                **({"name": m.name} if m.name else {}),
                **({"extra": m.extra} if m.extra is not None else {}),
            }
            for m in messages
        ],
        "tools": [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ],
        "model_params": {
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
            "thinking_enabled": params.thinking_enabled,
            "extra": params.extra,
        },
        "stream": stream,
    }

    await handle.send("model_call", [target_node_id], payload)

    chunks = []
    while True:
        msg = await handle.recv()
        if msg.msg_type == "model_response_chunk":
            chunks.append(msg.payload)
        elif msg.msg_type == "model_response":
            await handle.disconnect()
            return msg.payload, chunks


# ═══════════════════════════════════════════════════════════════════════
# L1 — OpenAI format: DeepSeekProvider direct (7 tests)
# ═══════════════════════════════════════════════════════════════════════


def ds_provider():
    """Create DeepSeekProvider with real API key."""
    return DeepSeekProvider(
        DeepSeekConfig(
            api_key=require_api_key(),
            models=["deepseek-v4-flash", "deepseek-v4-pro"],
        )
    )


async def test_live_basic_chat():
    """[连通] 基础对话 — 非流式，finish_reason='stop'，有 content 和 usage."""
    p = ds_provider()
    msgs = [ModelMessage(role="user", content="Say hello in one word.")]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert response.finish_reason == "stop"
    assert response.message.content != ""
    assert response.usage is not None
    assert response.usage.total_tokens > 0
    print(f"[basic_chat] content: {response.message.content}")
    print(f"[basic_chat] usage: {response.usage}")


async def test_live_multi_round_chat():
    """[连通] 多轮对话 — 模型理解上下文，记住名字."""
    p = ds_provider()
    msgs = [
        ModelMessage(role="user", content="My name is Alice."),
        ModelMessage(role="assistant", content="Nice to meet you, Alice!"),
        ModelMessage(role="user", content="What is my name?"),
    ]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert "alice" in response.message.content.lower()
    print(f"[multi_round] content: {response.message.content}")


async def test_live_single_tool_call():
    """[工具] 单工具调用 — finish_reason='tool_calls'，工具名正确."""
    p = ds_provider()
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
    ]
    msgs = [ModelMessage(role="user", content="What is the weather in Beijing?")]
    response = await p.chat("deepseek-v4-flash", msgs, tools, empty_params())
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls is not None
    assert len(response.tool_calls) > 0
    assert response.tool_calls[0].name == "get_weather"
    print(
        f"[tool_call] name: {response.tool_calls[0].name}, "
        f"args: {response.tool_calls[0].arguments}"
    )


async def test_live_multi_tool_call_with_results():
    """[工具] 多工具调用 + 结果回传 — 最终 finish_reason='stop'."""
    p = ds_provider()
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        ),
        ToolDef(
            name="get_time",
            description="Get current time in a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        ),
    ]
    msgs = [
        ModelMessage(
            role="user",
            content="What is the weather AND time in Shanghai?",
        )
    ]
    response = await p.chat("deepseek-v4-flash", msgs, tools, empty_params())
    print(f"[multi_tool] finish_reason: {response.finish_reason}")

    if response.finish_reason == "tool_calls":
        tcs = response.tool_calls
        print(f"[multi_tool] tool_calls count: {len(tcs)}")

        import json as _json

        api_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _json.dumps(tc.arguments),
                },
            }
            for tc in tcs
        ]
        msgs2 = [
            ModelMessage(
                role="user",
                content="What is the weather AND time in Shanghai?",
            ),
            ModelMessage(
                role="assistant",
                content="",
                extra={"tool_calls": api_tool_calls},
            ),
        ]
        for tc in tcs:
            result_text = {
                "get_weather": "Sunny, 25°C",
                "get_time": "14:30 CST",
            }.get(tc.name, "done")
            msgs2.append(
                ModelMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
            )

        response2 = await p.chat("deepseek-v4-flash", msgs2, [], empty_params())
        assert response2.finish_reason == "stop"
        print(f"[multi_tool] final: {response2.message.content}")


async def test_live_thinking_enabled():
    """[思考] 开启思考模式 — extra 中有 reasoning_content."""
    p = ds_provider()
    params = empty_params(
        thinking_enabled=True,
        extra={"reasoning_effort": "high"},
    )
    msgs = [
        ModelMessage(
            role="user",
            content="Explain quantum computing in one paragraph.",
        )
    ]
    response = await p.chat("deepseek-v4-pro", msgs, [], params)
    print(f"[thinking] content: {response.message.content[:100]}...")
    print(f"[thinking] extra: {response.message.extra}")

    extra = response.message.extra
    has_reasoning = extra is not None and "reasoning_content" in extra
    print(f"[thinking] has reasoning_content: {has_reasoning}")


async def test_live_thinking_disabled():
    """[思考] 关闭思考模式 — finish_reason='stop'，正常回复."""
    p = ds_provider()
    params = empty_params(thinking_enabled=False)
    msgs = [ModelMessage(role="user", content="Say hello.")]
    response = await p.chat("deepseek-v4-flash", msgs, [], params)
    assert response.finish_reason == "stop"
    assert response.message.content != ""
    extra = response.message.extra
    has_reasoning = extra is not None and "reasoning_content" in extra
    print(f"[thinking_off] content: {response.message.content}")
    print(f"[thinking_off] has reasoning_content: {has_reasoning}")


async def test_live_streaming():
    """[流式] SSE 流式响应 — chunks 非空，最终 content 拼接正确."""
    p = ds_provider()
    msgs = [ModelMessage(role="user", content="Count from 1 to 5 slowly.")]
    chunks, response = await p.chat_stream(
        "deepseek-v4-flash", msgs, [], empty_params()
    )
    print(f"[streaming] chunk count: {len(chunks)}")
    for i, c in enumerate(chunks):
        if c.chunk_type == "text":
            print(f"[streaming] chunk[{i}]: {c.content}")
    assert len(chunks) > 0, "streaming should produce chunks"
    assert response.message.content != ""
    print(f"[streaming] full content: {response.message.content}")


# ═══════════════════════════════════════════════════════════════════════
# L2 — Anthropic format: AnthropicProvider → DeepSeek (3 tests)
# ═══════════════════════════════════════════════════════════════════════


def an_provider():
    """Create AnthropicProvider targeting DeepSeek Anthropic endpoint."""
    return AnthropicProvider(
        AnthropicConfig(
            api_key=require_api_key(),
            models=["deepseek-v4-flash"],
            base_url="https://api.deepseek.com",
            api_path="/anthropic/messages",
        )
    )


async def test_live_anthropic_basic_chat():
    """[连通] Anthropic 格式基础对话 — system 提取为顶层参数."""
    p = an_provider()
    msgs = [
        ModelMessage(role="system", content="Respond briefly."),
        ModelMessage(role="user", content="Say hello in one word."),
    ]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert response.message.content != ""
    print(f"[anthropic] content: {response.message.content}")
    print(f"[anthropic] finish_reason: {response.finish_reason}")
    print(f"[anthropic] usage: {response.usage}")


async def test_live_anthropic_multi_round_chat():
    """[连通] Anthropic 格式多轮对话 — 记住颜色."""
    p = an_provider()
    msgs = [
        ModelMessage(role="user", content="My favorite color is blue."),
        ModelMessage(role="assistant", content="Blue is a great choice!"),
        ModelMessage(role="user", content="What did I say my favorite color is?"),
    ]
    response = await p.chat("deepseek-v4-flash", msgs, [], empty_params())
    assert "blue" in response.message.content.lower()
    print(f"[anthropic_multi] content: {response.message.content}")


async def test_live_anthropic_tool_call():
    """[工具] Anthropic 格式工具调用 — stop_reason 映射正确."""
    p = an_provider()
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
    ]
    msgs = [ModelMessage(role="user", content="What is the weather in Tokyo?")]
    response = await p.chat("deepseek-v4-flash", msgs, tools, empty_params())
    print(f"[anthropic_tool] finish_reason: {response.finish_reason}")
    if response.tool_calls:
        print(
            f"[anthropic_tool] tool: {response.tool_calls[0].name} "
            f"args: {response.tool_calls[0].arguments}"
        )


# ═══════════════════════════════════════════════════════════════════════
# L3 — Bus 全链路集成测试 (8 tests)
# ═══════════════════════════════════════════════════════════════════════


class BusIntegrationFixture:
    """Setup/teardown for Bus full-link tests."""

    def __init__(self, model_name="deepseek-v4-flash"):
        self.model_name = model_name
        self.bus = None
        self.node = None
        self.node_id = None

    async def __aenter__(self):
        self.bus = Bus(
            heartbeat_interval_ms=10000,
            heartbeat_timeout_ms=30000,
            channel_capacity=64,
        )
        provider = DeepSeekProvider(
            DeepSeekConfig(
                api_key=require_api_key(),
                models=[self.model_name, "deepseek-v4-pro"],
            )
        )
        self.node_id = NodeId(f"model/{self.model_name}")
        self.node = await provider.connect_to_bus(self.bus, self.node_id)
        await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *args):
        if self.node:
            try:
                await self.node.shutdown()
            except Exception:
                pass
        if self.bus:
            try:
                await self.bus.shutdown()
            except Exception:
                pass


async def test_live_bus_basic_chat():
    """[连通] 基础对话经 Bus — non-streaming，chunks 空，finish_reason='stop'."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        msgs = [ModelMessage(role="user", content="Say hello in one word.")]
        response, chunks = await engine_call(
            fix.bus, fix.node_id, msgs, [], empty_params(), stream=False
        )
        assert len(chunks) == 0, "non-streaming should have no chunks"
        assert response["finish_reason"] == "stop"
        assert response["message"]["content"] != ""
        print(f"[bus_basic] content: {response['message']['content']}")


async def test_live_bus_multi_round_chat():
    """[连通] 多轮对话经 Bus — 上下文理解."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        msgs = [
            ModelMessage(role="user", content="My name is Alice."),
            ModelMessage(role="assistant", content="Nice to meet you, Alice!"),
            ModelMessage(role="user", content="What is my name?"),
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, [], empty_params(), stream=False
        )
        assert "alice" in response["message"]["content"].lower()
        print(f"[bus_multi] content: {response['message']['content']}")


async def test_live_bus_single_tool_call():
    """[工具] 单工具调用经 Bus — finish_reason='tool_calls'."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        tools = [
            ToolDef(
                name="get_weather",
                description="Get current weather for a city",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            )
        ]
        msgs = [ModelMessage(role="user", content="What is the weather in Beijing?")]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, tools, empty_params(), stream=False
        )
        assert response["finish_reason"] == "tool_calls"
        tc = response["tool_calls"]
        assert len(tc) > 0
        assert tc[0]["name"] == "get_weather"
        print(f"[bus_tool] name: {tc[0]['name']}, args: {tc[0]['arguments']}")


async def test_live_bus_multi_tool_call_with_results():
    """[工具] 多工具+结果回传经 Bus — 两轮闭环."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        tools = [
            ToolDef(
                name="get_weather",
                description="Get current weather",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
            ToolDef(
                name="get_time",
                description="Get current time in a city",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
        ]
        msgs = [
            ModelMessage(
                role="user",
                content="What is the weather AND time in Shanghai?",
            )
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, tools, empty_params(), stream=False
        )
        print(f"[bus_multi] finish_reason: {response['finish_reason']}")

        if response.get("finish_reason") == "tool_calls":
            tcs = response["tool_calls"]
            print(f"[bus_multi] tool_calls count: {len(tcs)}")

            import json as _json

            api_tool_calls = [
                {
                    "id": t["id"],
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "arguments": _json.dumps(t["arguments"]),
                    },
                }
                for t in tcs
            ]
            msgs2 = [
                ModelMessage(
                    role="user",
                    content="What is the weather AND time in Shanghai?",
                ),
                ModelMessage(
                    role="assistant",
                    content="",
                    extra={"tool_calls": api_tool_calls},
                ),
            ]
            for t in tcs:
                result_text = {
                    "get_weather": "Sunny, 25°C",
                    "get_time": "14:30 CST",
                }.get(t["name"], "done")
                msgs2.append(
                    ModelMessage(
                        role="tool",
                        content=result_text,
                        tool_call_id=t["id"],
                        name=t["name"],
                    )
                )

            response2, _ = await engine_call(
                fix.bus, fix.node_id, msgs2, [], empty_params(), stream=False
            )
            assert response2["finish_reason"] == "stop"
            print(f"[bus_multi] final: {response2['message']['content']}")


async def test_live_bus_thinking_enabled():
    """[思考] 开启思考经 Bus — reasoning_content 不丢失."""
    async with BusIntegrationFixture("deepseek-v4-pro") as fix:
        params = empty_params(
            thinking_enabled=True,
            extra={"reasoning_effort": "high"},
        )
        msgs = [
            ModelMessage(
                role="user",
                content="Explain quantum computing in one paragraph.",
            )
        ]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, [], params, stream=False
        )
        extra = response.get("message", {}).get("extra")
        has_reasoning = extra is not None and "reasoning_content" in extra
        print(f"[bus_thinking] has reasoning_content: {has_reasoning}")
        print(f"[bus_thinking] content: {response['message']['content'][:100]}")


async def test_live_bus_thinking_disabled():
    """[思考] 关闭思考经 Bus — 正常回复."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        params = empty_params(thinking_enabled=False)
        msgs = [ModelMessage(role="user", content="Say hello.")]
        response, _ = await engine_call(
            fix.bus, fix.node_id, msgs, [], params, stream=False
        )
        assert response["finish_reason"] == "stop"
        assert response["message"]["content"] != ""
        extra = response.get("message", {}).get("extra")
        has_reasoning = extra is not None and "reasoning_content" in extra
        print(f"[bus_thinking_off] content: {response['message']['content']}")
        print(f"[bus_thinking_off] has reasoning_content: {has_reasoning}")


async def test_live_bus_streaming():
    """[流式] SSE 流经 Bus — 每个 chunk 作为独立消息到达."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        msgs = [ModelMessage(role="user", content="Count from 1 to 5 slowly.")]
        response, chunks = await engine_call(
            fix.bus, fix.node_id, msgs, [], empty_params(), stream=True
        )
        print(f"[bus_streaming] chunk count: {len(chunks)}")
        for i, c in enumerate(chunks):
            if c.get("chunk_type") == "text":
                print(f"[bus_streaming] chunk[{i}]: {c.get('content')}")
        assert len(chunks) > 0, "streaming should produce chunks"
        assert response["message"]["content"] != ""
        print(f"[bus_streaming] full content: {response['message']['content']}")


async def test_live_bus_invalid_payload():
    """[错误] 无效 payload — 返回 error 响应，不 panic."""
    async with BusIntegrationFixture("deepseek-v4-flash") as fix:
        from arf import NodeInfo, MessageFilter, ToMatch

        info = NodeInfo(
            node_id="engine/stub-error",
            node_type="engine",
            capabilities={},
        )
        flt = MessageFilter(
            types=["model_response", "model_response_chunk"],
            to_match=ToMatch.BroadcastAndDirectedToMe,
        )
        handle = await fix.bus.connect(info, flt)

        await handle.send(
            "model_call", [fix.node_id], {"malformed": "not a valid payload"}
        )

        msg = await handle.recv()
        assert msg.msg_type == "model_response"
        error_text = msg.payload.get("error", "")
        assert "invalid" in error_text.lower()
        print(f"[bus_error] error: {error_text}")
        await handle.disconnect()
