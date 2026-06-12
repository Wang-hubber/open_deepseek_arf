"""Integration test: model config → registry → resolution → validation."""
import yaml
import pytest


class TestModelIntegration:
    def test_agent_config_parses_model_defs(self):
        """AgentConfig should parse model_defs and create a working registry."""
        from arf.agent.config import AgentConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: test-pro
    api_base: https://test.api.com
    api_key_env: TEST_KEY
  - model: test-flash
    api_base: https://test.api.com
    api_key_env: TEST_KEY
agent_models:
  - model: test-pro
  - model: test-flash
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        registry = cfg.get_model_registry()
        assert registry is not None
        assert registry.has("test-pro")
        assert registry.has("test-flash")
        assert not registry.has("nonexistent")

    def test_agent_config_resolves_model_refs(self):
        """get_agent_model_configs should resolve refs to full configs."""
        from arf.agent.config import AgentConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: pro
    api_base: https://x.com
    api_key_env: K
agent_models:
  - model: pro
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        cfgs = cfg.get_agent_model_configs()
        assert cfgs is not None
        assert len(cfgs) == 1
        assert cfgs[0].model == "pro"

    def test_agent_config_resolves_plugin_model(self):
        """get_plugin_model_config should resolve plugin model ref."""
        from arf.agent.config import AgentConfig
        from arf.core.model_registry import ResolvedModelConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: flash
    api_base: https://x.com
    api_key_env: K
    kwargs:
      temperature: 0.7
plugins_config:
  compaction:
    model: flash
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        pcfg = cfg.get_plugin_model_config("compaction")
        assert isinstance(pcfg, ResolvedModelConfig)
        assert pcfg.model == "flash"
        assert pcfg.api_base == "https://x.com"
        assert pcfg.kwargs["temperature"] == 0.7

    def test_agent_config_returns_none_for_old_format(self):
        """When model_defs is empty, get_model_registry returns None."""
        from arf.agent.config import AgentConfig

        config_yaml = """
name: test_agent
models:
  - type: quick
    model: some-model
    api_base: https://x.com
    api_key_env: K
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        assert cfg.get_model_registry() is None
        assert cfg.get_agent_model_configs() is None
        assert cfg.get_plugin_model_config("compaction") is None

    def test_plugin_model_config_returns_resolved_model_config(self):
        """get_plugin_model_config should return ResolvedModelConfig, not dict."""
        from arf.agent.config import AgentConfig
        from arf.core.model_registry import ResolvedModelConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: flash
    api_base: https://x.com
    api_key_env: K
    kwargs:
      temperature: 0.7
plugins_config:
  compaction:
    model: flash
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        pcfg = cfg.get_plugin_model_config("compaction")
        assert isinstance(pcfg, ResolvedModelConfig)
        assert pcfg.model == "flash"
        assert pcfg.api_base == "https://x.com"
        assert pcfg.kwargs["temperature"] == 0.7

    def test_plugin_model_config_inline_mode(self):
        """When model name not in model_defs, treat plugin config as inline definition."""
        from arf.agent.config import AgentConfig
        from arf.core.model_registry import ResolvedModelConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: pro
    api_base: https://x.com
    api_key_env: K
plugins_config:
  eval:
    model: gpt-4
    api_base: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    kwargs:
      temperature: 0.0
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        pcfg = cfg.get_plugin_model_config("eval")
        assert isinstance(pcfg, ResolvedModelConfig)
        assert pcfg.model == "gpt-4"
        assert pcfg.api_base == "https://api.openai.com/v1"
        assert pcfg.api_key_env == "OPENAI_API_KEY"
        assert pcfg.kwargs["temperature"] == 0.0

    def test_plugin_model_config_inline_defaults(self):
        """Inline mode uses framework defaults for api_base/api_key_env when omitted."""
        from arf.agent.config import AgentConfig
        from arf.core.model_registry import ResolvedModelConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: pro
    api_base: https://x.com
    api_key_env: K
plugins_config:
  eval:
    model: local-model
    kwargs:
      temperature: 0.0
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        pcfg = cfg.get_plugin_model_config("eval")
        assert pcfg.api_base == "https://api.deepseek.com"
        assert pcfg.api_key_env == "DEEPSEEK_API_KEY"

    def test_plugin_model_config_ref_with_overrides(self):
        """Reference mode: kwargs merge (definition base + plugin overrides)."""
        from arf.agent.config import AgentConfig
        from arf.core.model_registry import ResolvedModelConfig

        config_yaml = """
name: test_agent
model_defs:
  - model: flash
    api_base: https://x.com
    api_key_env: K
    kwargs:
      temperature: 0.7
      reasoning_effort: high
plugins_config:
  eval:
    model: flash
    kwargs:
      temperature: 0.0
      max_tokens: 2000
"""
        cfg = AgentConfig(**yaml.safe_load(config_yaml))
        pcfg = cfg.get_plugin_model_config("eval")
        assert pcfg.kwargs["temperature"] == 0.0       # overridden
        assert pcfg.kwargs["max_tokens"] == 2000        # added
        assert pcfg.kwargs["reasoning_effort"] == "high"  # preserved
