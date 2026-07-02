"""[E2E] py-arf model_adapter: live MiniMax + mock fallback.

[构造] [方法] [兼容]

Mirrors py-arf/tests/test_model_adapter_live.py for the MiniMax-specific
path. The Rust-side `MiniMaxProvider` bindings are in py-arf/src/lib.rs
(`MiniMaxProvider.chat(model_name, messages, tools, params)`).
"""
import pytest
from arf import MiniMaxConfig, MiniMaxProvider
from .conftest import stage, wait_for_or_die

LIVE_TIMEOUT = 30.0


def test_minimax_config_default():
    """[构造] MiniMaxConfig.default() has correct endpoint and model."""
    cfg = MiniMaxConfig.default()
    assert cfg.endpoint == "https://api.minimaxi.com/v1/chat/completions"
    assert "MiniMax-M3" in cfg.models


def test_minimax_config_from_env(monkeypatch):
    """[方法] MiniMaxConfig.from_env() reads MINIMAX_API_KEY."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.delenv("MINIMAX_TOKEN", raising=False)
    cfg = MiniMaxConfig.from_env()
    assert cfg.api_key == "test-key"


@pytest.mark.asyncio
async def test_minimax_provider_live_chat(minimax_key, monkeypatch):
    """[方法] MiniMaxProvider.chat() with live API returns model_response.

    Note: MiniMaxConfig.api_key is read-only in the current binding.
    We use `from_env()` which reads MINIMAX_API_KEY. The `minimax_key`
    fixture guarantees the env var is set; we set it via monkeypatch
    so this test is isolated.
    """
    from arf import ModelMessage, ModelParams
    monkeypatch.setenv("MINIMAX_API_KEY", minimax_key)
    cfg = MiniMaxConfig.from_env()
    provider = MiniMaxProvider(cfg)
    stage("MiniMaxProvider.chat(model_name='MiniMax-M3', 'What is 2+2?')")
    response = await wait_for_or_die(
        provider.chat(
            model_name="MiniMax-M3",
            messages=[ModelMessage(role="user", content="What is 2+2? Answer with just the number.")],
            tools=[],
            params=ModelParams(),
        ),
        timeout=LIVE_TIMEOUT,
        label="MiniMaxProvider.chat (model=MiniMax-M3, single-turn)",
    )
    stage(f"response.message.content = {response.message.content!r}")
    content = response.message.content
    assert "4" in content or "four" in content.lower(), (
        f"expected '4' in response, got content={content!r}"
    )


def test_minimax_config_from_env_errors_when_missing(monkeypatch):
    """[兼容] MiniMaxConfig.from_env() raises when no env var set."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_TOKEN", raising=False)
    with pytest.raises(Exception):
        MiniMaxConfig.from_env()
