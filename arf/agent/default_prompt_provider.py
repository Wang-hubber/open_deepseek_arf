"""DefaultSystemPromptProvider — assembles SystemPrompt from config."""
from arf.agent.config import AgentConfig
from arf.agent.prompt import SystemPrompt


class DefaultSystemPromptProvider:
    """Default implementation of SystemPromptProvider.

    Assembles prefix from role + critical_rules + workspace_dir.
    Skills, tools, and memory are injected by the framework as
    separate system messages.
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
        if self._config.workspace_dir:
            parts.append(
                f"## Workspace\n\n"
                f"Your working directory is: {self._config.workspace_dir}\n"
                f"All file paths must stay within this directory tree."
            )
        return SystemPrompt(prefix="\n\n".join(parts))
