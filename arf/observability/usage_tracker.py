"""UsageTracker — accumulates token usage from EventBus, persists to file."""
import json
import time
from pathlib import Path
from arf.core.events import AgentEvent


class UsageTracker:
    """Subscribe to EventBus, track per-model token consumption.
    Persisted to JSON so stats survive restarts.
    """

    def __init__(self, bus, dir: str | Path = "./memory") -> None:
        self._dir = Path(dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "usage.json"
        self._models: dict[str, dict] = {}
        self._total_calls = 0
        self._load()
        import asyncio
        self._task = asyncio.create_task(self._consume(bus))

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_tokens(self) -> int:
        return sum(m.get("total_tokens", 0) for m in self._models.values())

    @property
    def by_model(self) -> list[dict]:
        return [
            {
                "model_name": name,
                "model_type": name,
                "prompt_tokens": m.get("prompt_tokens", 0),
                "completion_tokens": m.get("completion_tokens", 0),
                "total_tokens": m.get("total_tokens", 0),
                "calls": m.get("calls", 0),
            }
            for name, m in self._models.items()
        ]

    def summary(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "total_calls": self.total_calls,
            "by_model": self.by_model,
        }

    async def _consume(self, bus) -> None:
        try:
            async for event in bus.subscribe():
                if event.type == "model_call_end":
                    usage = event.data.get("usage", {})
                    if not usage:
                        continue
                    model = event.data.get("model", "unknown")
                    m = self._models.setdefault(model, {
                        "prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0, "calls": 0,
                    })
                    m["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    m["completion_tokens"] += usage.get("completion_tokens", 0)
                    m["total_tokens"] += usage.get("total_tokens", 0)
                    m["calls"] += 1
                    self._total_calls += 1
                    self._save()
        except Exception:
            pass

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._models = data.get("models", {})
            self._total_calls = data.get("total_calls", 0)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps({
                "models": self._models,
                "total_calls": self._total_calls,
                "updated_at": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
