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
        assert pcfg is not None
        assert pcfg["model"] == "flash"
        assert pcfg["api_base"] == "https://x.com"
        assert pcfg["kwargs"]["temperature"] == 0.7

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
