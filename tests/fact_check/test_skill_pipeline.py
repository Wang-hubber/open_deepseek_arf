"""Fact-check tests: ActionRunner & Promotion — docs vs arf/action_runner/ + arf/promotion/."""

import asyncio
import inspect

import pytest


# ============================================================
# Section 2.4 — DependencyResolver (was SkillPipeline)
# ============================================================

class TestDependencyResolver:
    """Doc: DependencyResolver replaces SkillPipeline for execution ordering."""

    def test_resolver_module_exists(self):
        """Doc: DependencyResolver in arf/action_runner/resolver.py."""
        from arf.action_runner.resolver import DependencyResolver
        assert DependencyResolver is not None

    def test_resolve_linear_chain(self):
        """Doc: a -> b -> c produces three waves."""
        from arf.action_runner.resolver import DependencyResolver
        from arf.core.execution import Executable, ExecuteResult, RetryPolicy

        class _E:
            def __init__(self, name, deps=None):
                self.name = name; self.kind = "tool"
                self.dependencies = deps or []; self.resources = []
                self.side_effect = False; self.retry_policy = RetryPolicy()
                self.timeout = None
            async def execute(self): return ExecuteResult(name=self.name, success=True)
            async def rollback(self): pass

        waves = DependencyResolver.resolve([
            _E("c", deps=["b"]), _E("a"), _E("b", deps=["a"]),
        ])
        assert len(waves) == 3
        assert waves[0].executables[0].name == "a"
        assert waves[1].executables[0].name == "b"
        assert waves[2].executables[0].name == "c"

    def test_diamond_dependency(self):
        """Doc: a -> [b, c] -> d produces three waves."""
        from arf.action_runner.resolver import DependencyResolver
        from arf.core.execution import Executable, ExecuteResult, RetryPolicy

        class _E:
            def __init__(self, name, deps=None):
                self.name = name; self.kind = "tool"
                self.dependencies = deps or []; self.resources = []
                self.side_effect = False; self.retry_policy = RetryPolicy()
                self.timeout = None
            async def execute(self): return ExecuteResult(name=self.name, success=True)
            async def rollback(self): pass

        waves = DependencyResolver.resolve([
            _E("d", deps=["b", "c"]), _E("c", deps=["a"]),
            _E("b", deps=["a"]), _E("a"),
        ])
        assert len(waves) == 3
        assert {e.name for e in waves[1].executables} == {"b", "c"}

    def test_circular_dependency_detected(self):
        """Doc: circular dependency raises ValueError."""
        from arf.action_runner.resolver import DependencyResolver
        from arf.core.execution import Executable, ExecuteResult, RetryPolicy

        class _E:
            def __init__(self, name, deps=None):
                self.name = name; self.kind = "tool"
                self.dependencies = deps or []; self.resources = []
                self.side_effect = False; self.retry_policy = RetryPolicy()
                self.timeout = None
            async def execute(self): return ExecuteResult(name=self.name, success=True)
            async def rollback(self): pass

        with pytest.raises(ValueError, match="(?i)circular"):
            DependencyResolver.resolve([
                _E("a", deps=["b"]), _E("b", deps=["a"]),
            ])

    def test_missing_dependency_detected(self):
        """Doc: missing dependency raises ValueError."""
        from arf.action_runner.resolver import DependencyResolver
        from arf.core.execution import Executable, ExecuteResult, RetryPolicy

        class _E:
            def __init__(self, name, deps=None):
                self.name = name; self.kind = "tool"
                self.dependencies = deps or []; self.resources = []
                self.side_effect = False; self.retry_policy = RetryPolicy()
                self.timeout = None
            async def execute(self): return ExecuteResult(name=self.name, success=True)
            async def rollback(self): pass

        with pytest.raises(ValueError, match="missing"):
            DependencyResolver.resolve([
                _E("a", deps=["nonexistent"]),
            ])

    def test_empty_input(self):
        """Doc: empty input returns empty waves."""
        from arf.action_runner.resolver import DependencyResolver
        assert DependencyResolver.resolve([]) == []


# ============================================================
# Section 2.2 — ConcurrentToolExecutor
# ============================================================

