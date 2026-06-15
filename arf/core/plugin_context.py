"""PluginContext — full-visibility context passed to plugin hooks."""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from arf.core.state import AgentState

if TYPE_CHECKING:
    from arf.core.protocols.event_bus import EventBus


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
    data_dir: str = "./data"
    memory_dir: str = "./data/memory"
    state_dir: str = "./data/state"
    trace_dir: str = "./data/traces"

    # Infrastructure
    event_bus: EventBus | None = None  # for side plugins to emit events

    # Hook-specific payload
    hook_data: dict = field(default_factory=dict)

    # Plugin configuration (from plugin.yaml)
    plugin_config: dict = field(default_factory=dict)

    # --- Hook → engine event channel ---
    # Hooks push events here via emit(); engine drains and yields them.
    _pending_events: list = field(default_factory=list)
    _event_ready: asyncio.Event | None = None  # created in engine

    def emit(self, event_type: str, data: dict) -> None:
        """Push an event into the stream from within a hook.

        The engine drains this queue after each hook fires, yielding
        events in the same astream() flow as engine-generated events.
        """
        from arf.core.events import AgentEvent
        self._pending_events.append(
            AgentEvent(type=event_type, data=data, session_id=self.session_id)
        )
        if self._event_ready:
            self._event_ready.set()

    def inject_engine_event(self, event_type: str, data: dict) -> None:
        """Record an engine-internal event for trace visibility.

        Called by ControlPlane after model_call / tool_exec to inject
        results into hook_data so TracePlugin (and other observers)
        can capture them in the next hook callback.
        """
        self.hook_data.setdefault("_engine_events", []).append({
            "type": event_type,
            "timestamp": time.time(),
            "data": data,
        })

    def inject_user_annotation(
        self,
        feedback: str,
        reason: str = "",
        round: int | None = None,
    ) -> None:
        """Inject a user feedback annotation into the trace stream.

        Fire-and-forget — does not block engine execution. Supports both
        immediate (same round) and delayed (past round) annotation.

        Args:
            feedback: "thumbs_up" or "thumbs_down"
            reason: optional free-text explanation
            round: target round to annotate, defaults to current round
        """
        from datetime import datetime, timezone
        target_round = round if round is not None else self.interaction_round
        data = {
            "round": target_round,
            "feedback": feedback,
            "reason": reason,
            "annotated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.inject_engine_event("user_annotation", data)
        self.emit("user_annotation", data)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "interaction_round": self.interaction_round,
            "turn": self.turn,
            "current_step": self.current_step,
            "model": self.model,
            "workspace_dir": self.workspace_dir,
            "data_dir": self.data_dir,
            "memory_dir": self.memory_dir,
            "state_dir": self.state_dir,
            "trace_dir": self.trace_dir,
            "system_model": self.model,
            **self.hook_data,
            **self.plugin_config,
        }
