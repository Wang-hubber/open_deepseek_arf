"""FileTraceStore — persist agent events to JSON files per session."""
import asyncio
import json
from pathlib import Path
from arf.core.events import AgentEvent


class FileTraceStore:
    """订阅 EventBus，将事件按 session 追加写入 JSON 文件。

    文件位置: {dir}/{session_id}.json
    每个 session 一个文件，session 结束后完整轨迹可被 /trace/{id} 查询。

    用法:
        store = FileTraceStore(agent.event_bus, dir="./memory/sessions")
        # 自动开始消费，无需手动管理生命周期
    """

    def __init__(self, bus, dir: str | Path = "./memory/sessions") -> None:
        self._dir = Path(dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._consume(bus))

    async def _consume(self, bus) -> None:
        try:
            async for event in bus.subscribe():
                if event.type in ("session_start", "session_end"):
                    continue
                self._append(event.session_id, event)
        except asyncio.CancelledError:
            pass

    def _append(self, session_id: str, event: AgentEvent) -> None:
        path = self._dir / f"{session_id}.json"
        records: list[dict] = []
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                records = []
        records.append({
            "type": event.type,
            "data": event.data,
            "turn": event.turn,
            "timestamp": event.timestamp,
            "trace_id": event.trace_id,
            "span_id": event.span_id,
        })
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, session_id: str) -> list[dict]:
        """加载指定 session 的完整轨迹"""
        path = self._dir / f"{session_id}.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def list_sessions(self) -> list[str]:
        """列出所有已记录的 session ID"""
        return [
            p.stem for p in self._dir.glob("*.json")
        ]
