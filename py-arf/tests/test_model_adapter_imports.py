"""
[M] ModelAdapter type construction — all exported types importable and basic construction correct.

Test angles: [覆盖] [构造] [trait] [边界]
"""
import pytest
from arf import (
    # Configs
    AnthropicConfig, DeepSeekConfig, OpenAIConfig,
    # Providers
    AnthropicProvider, DeepSeekProvider, OpenAIProvider,
    # Node
    ModelAdapterNode,
    # Data types
    ModelMessage, ModelParams, ToolDef,
    ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, Usage,
)


# ═══════════════════════════════════════════════════════════════════════
# M1 — Imports
# ═══════════════════════════════════════════════════════════════════════


def test_all_model_adapter_types_importable():
    """[覆盖] All ModelAdapter types importable."""
    for cls in [
        AnthropicConfig, AnthropicProvider,
        DeepSeekConfig, DeepSeekProvider,
        OpenAIConfig, OpenAIProvider,
        ModelAdapterNode, ModelMessage,
        ModelParams, ToolDef,
        ModelResponseChunk, ModelResponsePayload,
        ToolCall, ToolCallDelta, Usage,
    ]:
        assert cls is not None


# ═══════════════════════════════════════════════════════════════════════
# M2 — Config 构造
# ═══════════════════════════════════════════════════════════════════════


def test_deepseek_config_defaults():
    """[构造] DeepSeekConfig with required fields only, verify defaults."""
    c = DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    assert c.api_key == "sk-test"
    assert c.models == ["deepseek-v4-flash"]
    assert c.base_url == "https://api.deepseek.com"
    assert c.timeout_secs == 320
    assert c.max_retries == 3
    assert "DeepSeekConfig" in repr(c)


def test_deepseek_config_full_custom():
    """[构造] DeepSeekConfig all fields explicitly set."""
    c = DeepSeekConfig(
        api_key="sk-custom",
        models=["deepseek-v4-flash", "deepseek-v4-pro"],
        base_url="https://custom.deepseek.com",
        timeout_secs=120,
        max_retries=5,
    )
    assert c.models == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert c.base_url == "https://custom.deepseek.com"
    assert c.timeout_secs == 120
    assert c.max_retries == 5


def test_openai_config_defaults():
    """[构造] OpenAIConfig defaults: base_url='https://api.openai.com'."""
    c = OpenAIConfig(api_key="sk-test", models=["gpt-4o"])
    assert c.base_url == "https://api.openai.com"
    assert c.timeout_secs == 320
    assert c.max_retries == 3


def test_anthropic_config_defaults():
    """[构造] AnthropicConfig defaults: base_url + api_path."""
    c = AnthropicConfig(api_key="sk-test", models=["claude-sonnet-4-6"])
    assert c.base_url == "https://api.anthropic.com"
    assert c.api_path == "/v1/messages"
    assert c.timeout_secs == 320
    assert c.max_retries == 3


def test_anthropic_config_custom_api_path():
    """[构造] AnthropicConfig with DeepSeek-compatible api_path."""
    c = AnthropicConfig(
        api_key="sk-test",
        models=["deepseek-v4-flash"],
        base_url="https://api.deepseek.com",
        api_path="/anthropic",
    )
    assert c.base_url == "https://api.deepseek.com"
    assert c.api_path == "/anthropic"


def test_config_three_providers_independent():
    """[构造] All three configs created independently — no cross-contamination."""
    ds = DeepSeekConfig(api_key="sk-ds", models=["m1"])
    oa = OpenAIConfig(api_key="sk-oa", models=["m2"])
    an = AnthropicConfig(api_key="sk-an", models=["m3"])
    assert ds.api_key == "sk-ds"
    assert oa.api_key == "sk-oa"
    assert an.api_key == "sk-an"


# ═══════════════════════════════════════════════════════════════════════
# M3 — Provider 构造
# ═══════════════════════════════════════════════════════════════════════


def test_deepseek_provider_name_and_models():
    """[方法] DeepSeekProvider name and supported_models."""
    c = DeepSeekConfig(api_key="sk-test", models=["deepseek-v4-flash"])
    p = DeepSeekProvider(c)
    assert p.name == "deepseek"
    assert p.supported_models == ["deepseek-v4-flash"]


