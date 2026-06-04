"""PermissionRegistry — unified deny/ask/allow list matching.

Replaces the duplicated logic in:
  - arf/guardrails/permissions.py (ToolPermissionChecker)
  - arf/promotion/strategies.py (AskStrategy)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("arf.session.permissions")

# Built-in safety rules — always merged into deny_patterns.
_BUILTIN_DENY_PATTERNS: list[str] = []

# Safe tools that auto-approve by default (when no explicit lists configured)
_DEFAULT_ALLOW_TOOLS = [
    "file_reader", "web_search", "web_fetch", "memory_store",
    "resource_loader", "resource_registrar", "resource_scaffold",
]


@dataclass
class PermissionResult:
    """Result of a permission evaluation."""
    action: Literal["allow", "deny", "ask"]
    reason: str = ""


@dataclass
class PermissionLists:
    """Permission lists for a single agent, used by PermissionRegistry."""
    deny: set[str] = field(default_factory=set)
    ask: set[str] = field(default_factory=set)
    allow: set[str] = field(default_factory=set)
    deny_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: dict | None) -> "PermissionLists":
        """Build from PermissionsConfig-style dict."""
        cfg = config or {}
        deny_patterns = list(cfg.get("deny_patterns", [])) + _BUILTIN_DENY_PATTERNS
        allow = set(cfg.get("allow", []))
        if not allow and not cfg.get("deny") and not cfg.get("ask"):
            allow = set(_DEFAULT_ALLOW_TOOLS)
        return cls(
            deny=set(cfg.get("deny", [])),
            ask=set(cfg.get("ask", [])),
            allow=allow,
            deny_patterns=deny_patterns,
        )


class PermissionRegistry:
    """Unified permission list evaluator.

    Priority order: deny_patterns → deny list → ask list → allow list → default ask.
    Accepts PermissionLists (not config dicts) so callers can hot-swap lists
    per agent without rebuilding.
    """

    def evaluate(self, tool_name: str, params: dict, lists: PermissionLists) -> PermissionResult:
        """Evaluate tool permission against the given lists.

        Returns PermissionResult with action='allow'/'deny'/'ask'.
        """
        params_str = json.dumps(params, ensure_ascii=False) if params else ""

        # 1. deny_patterns — params content safety
        for pattern in lists.deny_patterns:
            if re.search(pattern, params_str, re.IGNORECASE):
                logger.info("Tool '%s' blocked (pattern)", tool_name)
                logger.debug("Deny pattern: %s", pattern)
                return PermissionResult(action="deny", reason="blocked by security policy")

        # 2. deny list
        if tool_name in lists.deny:
            logger.info("Tool '%s' blocked (deny_list)", tool_name)
            return PermissionResult(action="deny", reason="blocked by security policy")

        # 3. ask list
        if tool_name in lists.ask:
            return PermissionResult(action="ask", reason="")

        # 4. allow list
        if tool_name in lists.allow:
            return PermissionResult(action="allow", reason="")

        # 5. default: ask
        return PermissionResult(action="ask", reason="")
