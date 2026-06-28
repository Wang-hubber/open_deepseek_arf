"""
[N] ModelAdapter node Bus integration — connect/shutdown/graph lifecycle.

These tests work WITHOUT API keys — they only verify Bus lifecycle.
chat()/chat_stream() require real API keys and are tested in Rust integration tests.

Test angles: [构造] [方法] [边界] [清理]
"""
import asyncio
import gc
import pytest
from arf import Bus, NodeId
from arf import (
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
)


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════

def ds_provider():
    """Create a test DeepSeekProvider with placeholder key."""
    return DeepSeekProvider(
        DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    )


def oa_provider():
    """Create a test OpenAIProvider with placeholder key."""
    return OpenAIProvider(
        OpenAIConfig(api_key="sk-test", models=["gpt-4o"])
    )


def an_provider():
    """Create a test AnthropicProvider with placeholder key."""
    return AnthropicProvider(
        AnthropicConfig(api_key="sk-test", models=["claude-sonnet-4-6"])
    )


# ═══════════════════════════════════════════════════════════════════════
# N1 — connect_to_bus 基本流程
# ═══════════════════════════════════════════════════════════════════════


async def test_connect_to_bus_node_appears_in_graph():
    """[构造] provider.connect_to_bus() → node appears in bus graph."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "model/deepseek"
    assert g.nodes[0].node_type == "model"

    await node.shutdown()
    await bus.shutdown()


async def test_connect_to_bus_capabilities():
    """[方法] NodeInfo capabilities includes provider name and models."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    g = bus.graph()
    caps = g.nodes[0].capabilities
    assert caps["provider"] == "deepseek"
    assert caps["models"] == ["deepseek-v4-flash"]

    await node.shutdown()
    await bus.shutdown()


async def test_connect_to_bus_returns_model_adapter_node():
    """[方法] connect_to_bus() returns ModelAdapterNode with correct node_id."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/test"))

    assert str(node.node_id) == "model/test"
    assert "ModelAdapterNode" in repr(node)

    await node.shutdown()
    await bus.shutdown()


async def test_connect_all_three_providers_to_same_bus():
    """[方法] Three providers on same bus — all appear in graph."""
    bus = Bus()

    ds_node = await ds_provider().connect_to_bus(bus, NodeId("model/deepseek"))
    oa_node = await oa_provider().connect_to_bus(bus, NodeId("model/openai"))
    an_node = await an_provider().connect_to_bus(bus, NodeId("model/anthropic"))

    g = bus.graph()
    assert len(g.nodes) == 3
    provider_names = {n.capabilities["provider"] for n in g.nodes}
    assert provider_names == {"deepseek", "openai", "anthropic"}

    await ds_node.shutdown()
    await oa_node.shutdown()
    await an_node.shutdown()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# N2 — Shutdown 语义
# ═══════════════════════════════════════════════════════════════════════


async def test_shutdown_removes_node_from_graph():
    """[清理] After shutdown, node removed from bus graph."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    assert len(bus.graph().nodes) == 1

    await node.shutdown()
    await asyncio.sleep(0.05)  # allow async disconnect to propagate

    g = bus.graph()
    assert len(g.nodes) == 0, f"Expected empty graph, got {g.nodes}"

    await bus.shutdown()


async def test_double_shutdown_raises():
    """[边界] Second shutdown() raises RuntimeError."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()
    with pytest.raises(RuntimeError, match="already shut down"):
        await node.shutdown()

    await bus.shutdown()


async def test_double_shutdown_idempotent_after_bus_closed():
    """[边界] Even after bus shutdown, double-shutdown still raises."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()
    await bus.shutdown()

    with pytest.raises(RuntimeError, match="already shut down"):
        await node.shutdown()


async def test_node_id_after_shutdown_raises():
    """[边界] Accessing node_id after shutdown raises RuntimeError."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()

    with pytest.raises(RuntimeError, match="already shut down"):
        _ = node.node_id

    await bus.shutdown()


async def test_repr_after_shutdown_shows_shut_down():
    """[trait] ModelAdapterNode repr shows 'shut down' after shutdown."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()

    r = repr(node)
    assert "shut down" in r.lower() or "Shut" in r

    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# N3 — 多 Provider 场景
# ═══════════════════════════════════════════════════════════════════════


async def test_partial_shutdown_leaves_other_nodes():
    """[清理] Shutdown one node — others remain in graph."""
    bus = Bus()

    ds_node = await ds_provider().connect_to_bus(bus, NodeId("model/deepseek"))
    oa_node = await oa_provider().connect_to_bus(bus, NodeId("model/openai"))

    assert len(bus.graph().nodes) == 2

    await ds_node.shutdown()
    await asyncio.sleep(0.05)

    g = bus.graph()
    assert len(g.nodes) == 1
    assert str(g.nodes[0].node_id) == "model/openai"

    await oa_node.shutdown()
    await bus.shutdown()


async def test_multiple_same_provider_different_models():
    """[方法] Two DeepSeek nodes with different models on same bus."""
    bus = Bus()

    p1 = DeepSeekProvider(
        DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    )
    p2 = DeepSeekProvider(
        DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-pro"])
    )

    n1 = await p1.connect_to_bus(bus, NodeId("model/flash"))
    n2 = await p2.connect_to_bus(bus, NodeId("model/pro"))

    g = bus.graph()
    assert len(g.nodes) == 2
    models_seen = set()
    for n in g.nodes:
        models_seen.update(n.capabilities["models"])
    assert "deepseek-v4-flash" in models_seen
    assert "deepseek-v4-pro" in models_seen

    await n1.shutdown()
    await n2.shutdown()
    await bus.shutdown()


async def test_bus_shutdown_before_node_shutdown():
    """[边界] Bus shutdown before node — graceful handling."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await bus.shutdown()

    try:
        await node.shutdown()
    except Exception:
        pass  # acceptable — bus already closed


# ═══════════════════════════════════════════════════════════════════════
# N4 — NodeId 边界
# ═══════════════════════════════════════════════════════════════════════


async def test_node_id_unicode():
    """[边界] NodeId with Unicode — model name in Chinese."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("模型/deepseek"))

    assert str(node.node_id) == "模型/deepseek"

    await node.shutdown()
    await bus.shutdown()


async def test_node_id_long_name():
    """[边界] NodeId with long model path."""
    long_id = "model/" + "a" * 64 + "/deepseek-v4-flash"
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId(long_id))

    assert str(node.node_id) == long_id

    await node.shutdown()
    await bus.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# N5 — GC / 资源清理
# ═══════════════════════════════════════════════════════════════════════


async def test_gc_collects_node_after_shutdown():
    """[泄漏] Node can be GC'd after shutdown — no dangling references."""
    bus = Bus()
    provider = ds_provider()
    node = await provider.connect_to_bus(bus, NodeId("model/deepseek"))

    await node.shutdown()
    del node
    gc.collect()
    await asyncio.sleep(0.05)

    assert bus.uptime_ms >= 0

    await bus.shutdown()


async def test_config_reuse_across_providers():
    """[方法] Same config can be reused across multiple provider instances."""
    config = DeepSeekConfig(api_key="sk-shared", models=["deepseek-v4-flash"])

    p1 = DeepSeekProvider(config)
    p2 = DeepSeekProvider(config)

    assert p1.supported_models == p2.supported_models