def test_openai_provider_name_and_models():
    """[方法] OpenAIProvider name='openai'."""
    c = OpenAIConfig(api_key="sk-test", models=["gpt-4o", "gpt-4-turbo"])
    p = OpenAIProvider(c)
    assert p.name == "openai"
    assert p.supported_models == ["gpt-4o", "gpt-4-turbo"]


def test_anthropic_provider_name_and_models():
    """[方法] AnthropicProvider name='anthropic'."""
    c = AnthropicConfig(api_key="sk-test", models=["claude-sonnet-4-6"])
    p = AnthropicProvider(c)
    assert p.name == "anthropic"
    assert p.supported_models == ["claude-sonnet-4-6"]


def test_provider_three_independent():
    """[构造] Three provider instances independent."""
    ds = DeepSeekProvider(DeepSeekConfig(api_key="sk-ds", models=["m1"]))
    oa = OpenAIProvider(OpenAIConfig(api_key="sk-oa", models=["m2"]))
    an = AnthropicProvider(AnthropicConfig(api_key="sk-an", models=["m3"]))
    assert ds.name == "deepseek"
    assert oa.name == "openai"
    assert an.name == "anthropic"


# ═══════════════════════════════════════════════════════════════════════
# M4 — ModelMessage 构造 (arf-core type)
# ═══════════════════════════════════════════════════════════════════════


def test_model_message_basic():
    """[构造] ModelMessage role+content — minimal construction."""
    m = ModelMessage(role="user", content="Hello")
    assert m.role == "user"
    assert m.content == "Hello"
    assert m.tool_call_id is None
    assert m.name is None
    assert m.extra is None
    assert "ModelMessage" in repr(m)


def test_model_message_all_roles():
    """[覆盖] ModelMessage supports user/assistant/system/tool roles."""
    for role in ["user", "assistant", "system", "tool"]:
        m = ModelMessage(role=role, content="test")
        assert m.role == role


def test_model_message_full():
    """[构造] ModelMessage all fields including tool_call_id, name, extra."""
    m = ModelMessage(
        role="tool",
        content="file content here",
        tool_call_id="call_abc123",
        name="read_file",
        extra={"result_type": "text"},
    )
    assert m.role == "tool"
    assert m.content == "file content here"
    assert m.tool_call_id == "call_abc123"
    assert m.name == "read_file"
    assert m.extra == {"result_type": "text"}


def test_model_message_extra_nested_json():
    """[边界] ModelMessage.extra handles nested JSON dict/list."""
    m = ModelMessage(
        role="assistant",
        content="",
        extra={"reasoning_content": "Let me think...", "citations": [1, 2, 3]},
    )
    assert m.extra["reasoning_content"] == "Let me think..."
    assert m.extra["citations"] == [1, 2, 3]


def test_model_message_extra_none_default():
    """[构造] ModelMessage extra=None by default — getter returns None."""
    m = ModelMessage(role="user", content="hi")
    assert m.extra is None


def test_model_message_unicode():
    """[边界] ModelMessage with Unicode content (Chinese, emoji)."""
    m = ModelMessage(role="user", content="你好世界 🚀")
    assert m.content == "你好世界 🚀"


def test_model_message_empty_content():
    """[边界] ModelMessage with empty content (valid for tool results)."""
    m = ModelMessage(role="tool", content="", tool_call_id="call_1")
    assert m.content == ""
    assert m.tool_call_id == "call_1"


def test_model_message_repr_truncation():
    """[trait] ModelMessage __repr__ truncates long content."""
    m = ModelMessage(role="user", content="a" * 100)
    r = repr(m)
    assert "..." in r
    assert len(r) < 120


# ═══════════════════════════════════════════════════════════════════════
# M5 — ModelParams 构造
# ═══════════════════════════════════════════════════════════════════════


def test_model_params_defaults():
    """[构造] ModelParams() all defaults — None temperature, thinking_enabled=False."""
    p = ModelParams()
    assert p.temperature is None
    assert p.max_tokens is None
    assert p.thinking_enabled is False
    assert p.extra is None


