"""FileTraceStore — persist agent events to JSONL files (append-only, O(1))."""
import asyncio
import json
from pathlib import Path
from arf.core.events import AgentEvent


def _sanitize_for_json(obj):
    """Convert non-JSON-serializable values to strings."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, Exception):
        return f"{type(obj).__name__}: {obj}"
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


class FileTraceStore:
    """订阅 EventBus，将事件按 session 追加写入 JSONL 文件。

    文件位置: {dir}/{session_id}.jsonl
    每行一个 JSON 对象，append-only — O(1) 每次写入。

    用法:
        store = FileTraceStore(agent.event_bus, dir="./data/traces")
    """

    def __init__(self, bus, dir: str | Path = "./data/traces") -> None:
        self._dir = Path(dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._consume(bus))

    async def _consume(self, bus) -> None:
        try:
            async for event in bus.subscribe():
                if event.type in ("session_start", "session_end", "thinking_delta"):
                    continue
                self._append(event.session_id, event)
        except asyncio.CancelledError:
            pass

    def _append(self, session_id: str, event: AgentEvent) -> None:
        record = json.dumps({
            "type": event.type,
            "data": _sanitize_for_json(event.data),
            "turn": event.turn,
            "timestamp": event.timestamp,
            "trace_id": event.trace_id,
            "span_id": event.span_id,
        }, ensure_ascii=False)
        path = self._dir / f"{session_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(record + "\n")

    def load(self, session_id: str) -> list[dict]:
        path = self._dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        events: list[dict] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events

    def list_sessions(self) -> list[str]:
        return [
            p.stem for p in self._dir.glob("*.jsonl")
        ]
