"""PluginContext — full-visibility context passed to plugin hooks."""
from dataclasses import dataclass, field
from arf.core.state import AgentState


@dataclass
class PluginContext:
    """Full read/write context for plugin hook invocation.

    Plugin has complete visibility into state, messages, tool definitions,
    and runtime directories. Blocking plugins can mutate state; side plugins
    should treat it as read-only (convention, not enforced).
    """

    # Runtime identifiers
    session_id: str = "default"
    interaction_round: int = 0
    turn: int = 0
    current_step: str = ""              # "call_model" | "execute_tools"

    # Core data — full visibility
    state: AgentState = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)  # shortcut to state["messages"]
    tool_definitions: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    model: str = ""

    # Directories
    workspace_dir: str = "."
    memory_dir: str = "./memory"
    state_dir: str = "./data/state"
    trace_dir: str = "./data/traces"

    # Hook-specific payload
    hook_data: dict = field(default_factory=dict)

    # Plugin configuration (from plugin.yaml)
    plugin_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "interaction_round": self.interaction_round,
            "turn": self.turn,
            "current_step": self.current_step,
            "model": self.model,
            "workspace_dir": self.workspace_dir,
            "memory_dir": self.memory_dir,
            "state_dir": self.state_dir,
            "trace_dir": self.trace_dir,
            "system_model": self.model,
            **self.hook_data,
            **self.plugin_config,
        }
