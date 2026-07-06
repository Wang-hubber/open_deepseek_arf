"""Task 18d — provider validation tests.

These tests do NOT require a running server — they test the pure
`_resolve_provider()` function in isolation via monkeypatched env vars.
"""

import importlib

import pytest


@pytest.fixture
def server_module(monkeypatch):
    """Import server module with monkeypatched env."""
    # server.py is at examples/multi_agent_team/server.py
    # pytest runs from examples/multi_agent_team/ as rootdir, so server is importable
    import server
    # Reload to pick up env changes (in case test order changes env)
    importlib.reload(server)
    return server


# [构造] ARF_PROVIDER 未设 → RuntimeError
def test_resolve_provider_missing_env(monkeypatch, server_module):
    monkeypatch.delenv("ARF_PROVIDER", raising=False)
    with pytest.raises(RuntimeError, match="ARF_PROVIDER env var not set"):
        server_module._resolve_provider()


# [构造] ARF_PROVIDER 设为未知值 → RuntimeError 列出有效选项
def test_resolve_provider_unknown_value(monkeypatch, server_module):
    monkeypatch.setenv("ARF_PROVIDER", "openai_gpt5")
    with pytest.raises(RuntimeError, match="not recognized"):
        server_module._resolve_provider()


# [构造] ARF_PROVIDER=deepseek 但 DEEPSEEK_API_KEY 缺失 → RuntimeError
def test_resolve_provider_missing_api_key(monkeypatch, server_module):
    monkeypatch.setenv("ARF_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        server_module._resolve_provider()


# [构造] ARF_PROVIDER=aliyun_bailian 但 DASHSCOPE_API_KEY 缺失
def test_resolve_provider_aliyun_missing_key(monkeypatch, server_module):
    monkeypatch.setenv("ARF_PROVIDER", "aliyun_bailian")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        server_module._resolve_provider()


# [构造] ARF_PROVIDER=minimax 但 MINIMAX_API_KEY 缺失
def test_resolve_provider_minimax_missing_key(monkeypatch, server_module):
    monkeypatch.setenv("ARF_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        server_module._resolve_provider()


# [成功] 3 个 provider 都能正确解析
@pytest.mark.parametrize(
    "name,env_var,model",
    [
        ("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
        ("aliyun_bailian", "DASHSCOPE_API_KEY", "qwen3-max"),
        ("minimax", "MINIMAX_API_KEY", "MiniMax-Text-01"),
    ],
)
def test_resolve_provider_success(monkeypatch, server_module, name, env_var, model):
    monkeypatch.setenv("ARF_PROVIDER", name)
    monkeypatch.setenv(env_var, "fake-key-123")
    p = server_module._resolve_provider()
    assert p["name"] == name
    assert p["env_var"] == env_var
    assert p["default_model"] == model
    assert p["api_key"] == "fake-key-123"