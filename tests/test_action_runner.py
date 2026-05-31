"""Tests for ActionRunner components."""

from __future__ import annotations

import time

import pytest

from arf.core.execution import (
    Executable,
    ExecuteResult,
    ExecutionError,
    RetryPolicy,
)


# ── Test Executable factory ───────────────────────────────────


class _Failing:
    """Test executable that always fails with a deterministic error."""

    name = "failer"
    kind = "tool"
    dependencies: list[str] = []
    resources: list[str] = []
    side_effect = False
    retry_policy = RetryPolicy()
    timeout: float | None = None

    async def execute(self) -> ExecuteResult:
        return ExecuteResult(
            name="failer",
            success=False,
            error=ExecutionError(kind="deterministic", message="bad"),
        )

    async def rollback(self) -> None:
        pass


def _make_exec(
    exec_name: str,
    deps: list[str] | None = None,
    resources: list[str] | None = None,
) -> Executable:
    """Build a minimal Executable for testing."""

    _deps = deps or []
    _res = resources or []

    class _E:
        def __init__(self) -> None:
            self.name = exec_name
            self.kind = "tool"
            self.dependencies = _deps
            self.resources = _res
            self.side_effect = False
            self.retry_policy = RetryPolicy()
            self.timeout: float | None = None

        async def execute(self) -> ExecuteResult:
            return ExecuteResult(name=self.name, success=True)

        async def rollback(self) -> None:
            pass

    return _E()  # type: ignore[return-value]


# ── DependencyResolver tests ──────────────────────────────────


class TestDependencyResolver:
    def test_no_dependencies_single_wave(self):
        """All independent executables go into one wave."""
        from arf.action_runner.resolver import DependencyResolver

        a = _make_exec("a")
        b = _make_exec("b")
        c = _make_exec("c")
        waves = DependencyResolver.resolve([a, b, c])
        assert len(waves) == 1
        assert len(waves[0].executables) == 3

    def test_linear_dependency_chain(self):
        """a -> b -> c produces three waves."""
        from arf.action_runner.resolver import DependencyResolver

        a = _make_exec("a")
        b = _make_exec("b", deps=["a"])
        c = _make_exec("c", deps=["b"])
        waves = DependencyResolver.resolve([c, a, b])  # shuffled input
        assert len(waves) == 3
        assert waves[0].executables[0].name == "a"
        assert waves[1].executables[0].name == "b"
        assert waves[2].executables[0].name == "c"

    def test_diamond_dependency(self):
        """a -> [b, c] -> d produces three waves with parallelism in middle."""
        from arf.action_runner.resolver import DependencyResolver

        a = _make_exec("a")
        b = _make_exec("b", deps=["a"])
        c = _make_exec("c", deps=["a"])
        d = _make_exec("d", deps=["b", "c"])
        waves = DependencyResolver.resolve([d, c, b, a])
        assert len(waves) == 3
        assert waves[0].executables[0].name == "a"
        assert {e.name for e in waves[1].executables} == {"b", "c"}
        assert waves[2].executables[0].name == "d"

    def test_missing_dependency_raises(self):
        """Dependency declared but not present -> ValueError."""
        from arf.action_runner.resolver import DependencyResolver

        a = _make_exec("a", deps=["nonexistent"])
        with pytest.raises(ValueError, match="missing"):
            DependencyResolver.resolve([a])

    def test_circular_dependency_raises(self):
        """a -> b -> a -> ValueError."""
        from arf.action_runner.resolver import DependencyResolver

        a = _make_exec("a", deps=["b"])
        b = _make_exec("b", deps=["a"])
        with pytest.raises(ValueError, match="Circular"):
            DependencyResolver.resolve([a, b])

    def test_empty_input_returns_empty(self):
        """Empty list returns empty waves."""
        from arf.action_runner.resolver import DependencyResolver

        assert DependencyResolver.resolve([]) == []


# ── ResourceScheduler tests ──────────────────────────────────


