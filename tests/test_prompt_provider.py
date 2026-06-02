"""Tests for SystemPrompt, SystemPromptProvider, and DefaultSystemPromptProvider."""
import pytest
from arf.agent.prompt import SystemPrompt
from arf.agent.config import AgentConfig, SystemPromptConfig, PrefixConfig
from arf.agent.default_prompt_provider import DefaultSystemPromptProvider


class TestSystemPrompt:
    def test_full_text_concatenates_prefix_and_suffix(self):
        sp = SystemPrompt(prefix="Hello. ", suffix="World.")
        assert sp.full_text == "Hello. World."

    def test_empty_prefix(self):
        sp = SystemPrompt(prefix="", suffix="World.")
        assert sp.full_text == "World."

    def test_empty_suffix(self):
        sp = SystemPrompt(prefix="Hello.", suffix="")
        assert sp.full_text == "Hello."

    def test_immutable_after_construction(self):
        sp = SystemPrompt(prefix="A", suffix="B")
        sp.prefix = "X"  # dataclass allows reassignment by default
        assert sp.prefix == "X"


class TestDefaultSystemPromptProvider:
    def test_build_returns_system_prompt(self):
        config = AgentConfig(
            name="test_agent",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(
                    role="You are a test assistant.",
                    critical_rules="Rule 1: Be helpful.",
                ),
                suffix="$INVENTORY",
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert isinstance(sp, SystemPrompt)
        assert "You are a test assistant." in sp.prefix
        assert "Rule 1: Be helpful." in sp.prefix
        # prefix: role comes before critical_rules
        assert sp.prefix.index("test assistant") < sp.prefix.index("Rule 1")

    def test_suffix_passed_through_as_is(self):
        """Provider no longer builds inventory — suffix is pass-through."""
        config = AgentConfig(
            name="test",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(role="Role.", critical_rules="Rules."),
                suffix="$INVENTORY",
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert sp.suffix == "$INVENTORY"

    def test_placeholders_left_untouched(self):
        """$INVENTORY, $MEMORY etc. pass through — filled by MCP/engine."""
        config = AgentConfig(
            name="test",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(role="Role.", critical_rules="Rules."),
                suffix="""$INVENTORY

## Memory
$MEMORY

## Workspace
$WORKSPACE""",
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert "$MEMORY" in sp.suffix, "Per-turn placeholder should remain"
        assert "$WORKSPACE" in sp.suffix, "Per-turn placeholder should remain"
        assert "$INVENTORY" in sp.suffix, "INVENTORY passed through, filled by MCP"

    def test_empty_prefix_critical_rules(self):
        config = AgentConfig(
            name="test",
            system_prompt=SystemPromptConfig(
                prefix=PrefixConfig(role="Role only.", critical_rules=""),
                suffix="Suffix only.",
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
                suffix="Suffix.",
            ),
        )
        provider = DefaultSystemPromptProvider(config=config)
        sp = provider.build()
        assert sp.prefix == "Only rules."