class TestConcurrentToolExecutor:
    """Doc §2.2: ConcurrentToolExecutor — parallel/sequential tool execution."""

    def test_executor_exists(self):
        """Doc: ConcurrentToolExecutor in arf/engine/tool_executor.py."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        assert ConcurrentToolExecutor is not None

    def test_constructor_params(self):
        """Doc: __init__(tool_resolver, strategy="parallel", max_concurrency=5)."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        sig = inspect.signature(ConcurrentToolExecutor.__init__)
        params = list(sig.parameters.keys())
        assert "tool_resolver" in params
        assert "strategy" in params
        assert "max_concurrency" in params

    def test_default_strategy_is_parallel(self):
        """Doc: default strategy = "parallel"."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        sig = inspect.signature(ConcurrentToolExecutor.__init__)
        assert sig.parameters["strategy"].default == "parallel"

    def test_default_max_concurrency_is_5(self):
        """Doc: default max_concurrency = 5."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        sig = inspect.signature(ConcurrentToolExecutor.__init__)
        assert sig.parameters["max_concurrency"].default == 5

    def test_sequential_mode(self):
        """Doc: strategy="sequential" executes one at a time."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        from arf.testing import InMemoryToolResolver
        import asyncio

        resolver = InMemoryToolResolver()
        executor = ConcurrentToolExecutor(resolver, strategy="sequential")

        calls = [
            {"id": "1", "name": "tool_a", "params": {}},
            {"id": "2", "name": "tool_b", "params": {}},
        ]
        results = asyncio.run(executor.execute(calls))
        assert len(results) == 2
        assert len(resolver.calls) == 2

    def test_parallel_mode_uses_semaphore(self):
        """Doc: parallel mode uses asyncio.Semaphore for concurrency limit."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        source = inspect.getsource(ConcurrentToolExecutor.execute)
        assert "Semaphore" in source
        assert "asyncio.gather" in source

    def test_execute_accepts_agent_mode_and_engine(self):
        """Doc: execute() passes _agent_mode, _engine, _state_store to tools."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        sig = inspect.signature(ConcurrentToolExecutor.execute)
        params = list(sig.parameters.keys())
        assert "agent_mode" in params
        assert "engine" in params
        assert "state_store" in params


# ============================================================
# Section 2.1 — Concurrency model
# ============================================================

class TestConcurrencyConfig:
    """Doc §2.5: ConcurrencyConfig for max concurrency."""

    def test_concurrency_config_exists(self):
        """Doc: ConcurrencyConfig in config_base."""
        from arf.core.config_base import ConcurrencyConfig
        cfg = ConcurrencyConfig()
        assert cfg.strategy == "parallel"
        assert cfg.max_concurrency == 5

    def test_concurrency_config_fields(self):
        """Doc: strategy (parallel|sequential), max_concurrency (default 5, >=1)."""
        from arf.core.config_base import ConcurrencyConfig
        field_s = ConcurrencyConfig.model_fields["strategy"]
        field_m = ConcurrencyConfig.model_fields["max_concurrency"]
        assert field_s.default == "parallel"
        assert field_m.default == 5


# ============================================================
# Section 2.6 — ResourceScheduler (was SequentialScheduler)
# ============================================================

class TestResourceScheduler:
    """Doc: ResourceScheduler replaces SequentialScheduler for resource-aware scheduling."""

    def test_resource_scheduler_exists(self):
        """Doc: ResourceScheduler in arf/action_runner/scheduler.py."""
        from arf.action_runner.scheduler import ResourceScheduler
        assert ResourceScheduler is not None

    def test_resource_scheduler_has_schedule(self):
        """Doc: schedule() method for resource conflict detection."""
        from arf.action_runner.scheduler import ResourceScheduler
        assert hasattr(ResourceScheduler, "schedule")


# ============================================================
# Section 2.8 — Tool Call Closure
# ============================================================

class TestToolCallClosure:
    """Doc §2.8: GraphEngine injects synthetic [Blocked] results for denied calls."""

    def test_close_tool_calls_method_exists(self):
        """Doc: _close_tool_calls() method for orphaned tool calls."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_close_tool_calls")

    def test_step_classify_method_exists(self):
        """Doc: _step_classify_tool_calls for guard/permission/approval."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_step_classify_tool_calls")

    def test_close_tool_calls_source(self):
        """Doc: _repair_messages injects '(tool result unavailable)' placeholder."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._repair_messages)
        assert "tool result unavailable" in source

    def test_classify_source_has_blocked_messages(self):
        """Doc: denied calls get [Blocked] messages injected."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._step_classify_tool_calls)
        assert "denied_calls" in source
        assert "valid_calls" in source


# ============================================================
# Section 2.3 — Hook parallel firing
# ============================================================

class TestHookParallelism:
    """Doc §2.3: SubprocessHookRunner fires hooks in parallel."""

    def test_hook_runner_exists(self):
        """Doc: SubprocessHookRunner in arf/hooks/runner.py."""
        from arf.hooks.runner import SubprocessHookRunner
        assert SubprocessHookRunner is not None


# ============================================================
# Section — GraphEngine key paths
# ============================================================

class TestGraphEngineIntegration:
    """Doc: Engine integration with Promotion and ActionRunner."""

    def test_graph_engine_takes_concurrency_executor(self):
        """Doc: GraphEngine uses ConcurrentToolExecutor."""
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert "tool_executor" in sig.parameters

    def test_graph_engine_has_approve_method(self):
        """Doc: engine.approve(decision_id, approved) for approval resolution."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "approve")

    def test_graph_engine_takes_promotion_and_action_runner(self):
        """Doc: GraphEngine accepts promotion and action_runner parameters."""
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert "promotion" in sig.parameters
        assert "action_runner" in sig.parameters

    def test_pipeline_check_in_classify(self):
        """Doc: pipeline dependency check in _step_classify_tool_calls."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._step_classify_tool_calls)
        assert "pipeline" in source.lower()


# ============================================================
# Section — Completeness checks
# ============================================================

class TestCompleteness:
    """Check code entities vs doc coverage."""

    def test_concurrency_module_init_exists(self):
        path = __import__("pathlib").Path("arf/concurrency/__init__.py")
        assert path.exists()

    def test_action_runner_module_exists(self):
        """Doc: ActionRunner in arf/action_runner/."""
        path = __import__("pathlib").Path("arf/action_runner/__init__.py")
        assert path.exists()

    def test_promotion_module_exists(self):
        """Doc: Promotion in arf/promotion/."""
        path = __import__("pathlib").Path("arf/promotion/__init__.py")
        assert path.exists()

    def test_loop_strategies_module_exists(self):
        """Doc mentions GraphEngine while loop but doesn't detail loop strategies."""
        path = __import__("pathlib").Path("arf/engine/loop_strategies/__init__.py")
        assert path.exists()

    def test_react_loop_strategy_exists(self):
        """REACT loop strategy exists but not detailed in skill-pipeline doc."""
        path = __import__("pathlib").Path("arf/engine/loop_strategies/react.py")
        assert path.exists()
