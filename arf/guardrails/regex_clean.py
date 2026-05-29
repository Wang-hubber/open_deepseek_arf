"""RegexOutputGuard — sanitize model output with regex patterns."""
import re
from arf.core.results import GuardResult


class RegexOutputGuard:
    PATTERNS = [
        (r'sk-[-a-zA-Z0-9]{20,}', '[REDACTED_API_KEY]'),
        (r'\b1[3-9]\d{9}\b', '[REDACTED_PHONE]'),
    ]

    async def check(self, message: str, context: dict) -> GuardResult:
        modified = message
        changed = False
        for pat, repl in self.PATTERNS:
            if re.search(pat, modified):
                modified = re.sub(pat, repl, modified)
                changed = True
        return GuardResult(allowed=True, modified_message=modified if changed else None)
