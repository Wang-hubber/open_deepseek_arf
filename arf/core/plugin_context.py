"""PluginContext — read-only context passed to plugin hooks."""
from dataclasses import dataclass, field


@dataclass
class PluginContext:
    """Read-only context for plugin hook invocation.

    Contains runtime info (session, round, dirs) plus hook-specific data.
    """

    # Runtime
    session_id: str = "default"
    interaction_round: int = 0
    memory_dir: str = "./memory"
    workspace_dir: str = "."
    state_dir: str = "./data/state"
    trace_dir: str = "./data/traces"
    system_model: str = "quick"

    # Hook-specific payload
    hook_data: dict = field(default_factory=dict)

    # Plugin configuration (from plugin.yaml)
    plugin_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "interaction_round": self.interaction_round,
            "memory_dir": self.memory_dir,
            "workspace_dir": self.workspace_dir,
            "state_dir": self.state_dir,
            "trace_dir": self.trace_dir,
            "system_model": self.system_model,
            **self.hook_data,
            **self.plugin_config,
        }
