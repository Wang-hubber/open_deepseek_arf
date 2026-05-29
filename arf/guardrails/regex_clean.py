"""RegexOutputGuard — sanitize model output with configurable regex patterns."""
import re
from arf.core.results import GuardResult

_BUILTIN_PATTERNS: list[tuple[str, str]] = [
    (r'sk-[-a-zA-Z0-9]{20,}', '[REDACTED_API_KEY]'),
    (r'\b1[3-9]\d{9}\b', '[REDACTED_PHONE]'),
]


class RegexOutputGuard:
    """Regex-based output sanitizer.

    Patterns are configurable via constructor or agent.yaml:
      advanced.guardrails.output_patterns:
        - pattern: "sk-[-a-zA-Z0-9]{20,}"
          replacement: "[REDACTED_API_KEY]"
        - pattern: "\\b1[3-9]\\d{9}\\b"
          replacement: "[REDACTED_PHONE]"

    If no patterns provided, uses framework built-in defaults.
    Pass an empty list to disable all output filtering.
    """

    def __init__(self, patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns: list[tuple[str, str]] = (
            patterns if patterns is not None else _BUILTIN_PATTERNS
        )

    @property
    def patterns(self) -> list[tuple[str, str]]:
        return list(self._patterns)

    async def check(self, message: str, context: dict) -> GuardResult:
        modified = message
        changed = False
        for pat, repl in self._patterns:
            if re.search(pat, modified):
                modified = re.sub(pat, repl, modified)
                changed = True
        return GuardResult(allowed=True, modified_message=modified if changed else None)
