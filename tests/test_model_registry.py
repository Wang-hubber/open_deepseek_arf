"""Test ModelRegistry — validation, resolution, partial override."""
import pytest
from arf.core.model_registry import ModelRegistry, ResolvedModelConfig


class TestModelRegistry:
    def test_resolve_single_model(self):
        registry = ModelRegistry([
            {"model": "gpt-5", "api_base": "https://api.openai.com",
             "api_key_env": "OPENAI_KEY"},
        ])
        cfg = registry.resolve("gpt-5")
        assert cfg.model == "gpt-5"
        assert cfg.api_base == "https://api.openai.com"
        assert cfg.api_key_env == "OPENAI_KEY"

    def test_resolve_list_full_reuse(self):
        registry = ModelRegistry([
            {"model": "pro", "api_base": "https://x.com", "api_key_env": "K"},
            {"model": "flash", "api_base": "https://x.com", "api_key_env": "K"},
        ])
        cfgs = registry.resolve_list([{"model": "pro"}, {"model": "flash"}])
        assert len(cfgs) == 2
        assert cfgs[0].model == "pro"
        assert cfgs[1].model == "flash"

    def test_resolve_list_partial_override(self):
        registry = ModelRegistry([
            {"model": "flash", "api_base": "https://x.com", "api_key_env": "K",
             "kwargs": {"temperature": 0.7}},
        ])
        cfgs = registry.resolve_list([
            {"model": "flash", "kwargs": {"temperature": 0.0}},
        ])
        assert cfgs[0].kwargs["temperature"] == 0.0
        assert cfgs[0].api_base == "https://x.com"  # inherited

    def test_resolve_missing_raises(self):
        registry = ModelRegistry([
            {"model": "a", "api_base": "x", "api_key_env": "K"},
        ])
        with pytest.raises(KeyError, match="nonexistent"):
            registry.resolve("nonexistent")

    def test_duplicate_last_wins(self):
        registry = ModelRegistry([
            {"model": "x", "api_base": "https://first.com", "api_key_env": "K1"},
            {"model": "x", "api_base": "https://second.com", "api_key_env": "K2"},
        ])
        cfg = registry.resolve("x")
        assert cfg.api_base == "https://second.com"  # last wins

    def test_has_and_list_names(self):
        registry = ModelRegistry([
            {"model": "a", "api_base": "x", "api_key_env": "K"},
            {"model": "b", "api_base": "x", "api_key_env": "K"},
        ])
        assert registry.has("a")
        assert not registry.has("c")
        assert set(registry.list_names()) == {"a", "b"}

    def test_validate_empty_api_key_env_raises(self):
        registry = ModelRegistry([
            {"model": "bad", "api_base": "x", "api_key_env": ""},
        ])
        with pytest.raises(ValueError, match="api_key_env must not be empty"):
            registry.validate()

    def test_ref_missing_model_key_raises(self):
        registry = ModelRegistry([
            {"model": "a", "api_base": "x", "api_key_env": "K"},
        ])
        with pytest.raises(ValueError, match="missing 'model'"):
            registry.resolve_list([{"not_model": "a"}])

    def test_defaults_applied(self):
        registry = ModelRegistry([
            {"model": "minimal", "api_base": "https://x.com", "api_key_env": "K"},
        ])
        cfg = registry.resolve("minimal")
        assert cfg.kwargs == {}  # default empty dict