class TestResourceScheduler:
    def test_no_conflicts_all_parallel(self):
        """All different resources -> no serialization needed."""
        from arf.action_runner.scheduler import ResourceScheduler

        a = _make_exec("a", resources=["file:x"])
        b = _make_exec("b", resources=["file:y"])
        c = _make_exec("c", resources=["api:z"])
        schedule = ResourceScheduler.schedule([a, b, c])
        assert len(schedule) == 1
        assert len(schedule[0]) == 3

    def test_write_write_conflict_serializes(self):
        """Same resource with write-write -> must serialize."""
        from arf.action_runner.scheduler import ResourceScheduler

        a = _make_exec("a", resources=["file:x"])
        a.side_effect = True
        b = _make_exec("b", resources=["file:x"])
        b.side_effect = True
        schedule = ResourceScheduler.schedule([a, b])
        assert len(schedule) >= 2

    def test_write_read_conflict_serializes(self):
        """Same resource, one write one read -> serialize."""
        from arf.action_runner.scheduler import ResourceScheduler

        writer = _make_exec("writer", resources=["file:x"])
        writer.side_effect = True
        reader = _make_exec("reader", resources=["file:x"])
        reader.side_effect = False
        schedule = ResourceScheduler.schedule([reader, writer])
        assert len(schedule) >= 2

    def test_read_read_no_conflict(self):
        """Same resource, both reads -> can parallelize."""
        from arf.action_runner.scheduler import ResourceScheduler

        a = _make_exec("a", resources=["file:x"])
        a.side_effect = False
        b = _make_exec("b", resources=["file:x"])
        b.side_effect = False
        schedule = ResourceScheduler.schedule([a, b])
        assert len(schedule) == 1
        assert len(schedule[0]) == 2

    def test_empty_input_returns_empty(self):
        """Empty list returns empty schedule."""
        from arf.action_runner.scheduler import ResourceScheduler

        assert ResourceScheduler.schedule([]) == []

    def test_mixed_conflicts_serializes_correctly(self):
        """a(file:x, file:y), b(file:x), c(file:y) -> a alone, then b+c parallel."""
        from arf.action_runner.scheduler import ResourceScheduler

        a = _make_exec("a", resources=["file:x", "file:y"])
        a.side_effect = True
        b = _make_exec("b", resources=["file:x"])
        c = _make_exec("c", resources=["file:y"])
        schedule = ResourceScheduler.schedule([a, b, c])
        all_execs = [e for group in schedule for e in group]
        assert all_execs[0].name == "a"
        assert len(schedule) == 2
        assert {e.name for e in schedule[1]} == {"b", "c"}


# ── RetryExecutor tests ──────────────────────────────────────