def test_model_params_full():
    """[构造] ModelParams all fields explicitly set."""
    p = ModelParams(temperature=0.7, max_tokens=4096, thinking_enabled=True)
    assert p.temperature == pytest.approx(0.7)
    assert p.max_tokens == 4096
    assert p.thinking_enabled is True


def test_model_params_with_extra():
    """[构造] ModelParams with provider-specific extra params."""
    p = ModelParams(
        temperature=0.5,
        thinking_enabled=True,
        extra={"reasoning_effort": "high", "top_p": 0.9},
    )
    assert p.extra == {"reasoning_effort": "high", "top_p": 0.9}


def test_model_params_boolean_thinking():
    """[方法] ModelParams.thinking_enabled is Python bool — not string."""
    p = ModelParams(thinking_enabled=True)
    assert p.thinking_enabled is True
    assert isinstance(p.thinking_enabled, bool)

    p2 = ModelParams(thinking_enabled=False)
    assert p2.thinking_enabled is False


def test_model_params_repr():
    """[trait] ModelParams __repr__ includes non-None fields."""
    p = ModelParams(temperature=0.0, max_tokens=100, thinking_enabled=True)
    r = repr(p)
    assert "true" in r  # Rust repr uses lowercase


def test_model_params_temperature_boundary():
    """[边界] ModelParams temperature 0.0 and 2.0 (boundary values)."""
    for t in [0.0, 1.0, 2.0]:
        p = ModelParams(temperature=t)
        assert p.temperature == pytest.approx(t)


# ═══════════════════════════════════════════════════════════════════════
# M6 — ToolDef 构造
# ═══════════════════════════════════════════════════════════════════════


def test_tool_def_basic():
    """[构造] ToolDef with simple parameters."""
    t = ToolDef(
        name="search",
        description="Search the web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    assert t.name == "search"
    assert t.description == "Search the web"
    assert t.parameters == {"type": "object", "properties": {"query": {"type": "string"}}}


def test_tool_def_empty_parameters():
    """[边界] ToolDef with empty dict parameters."""
    t = ToolDef(name="noop", description="Does nothing", parameters={})
    assert t.parameters == {}


def test_tool_def_nested_parameters():
    """[构造] ToolDef with deeply nested JSON Schema parameters."""
    t = ToolDef(
        name="complex",
        description="Complex tool",
        parameters={
            "type": "object",
            "properties": {
                "nested": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"key": {"type": "string"}}},
                }
            },
            "required": ["nested"],
        },
    )
    assert "nested" in t.parameters["properties"]
    assert t.parameters["required"] == ["nested"]


def test_tool_def_unicode():
    """[边界] ToolDef with Unicode name and description."""
    t = ToolDef(name="搜索", description="搜索互联网内容", parameters={})
    assert t.name == "搜索"
    assert t.description == "搜索互联网内容"


def test_tool_def_repr():
    """[trait] ToolDef __repr__ includes name and description."""
    t = ToolDef(name="read", description="Read file", parameters={})
    r = repr(t)
    assert "read" in r
    assert "Read file" in r


# ═══════════════════════════════════════════════════════════════════════
# M7 — 只读类型验证
# ═══════════════════════════════════════════════════════════════════════


def test_tool_call_no_public_constructor():
    """[边界] ToolCall has no public constructor (read-only from provider)."""
    with pytest.raises(TypeError):
        ToolCall()  # type: ignore


def test_tool_call_delta_no_public_constructor():
    """[边界] ToolCallDelta has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        ToolCallDelta()  # type: ignore


def test_usage_no_public_constructor():
    """[边界] Usage has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        Usage()  # type: ignore


def test_model_response_chunk_no_public_constructor():
    """[边界] ModelResponseChunk has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        ModelResponseChunk()  # type: ignore


def test_model_response_payload_no_public_constructor():
    """[边界] ModelResponsePayload has no public constructor (read-only)."""
    with pytest.raises(TypeError):
        ModelResponsePayload()  # type: ignore


def test_model_adapter_node_no_public_constructor():
    """[边界] ModelAdapterNode has no public constructor (created by provider.connect_to_bus())."""
    with pytest.raises(TypeError):
        ModelAdapterNode()  # type: ignore
