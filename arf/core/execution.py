"""Shared types for ActionRunner and Promotion — the execution protocol both layers consume."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


# ── Retry Policy ──────────────────────────────────────────

@dataclass
class RetryPolicy:
    """Per-executable retry configuration. Embedded in Executable."""

    max_attempts: int = 2
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    jitter: bool = True


# ── Execution Result ──────────────────────────────────────

@dataclass
class ExecutionError:
    """Classifies failure for RetryExecutor routing."""

    kind: Literal["transient", "deterministic"]
    message: str


@dataclass
class ExecuteResult:
    """Result of executing a single Executable."""

    name: str
    success: bool
    data: dict[str, Any] | None = None
    error: ExecutionError | None = None
    duration_ms: float = 0.0
    attempt: int = 1


# ── Decision (Promotion output) ───────────────────────────

@dataclass
class Decision:
    """Promotion gate output."""

    action: Literal["allow", "deny", "ask"]
    reason: str = ""


# ── Wave (DependencyResolver output) ──────────────────────

@dataclass
class Wave:
    """A group of executables with no mutual dependencies — safe to parallelize."""

    executables: list[Executable]


# ── Executable Protocol ───────────────────────────────────

@runtime_checkable
class Executable(Protocol):
    """The single protocol both ActionRunner and Promotion consume.

    Neither layer knows or cares whether this is a hook, tool, or model call.
    """

    name: str
    kind: Literal["hook", "tool", "model_call"]
    dependencies: list[str]
    resources: list[str]
    side_effect: bool
    retry_policy: RetryPolicy
    timeout: float | None

    async def execute(self) -> ExecuteResult:
        """Execute the unit. Called by ActionRunner."""
        ...

    async def rollback(self) -> None:
        """Reverse the unit's side effects. Called by RollbackManager on failure."""
        ...
