"""Plugin base class — register events at harness checkpoints."""
from __future__ import annotations
from abc import ABC, abstractmethod
from arf.harness.context import PluginContext


class Plugin(ABC):
    def __init__(self, name: str, events: list[dict], config: dict | None = None) -> None:
        self.name = name
        self.events = events  # [{hook_name, event_name, mode}]
        self.config = config or {}

    def event_names_for_hook(self, hook_name: str) -> list[str]:
        return [e["event_name"] for e in self.events if e["hook_name"] == hook_name]

    def mode_for(self, hook_name: str, event_name: str) -> str:
        for e in self.events:
            if e["hook_name"] == hook_name and e["event_name"] == event_name:
                return e.get("mode", "side")
        return "side"

    @abstractmethod
    async def handle(self, event_name: str, ctx: PluginContext) -> None: ...
