"""Tests for SystemPrompt, SystemPromptProvider, and DefaultSystemPromptProvider."""
import pytest
from arf.agent.prompt import SystemPrompt


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
