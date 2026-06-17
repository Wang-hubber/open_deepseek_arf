"""SystemPrompt value object."""
from dataclasses import dataclass


@dataclass
class SystemPrompt:
    """Assembled system prompt — just the prefix (role + critical_rules).

    Skills, tools, and memory are injected by the framework as
    separate system messages. No placeholders or suffix needed.
    """
    prefix: str
