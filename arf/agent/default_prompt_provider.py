"""DefaultSystemPromptProvider — assembles SystemPrompt from config."""
from arf.agent.config import AgentConfig
from arf.agent.prompt import SystemPrompt


class DefaultSystemPromptProvider:
    """Default implementation of SystemPromptProvider.

    Reads PrefixConfig (role + critical_rules). Skills, tools, and
    memory are injected by the framework as separate system messages.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    def build(self) -> SystemPrompt:
        pc = self._config.system_prompt.prefix
        parts: list[str] = []
        if pc.role:
            parts.append(pc.role.strip())
        if pc.critical_rules:
            parts.append(pc.critical_rules.strip())
        return SystemPrompt(prefix="\n\n".join(parts))
