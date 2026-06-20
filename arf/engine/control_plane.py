"""ControlPlane — thin compatibility wrapper delegating to AgentHarness.

The old 1337-line execution loop is replaced by AgentHarness.
This stub keeps the ControlPlane class for backward compat with
existing tests. New code should use AgentHarness directly.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable
from collections.abc import AsyncIterator

from arf.core.events import AgentEvent
from arf.core.plugin_context import PluginContext
from arf.hooks.in_process_runner import InProcessHookRunner
from arf.hooks.runner import SubprocessHookRunner

logger = logging.getLogger("arf.engine")


class SessionAbortedError(Exception):
    """Session was aborted by an error handler."""


class MessageContractError(Exception):
    """Message structure validation failed."""


class ControlPlane:
    """Thin compatibility stub that holds resources but delegates execution.

    New code: use AgentHarness from arf.harness.engine instead.
    """

    def __init__(self, **kwargs) -> None:
        logger.debug("ControlPlane stub created (all execution in AgentHarness)")
        for key, val in kwargs.items():
            setattr(self, f"_{key}", val)
        self._call_model = kwargs.get("call_model")
        self._stream_model = kwargs.get("stream_model")
        self._blocking = InProcessHookRunner(kwargs.get("blocking_plugins", []))
        self._side = SubprocessHookRunner(kwargs.get("side_plugins", []))
        self._interaction_round = 0

    # ── Engine methods (kept for compat) ──

    async def astream(self, state: dict, stop_on_text: bool = False) -> AsyncIterator[AgentEvent]:
        """Legacy astream — minimal no-op wrapper. Use AgentHarness.run()."""
        logger.warning("ControlPlane.astream() is deprecated. Use AgentHarness.run().")
        if False:
            yield  # Make this an async generator

    async def resume(self, state: dict) -> AsyncIterator[AgentEvent]:
        if False:
            yield

    async def close(self, state: dict) -> AsyncIterator[AgentEvent]:
        if False:
            yield

    def set_call_model(self, call_model) -> None:
        self._call_model = call_model

    def set_stream_model(self, stream_model) -> None:
        self._stream_model = stream_model

    def set_skill_index(self, skill_index) -> None:
        self._skill_index = skill_index

    def set_context_texts(self, **kwargs) -> None:
        pass

    def set_memory_index(self, memory_index) -> None:
        self._memory_index = memory_index

    def set_undo_plugin(self, plugin) -> None:
        pass

    @property
    def _data_dir(self) -> str:
        return getattr(self, '__data_dir', './data')
