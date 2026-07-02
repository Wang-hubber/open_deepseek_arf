"""subagent_launcher.py — Phase 8 task F7.

Spawns short-lived subagent Engines on the same Bus as the parent.
Used by the parent Engine to delegate scoped tasks; the subagent runs
to completion in its own session and returns a result message.

This module is the Python-side helper; the wire protocol is
`SubagentDelegate` (parent → subagent) and `SubagentResult`
(subagent → parent) defined in arf-core. For the codecompass-fs
example the App's `delegate_to_subagent` method is the public surface.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from arf import AgentConfig, EngineBuilder, NodeId, Bus


@dataclass
class SubagentSpec:
    """How to spawn a subagent engine.

    `task` is the natural-language description of the work.
    `parent_session_id` identifies the requesting engine (for correlation).
    `config_overrides` lets the App tune the subagent's AgentConfig
    (e.g., different model, fewer max_turns).
    """

    name: str
    task: str
    parent_session_id: str
    config_overrides: dict = field(default_factory=dict)
    max_depth: int = 2  # nesting cap (F7 spec)


@dataclass
class SubagentOutput:
    """Result returned to the parent."""

    subagent_id: str
    status: str  # "success" / "failed" / "cancelled"
    output: str
    trajectory_messages: list = field(default_factory=list)


class SubagentLauncher:
    """Spawn and manage subagent engines.

    Usage:
        launcher = SubagentLauncher(bus, parent_engine)
        out = await launcher.delegate(Spec("researcher", "find X", parent_sid))
    """

    def __init__(self, bus: Bus, parent_engine: Any) -> None:
        self.bus = bus
        self.parent = parent_engine
        self._live: dict[str, Any] = {}  # subagent_id → engine
        self._depth: dict[str, int] = {}  # subagent_id → depth
        self._lock = asyncio.Lock()

    async def delegate(self, spec: SubagentSpec) -> SubagentOutput:
        """Spawn a subagent, run it, return the result.

        MVP: synchronous-ish — runs to completion before returning.
        """
        sub_id = f"subagent-{uuid.uuid4().hex[:8]}"
        async with self._lock:
            # Enforce depth limit
            parent_depth = self._depth.get(spec.parent_session_id, 0)
            if parent_depth + 1 > spec.max_depth:
                return SubagentOutput(
                    subagent_id=sub_id,
                    status="failed",
                    output=f"max_depth {spec.max_depth} exceeded",
                )
            self._depth[sub_id] = parent_depth + 1

        # Build subagent AgentConfig (inherit from parent + overrides)
        sub_config = AgentConfig(
            provider="mock",
            model="mock-v1",
            system_prompt_template=(
                f"You are a subagent spawned to perform a scoped task.\n"
                f"Task: {spec.task}\n"
                f"Be concise; return only what was asked for."
            ),
            max_turns=spec.config_overrides.get("max_turns", 5),
            routes=spec.config_overrides.get("routes", {}),
            checkpoint_rules=spec.config_overrides.get("checkpoint_rules", []),
        )
        sub_engine = await EngineBuilder.new(buses=[self.bus]).build(config=sub_config)
        async with self._lock:
            self._live[sub_id] = sub_engine

        # Run subagent synchronously (for MVP)
        try:
            result = await self._run_subagent(sub_engine, spec)
            return SubagentOutput(
                subagent_id=sub_id,
                status=result.get("status", "success"),
                output=result.get("output", ""),
                trajectory_messages=result.get("trajectory", []),
            )
        except Exception as e:
            return SubagentOutput(
                subagent_id=sub_id,
                status="failed",
                output=f"error: {e}",
            )
        finally:
            async with self._lock:
                self._live.pop(sub_id, None)

    async def _run_subagent(self, engine: Any, spec: SubagentSpec) -> dict:
        """Run the subagent on its task. Returns {status, output, trajectory}."""
        # For the example, the subagent uses the same mock model
        # via the parent app's session_store, so we route through it.
        # Real impl: the subagent engine's run() handles state.
        from app import CodecompassApp  # avoid circular import at module load

        # We synthesize a response via the parent's mock logic
        # (the real impl would use the subagent's own model adapter).
        # For MVP we use a static script.
        last_user = spec.task
        text_lower = last_user.lower()
        if "delegate" in text_lower:
            output = f"[subagent {spec.name}] delegated task handled"
        elif "summarize" in text_lower:
            output = f"[subagent {spec.name}] summary: ok"
        else:
            output = f"[subagent {spec.name}] done: {last_user[:50]}"
        return {"status": "success", "output": output, "trajectory": []}

    async def cancel_all(self) -> None:
        """Cancel all live subagents. Used during parent shutdown."""
        async with self._lock:
            for sub_id, eng in list(self._live.items()):
                try:
                    # Real impl: engine.cancel()
                    pass
                except Exception:
                    pass
                self._live.pop(sub_id, None)
