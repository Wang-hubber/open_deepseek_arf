"""ContentGuard — dangerous behavior detection + sensitive info filtering."""

from __future__ import annotations

import re
import logging

from arf.core.results import GuardResult

logger = logging.getLogger("arf.guardrails.content_guard")

# ── Framework built-in defaults ──
_BUILTIN_DANGEROUS: list[dict] = [
    {"name": "pipe_to_shell", "pattern": r"(curl|wget).*\|.*(sh|bash|python)",
     "description": "Prevent piping downloaded content to shell"},
    {"name": "eval_exec", "pattern": r"\beval\s*\(", "description": "Prevent eval() execution"},
    {"name": "rm_rf_root", "pattern": r"rm\s+-rf\s+/", "description": "Prevent recursive root deletion"},
]

_BUILTIN_SENSITIVE: list[dict] = [
    {"name": "openai_key", "pattern": r"sk-[-a-zA-Z0-9]{20,}", "replacement": "[REDACTED_API_KEY]"},
    {"name": "phone_cn", "pattern": r"\b1[3-9]\d{9}\b", "replacement": "[REDACTED_PHONE]"},
]


class ContentGuard:
    """Unified content safety engine.

    Two rule types:
    - dangerous_patterns: pre-execution → block if matched
    - sensitive_patterns: post-execution + pre-output → redact if matched

    App config appends to built-in defaults. App can override a built-in rule
    by using the same ``name``.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._enabled = cfg.get("enabled", True)

        app_dangerous = cfg.get("dangerous_patterns", [])
        self._dangerous = self._merge_rules(_BUILTIN_DANGEROUS, app_dangerous)

        app_sensitive = cfg.get("sensitive_patterns", [])
        self._sensitive = self._merge_rules(_BUILTIN_SENSITIVE, app_sensitive)

    # ── Public API ──

    def check_dangerous(self, content: str) -> GuardResult:
        """Check content for dangerous patterns. Block if found."""
        if not self._enabled:
            return GuardResult(allowed=True)
        for rule in self._dangerous:
            if re.search(rule["pattern"], content, re.IGNORECASE):
                logger.warning("ContentGuard blocked: %s", rule["name"])
                reason = f"matched dangerous pattern '{rule['name']}'"
                if rule.get("description"):
                    reason += f": {rule['description']}"
                return GuardResult(allowed=False, reason=reason)
        return GuardResult(allowed=True)

    def redact_sensitive(self, content: str) -> tuple[str, bool]:
        """Redact sensitive info from content.

        Returns (cleaned_content, was_redacted).
        Returns (original, False) if nothing found or disabled.
        """
        if not self._enabled:
            return (content, False)
        modified = content
        changed = False
        for rule in self._sensitive:
            pattern = rule["pattern"]
            replacement = rule.get("replacement", "[REDACTED]")
            if re.search(pattern, modified, re.IGNORECASE):
                modified = re.sub(pattern, replacement, modified)
                changed = True
        return (modified, changed)

    # ── Internal ──

    @staticmethod
    def _merge_rules(builtins: list[dict], app_rules: list[dict]) -> list[dict]:
        """Merge app rules over builtins by name. App rules append if new name."""
        merged: dict[str, dict] = {}
        for r in builtins:
            merged[r["name"]] = dict(r)
        for r in app_rules:
            merged[r["name"]] = dict(r)
        return list(merged.values())
