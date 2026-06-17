"""Tests for SystemPrompt and DefaultSystemPromptProvider."""
import pytest
from arf.agent.prompt import SystemPrompt
from arf.agent.config import AgentConfig, SystemPromptConfig, PrefixConfig
from arf.agent.default_prompt_provider import DefaultSystemPromptProvider


class TestSystemPrompt:
    def test_prefix_only(self):
        sp = SystemPrompt(prefix="You are a helpful assistant.")
        assert sp.prefix == "You are a helpful assistant."

    def test_empty_prefix(self):
        sp = SystemPrompt(prefix="")
        assert sp.prefix == ""


class TestDefaultSystemPromptProvider:
    def test_build_returns_system_prompt(self):
        config = AgentConfig(
            name="test_agent",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(
                    role="You are a test assistant.",
                    critical_rules="Rule 1: Be helpful.",
                ),
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert isinstance(sp, SystemPrompt)
        assert "You are a test assistant." in sp.prefix
        assert "Rule 1: Be helpful." in sp.prefix
        assert sp.prefix.index("test assistant") < sp.prefix.index("Rule 1")

    def test_empty_prefix_critical_rules(self):
        config = AgentConfig(
            name="test",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(role="Role only.", critical_rules=""),
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert sp.prefix == "Role only."

    def test_empty_role(self):
        config = AgentConfig(
            name="test",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(role="", critical_rules="Only rules."),
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert sp.prefix == "Only rules."

    def test_both_empty(self):
        config = AgentConfig(
            name="test",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(role="", critical_rules=""),
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert sp.prefix == ""
