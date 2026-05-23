"""Protocols for concurrency domain."""
from typing import Protocol


class TaskScheduler(Protocol):
    async def schedule(self, tasks: list[dict]) -> list[dict]: ...
    async def execute(self, tasks: list[dict]) -> list[dict]: ...
