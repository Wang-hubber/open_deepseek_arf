"""PluginContext — injected into plugins at each harness checkpoint."""
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Any
from arf.core.events import AgentEvent

if TYPE_CHECKING:
    from arf.agent.primitive import PrimitiveAgent
    from arf.event_bus import InMemoryEventBus


class PluginContext:
    def __init__(
        self,
        agent: PrimitiveAgent,
        session_id: str,
        event_bus: InMemoryEventBus | None = None,
        data_dir: str = "./data",
        trace_queue: asyncio.Queue | None = None,
    ) -> None:
        self.agent = agent
        self.session_id = session_id
        self.hook_data: dict[str, Any] = {}
        self.captured_events: list[AgentEvent] = []
        self._event_bus = event_bus
        self._trace_queue = trace_queue

        # Lifecycle counters (set by harness at each checkpoint)
        self.turn: int = 0
        self.interaction_round: int = 0

        # Directories
        self.data_dir: str = data_dir

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> AgentEvent:
        event = AgentEvent(
            type=event_type,  # type: ignore[arg-type]
            data=data or {},
            session_id=self.session_id,
            turn=self.turn,
        )
        if self._event_bus:
            self._event_bus.emit(event)
        self.captured_events.append(event)
        if self._trace_queue is not None:
            self._trace_queue.put_nowait(event)
        return event

    def inject_engine_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Deprecated: prefer ctx.emit(). Events still forwarded to trace."""
        self.hook_data.setdefault("_engine_events", []).append({
            "type": event_type,
            "data": data,
        })
        self.emit(event_type, data)  # forward to trace queue
