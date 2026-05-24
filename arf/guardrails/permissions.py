"""Tool permission checker — deny > ask > allow, config-driven.

Follows Claude Code's three-tier model:
- deny: hard-block, no override
- ask: requires user confirmation (channel-dependent)
- allow: auto-approve without prompt

Rules are evaluated in order: deny first, then ask, then allow.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("arf.guardrails.permissions")

# Built-in safety rules — always enforced
_BUILTIN_DENY_PATTERNS = [
    "rm -rf /", "sudo ", "chmod 777 /", "> /dev/sda",
    "curl.*|.*sh", "wget.*|.*sh",
]

# Safe tools that can auto-approve by default
_DEFAULT_ALLOW_TOOLS = [
    "file_reader", "web_search", "web_fetch", "memory_store",
    "resource_loader", "resource_registrar", "resource_scaffold",
]


class ToolPermissionChecker:
    """Evaluate deny/ask/allow rules for tool calls.

    Config format (agent.yaml):
        permissions:
          deny: ["python_exec", "file_deleter"]
          ask: ["file_writer"]
          allow: ["file_reader", "web_search"]
          deny_patterns: ["rm -rf", "sudo"]
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._deny_tools: set[str] = set(cfg.get("deny", []))
        self._ask_tools: set[str] = set(cfg.get("ask", []))
        self._allow_tools: set[str] = set(cfg.get("allow", _DEFAULT_ALLOW_TOOLS))
        self._deny_patterns: list[str] = list(cfg.get("deny_patterns", [])) + _BUILTIN_DENY_PATTERNS

    def check(self, tool_name: str, params: dict) -> str:
        """Return 'deny', 'ask', or 'allow' for a tool call.

        Checks in priority order: deny → ask → allow → default(ask).
        """
        import json

        params_str = json.dumps(params, ensure_ascii=False)

        # 1. Check deny patterns (params content)
        for pattern in self._deny_patterns:
            if pattern.lower() in params_str.lower():
                logger.warning("Tool '%s' denied by pattern '%s'", tool_name, pattern)
                return "deny"

        # 2. Check deny list
        if tool_name in self._deny_tools:
            logger.warning("Tool '%s' denied by config", tool_name)
            return "deny"

        # 3. Check ask list
        if tool_name in self._ask_tools:
            return "ask"

        # 4. Check allow list
        if tool_name in self._allow_tools:
            return "allow"

        # 5. Default: ask for safety
        return "ask"
