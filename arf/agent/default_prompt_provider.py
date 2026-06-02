"""DefaultSystemPromptProvider — assembles SystemPrompt from config."""
from arf.agent.config import AgentConfig
from arf.agent.prompt import SystemPrompt


class DefaultSystemPromptProvider:
    """Default implementation of SystemPromptProvider.

    Reads PrefixConfig (role + critical_rules) for the stable prefix.
    Suffix is passed through as-is — inventory ($INVENTORY) is filled
    by the MCP manager at startup, and per-turn placeholders ($MEMORY,
    $WORKSPACE, $TURN_BUDGET) are replaced by the engine at runtime.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config

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

        # Suffix: $INVENTORY left as-is for MCP to fill at startup
        suffix = sp.suffix

        return SystemPrompt(prefix=prefix, suffix=suffix)