class TestRetryExecutor:
    @pytest.mark.anyio

    async def test_success_first_attempt_no_retry(self):
        """Successful execution returns immediately."""
        from arf.action_runner.retry import RetryExecutor

        calls: list[int] = []

        class _OK:
            name = "ok"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy(max_attempts=3)
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                calls.append(1)
                return ExecuteResult(name="ok", success=True)

            async def rollback(self) -> None:
                pass

        result = await RetryExecutor.execute(_OK())  # type: ignore[arg-type]
        assert result.success
        assert result.attempt == 1
        assert len(calls) == 1


    @pytest.mark.anyio


    async def test_transient_retries_then_succeeds(self):
        """Transient errors are retried and succeed on later attempt."""
        from arf.action_runner.retry import RetryExecutor

        call_count = 0

        class _Flaky:
            name = "flaky"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy(max_attempts=3, backoff_base=0.01)
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return ExecuteResult(
                        name="flaky",
                        success=False,
                        error=ExecutionError(kind="transient", message="timeout"),
                    )
                return ExecuteResult(name="flaky", success=True)

            async def rollback(self) -> None:
                pass

        result = await RetryExecutor.execute(_Flaky())  # type: ignore[arg-type]
        assert result.success
        assert result.attempt == 3
        assert call_count == 3


    @pytest.mark.anyio


    async def test_deterministic_error_no_retry(self):
        """Deterministic errors skip retry entirely."""
        from arf.action_runner.retry import RetryExecutor

        class _Bad:
            name = "bad"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy(max_attempts=3, backoff_base=0.01)
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                return ExecuteResult(
                    name="bad",
                    success=False,
                    error=ExecutionError(kind="deterministic", message="invalid param"),
                )

            async def rollback(self) -> None:
                pass

        result = await RetryExecutor.execute(_Bad())  # type: ignore[arg-type]
        assert not result.success
        assert result.attempt == 1  # no retry
        assert result.error is not None
        assert result.error.kind == "deterministic"


    @pytest.mark.anyio


    async def test_retries_exhausted_returns_last_failure(self):
        """When max attempts exhausted, return last failure."""
        from arf.action_runner.retry import RetryExecutor

        class _AlwaysDown:
            name = "down"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy(max_attempts=2, backoff_base=0.01)
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                return ExecuteResult(
                    name="down",
                    success=False,
                    error=ExecutionError(kind="transient", message="timeout"),
                )

            async def rollback(self) -> None:
                pass

        result = await RetryExecutor.execute(_AlwaysDown())  # type: ignore[arg-type]
        assert not result.success
        assert result.attempt == 2


    @pytest.mark.anyio


    async def test_exception_treated_as_transient(self):
        """Execute() raising an exception is treated as transient and retried."""
        from arf.action_runner.retry import RetryExecutor

        call_count = 0

        class _Crashy:
            name = "crashy"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy(max_attempts=2, backoff_base=0.01)
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                nonlocal call_count
                call_count += 1
                raise RuntimeError("connection reset")

            async def rollback(self) -> None:
                pass

        result = await RetryExecutor.execute(_Crashy())  # type: ignore[arg-type]
        assert not result.success
        assert call_count == 2  # retried once


# ── RollbackManager tests ────────────────────────────────────


class TestRollbackManager:
    @pytest.mark.anyio

    async def test_rollback_called_on_failed_unit(self):
        """Failed unit gets rollback() called."""
        from arf.action_runner.rollback import RollbackManager

        rolled_back: list[str] = []

        class _FailingLocal:
            name = "failer"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy()
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                return ExecuteResult(
                    name="failer",
                    success=False,
                    error=ExecutionError(kind="deterministic", message="bad"),
                )

            async def rollback(self) -> None:
                rolled_back.append(self.name)

        await RollbackManager.handle(_FailingLocal(), [])  # type: ignore[arg-type]
        assert rolled_back == ["failer"]


    @pytest.mark.anyio


    async def test_downstream_cancelled(self):
        """All executables depending on the failed unit are cancelled."""
        from arf.action_runner.rollback import RollbackManager

        executed: list[str] = []

        class _Downstream:
            name = "downstream"
            kind = "tool"
            dependencies: list[str] = ["failer"]
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy()
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                executed.append(self.name)
                return ExecuteResult(name="downstream", success=True)

            async def rollback(self) -> None:
                pass

        cancelled = await RollbackManager.handle(_Failing(), [_Downstream()])  # type: ignore[list-item]
        # downstream depends on failer -> should be cancelled
        assert cancelled == ["downstream"]
        # execute should NOT be called on cancelled units
        assert executed == []


    @pytest.mark.anyio


    async def test_sibling_not_affected(self):
        """Units not depending on the failed one are not cancelled."""
        from arf.action_runner.rollback import RollbackManager

        class _Sibling:
            name = "sibling"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy()
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                return ExecuteResult(name="sibling", success=True)

            async def rollback(self) -> None:
                pass

        cancelled = await RollbackManager.handle(_Failing(), [_Sibling()])  # type: ignore[list-item]
        assert cancelled == []


    @pytest.mark.anyio


    async def test_rollback_error_suppressed(self):
        """If rollback() itself raises, the error is captured, not propagated."""
        from arf.action_runner.rollback import RollbackManager

        class _BadRollback:
            name = "bad_rb"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy()
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                return ExecuteResult(
                    name="bad_rb",
                    success=False,
                    error=ExecutionError(kind="deterministic", message="fail"),
                )

            async def rollback(self) -> None:
                raise RuntimeError("rollback failed too")

        # Should not raise
        await RollbackManager.handle(_BadRollback(), [])  # type: ignore[arg-type]


    @pytest.mark.anyio


    async def test_successful_unit_rollback_called_but_error_suppressed(self):
        """Rollback on a successful unit is called but errors are suppressed."""
        from arf.action_runner.rollback import RollbackManager

        class _Success:
            name = "ok"
            kind = "tool"
            dependencies: list[str] = []
            resources: list[str] = []
            side_effect = False
            retry_policy = RetryPolicy()
            timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                return ExecuteResult(name="ok", success=True)

            async def rollback(self) -> None:
                raise AssertionError("should not rollback success")

        # Rollback raises but is suppressed -> no exception
        await RollbackManager.handle(_Success(), [])  # type: ignore[arg-type]


