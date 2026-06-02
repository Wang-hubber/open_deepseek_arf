"""SystemPrompt value object and DefaultSystemPromptProvider."""
from dataclasses import dataclass


@dataclass
class SystemPrompt:
    """Assembled system prompt with prefix/suffix separation.

    prefix — role + critical_rules (stable, target API cache)
    suffix — inventory + per-turn placeholders
    """
    prefix: str
    suffix: str

    @property
    def full_text(self) -> str:
        return self.prefix + self.suffix
