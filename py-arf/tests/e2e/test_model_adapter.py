"""[E2E] py-arf model_adapter: live MiniMax + mock fallback.

[构造] [方法] [兼容]

Mirrors py-arf/tests/test_model_adapter_live.py for the MiniMax-specific
path. The Rust-side `MiniMaxProvider` bindings are in py-arf/src/lib.rs
(`MiniMaxProvider.chat(model_name, messages, tools, params)`).
"""
import pytest
from arf import MiniMaxConfig, MiniMaxProvider


def test_minimax_config_default():
    """[构造] MiniMaxConfig.default() has correct base URL and model."""
    cfg = MiniMaxConfig.default()
    assert cfg.base_url == "https://api.minimaxi.com/v1"
    assert "MiniMax-M3" in cfg.models


def test_minimax_config_from_env(monkeypatch):
    """[方法] MiniMaxConfig.from_env() reads MINIMAX_API_KEY."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.delenv("MINIMAX_TOKEN", raising=False)
    cfg = MiniMaxConfig.from_env()
    assert cfg.api_key == "test-key"


@pytest.mark.asyncio
async def test_minimax_provider_live_chat(minimax_key):
    """[方法] MiniMaxProvider.chat() with live API returns model_response.

    Calls MiniMax-M3 via MiniMax's OpenAI-compatible endpoint. The
    `model_name`, `messages`, `tools`, `params` signature is fixed by
    py-arf/src/lib.rs::PyMiniMaxProvider::chat. We pass empty tools and
    default params since we only need a text response.
    """
    from arf import ModelMessage, ModelParams
    cfg = MiniMaxConfig.default()
    cfg.api_key = minimax_key
    provider = MiniMaxProvider(cfg)
    response = await provider.chat(
        model_name="MiniMax-M3",
        messages=[ModelMessage(role="user", content="What is 2+2? Answer with just the number.")],
        tools=[],
        params=ModelParams(),
    )
    assert "4" in response.content or "four" in response.content.lower()


def test_minimax_config_from_env_errors_when_missing(monkeypatch):
    """[兼容] MiniMaxConfig.from_env() raises when no env var set."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_TOKEN", raising=False)
    with pytest.raises(Exception):
        MiniMaxConfig.from_env()