# ── ActionRunner integration tests ───────────────────────────


class TestActionRunnerIntegration:
    @pytest.mark.anyio

    async def test_simple_execution_all_succeed(self):
        """All executables succeed -> all results returned."""
        from arf.action_runner.runner import ActionRunner

        results = await ActionRunner.execute(
            [_make_exec("a"), _make_exec("b"), _make_exec("c")]
        )
        assert len(results) == 3
        assert all(r.success for r in results)


    @pytest.mark.anyio


    async def test_dependency_order_enforced(self):
        """b depends on a -> a executes before b."""
        from arf.action_runner.runner import ActionRunner

        order: list[str] = []

        class _Ordered:
            def __init__(self, name: str, deps: list[str] | None = None):
                self.name = name
                self.kind = "tool"
                self.dependencies = deps or []
                self.resources: list[str] = []
                self.side_effect = False
                self.retry_policy = RetryPolicy()
                self.timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                order.append(self.name)
                return ExecuteResult(name=self.name, success=True)

            async def rollback(self) -> None:
                pass

        await ActionRunner.execute(
            [_Ordered("b", deps=["a"]), _Ordered("a")]
        )
        assert order.index("a") < order.index("b")


    @pytest.mark.anyio


    async def test_rollback_on_failure_cancels_downstream(self):
        """a fails -> a gets rollback, b (depends on a) is cancelled."""
        from arf.action_runner.runner import ActionRunner

        rolled: list[str] = []
        executed: list[str] = []

        class _CustomExec:
            def __init__(self, name: str, deps: list[str] | None = None):
                self.name = name
                self.kind = "tool"
                self.dependencies = deps or []
                self.resources: list[str] = []
                self.side_effect = False
                self.retry_policy = RetryPolicy()
                self.timeout: float | None = None

            async def execute(self) -> ExecuteResult:
                executed.append(self.name)
                if self.name == "a":
                    return ExecuteResult(
                        name="a",
                        success=False,
                        error=ExecutionError(kind="deterministic", message="fail"),
                    )
                return ExecuteResult(name=self.name, success=True)

            async def rollback(self) -> None:
                rolled.append(self.name)

        results = await ActionRunner.execute(
            [_CustomExec("a"), _CustomExec("b", deps=["a"])]
        )
        assert rolled == ["a"]
        assert "b" not in executed  # cancelled, never executed
        result_map = {r.name: r for r in results}
        assert result_map["a"].success is False
        assert result_map["b"].success is False  # cancelled


    @pytest.mark.anyio


    async def test_parallel_within_wave(self):
        """No dependencies -> all execute in parallel (same wave)."""
        from arf.action_runner.runner import ActionRunner

        start = time.monotonic()
        results = await ActionRunner.execute(
            [_make_exec("a"), _make_exec("b"), _make_exec("c")]
        )
        elapsed = time.monotonic() - start
        assert len(results) == 3
        assert all(r.success for r in results)
        assert elapsed < 1.0  # generous upper bound for parallel execution
