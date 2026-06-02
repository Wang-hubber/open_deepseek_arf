"""DefaultSystemPromptProvider — assembles SystemPrompt from config and resources."""
from string import Template
from typing import Any

from arf.agent.config import AgentConfig
from arf.agent.prompt import SystemPrompt


class DefaultSystemPromptProvider:
    """Default implementation of SystemPromptProvider.

    Reads PrefixConfig (role + critical_rules) for the stable prefix,
    builds inventory (tools + skills) for the suffix, and uses
    string.Template for placeholder substitution.

    Per-turn placeholders ($MEMORY, $WORKSPACE, $TURN_BUDGET, $LANGUAGE)
    are left as-is in the suffix — the engine replaces them at runtime.
    """

    def __init__(
        self,
        config: AgentConfig,
        tool_definitions: list[dict[str, Any]],
        skill_definitions: list[dict[str, Any]],
    ) -> None:
        self._config = config
        self._tool_definitions = tool_definitions
        self._skill_definitions = skill_definitions

    def build(self) -> SystemPrompt:
        sp = self._config.system_prompt
        pc = sp.prefix

        # Prefix: role + critical_rules in guaranteed order
        prefix_parts: list[str] = []
        if pc.role:
            prefix_parts.append(pc.role.strip())
        if pc.critical_rules:
            prefix_parts.append(pc.critical_rules.strip())
        prefix = "\n\n".join(prefix_parts)

        # Suffix: substitute $INVENTORY, leave $MEMORY etc. for engine
        inventory = self._build_inventory()
        suffix = Template(sp.suffix).safe_substitute(INVENTORY=inventory)

        return SystemPrompt(prefix=prefix, suffix=suffix)

    def _build_inventory(self) -> str:
        kernel_tools = [
            t for t in self._tool_definitions
            if t.get("activation", "discoverable") == "kernel"
        ]
        discoverable_tools = [
            t for t in self._tool_definitions
            if t.get("activation", "discoverable") == "discoverable"
        ]
        lines: list[str] = []

        if kernel_tools:
            lines.append("## Available Tools\n")
            for t in kernel_tools:
                lines.append(f"- `{t['name']}`: {t.get('description', '')}")

        if discoverable_tools:
            lines.append("\n## Discoverable Tools\n")
            lines.append(
                "These tools are available on demand. "
                "Use `resource_loader` to activate them:\n"
            )
            for t in discoverable_tools:
                lines.append(f"- `{t['name']}`: {t.get('description', '')}")

        if self._skill_definitions:
            lines.append("\n## Available Skills\n")
            lines.append(
                "Skills are loaded on demand. "
                "Read a skill's full instructions via `file_reader`:\n"
            )
            for s in self._skill_definitions:
                lines.append(
                    f"- `{s['name']}`: {s.get('description', '(no description)')}"
                    f"  → read `skills/{s['name']}.yaml`"
                )

        return "\n".join(lines) if lines else ""
