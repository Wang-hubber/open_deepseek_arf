"""Protocols for hooks domain."""
from typing import Protocol
from arf.core.results import HookResult
from arf.core.config_base import HookDefinition


class HookRunner(Protocol):
    async def fire(self, event_type: str, context: dict) -> list[HookResult]: ...
    def set_order(self, event_type: str, hook_names: list[str]) -> None: ...
    def get_definitions(self) -> list[HookDefinition]: ...
