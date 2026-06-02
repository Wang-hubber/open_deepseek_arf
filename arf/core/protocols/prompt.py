"""SystemPromptProvider Protocol — assembles system prompts from config."""
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from arf.agent.prompt import SystemPrompt


class SystemPromptProvider(Protocol):
    """Assemble system prompt from config and resolved resources."""

    def build(self) -> SystemPrompt:
        """Return assembled SystemPrompt with prefix/suffix populated."""
        ...
