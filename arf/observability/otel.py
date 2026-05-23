"""OtelTracer — convert AgentEvent stream to OpenTelemetry Spans."""
import os
from arf.core.events import AgentEvent


class OtelTracer:
    def __init__(self) -> None:
        self._exporter = os.environ.get("OTEL_EXPORTER", "none")
        self._spans: dict[str, dict] = {}

    async def consume(self, events: list[AgentEvent]) -> None:
        for e in events:
            span_id = e.trace_id + ":" + e.type
            if e.type.endswith("_start"):
                self._spans[span_id] = {"start": e.timestamp, "attributes": {
                    "event_type": e.type, "session_id": e.session_id,
                    "agent_name": e.agent_name, "turn": e.turn, **e.data,
                }}
            elif e.type.endswith("_end") and span_id in self._spans:
                s = self._spans.pop(span_id)
                duration = (e.timestamp - s["start"]) * 1000
                if self._exporter == "console":
                    print(f"[OTel] {e.type}: {duration:.1f}ms {s['attributes']}")

    async def flush(self) -> None:
        pass
