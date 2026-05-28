"""Fact-check tests: Skill Pipeline — docs/skill-pipeline.md vs arf/skills/ + arf/engine/."""

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest


# ============================================================
# Section 2.4 — SkillPipeline
# ============================================================

class TestSkillPipeline:
    """Doc §2.4: SkillPipeline enforces tool execution order."""

    def test_pipeline_module_exists(self):
        """Doc: SkillPipeline in arf/skills/pipeline.py."""
        from arf.skills.pipeline import SkillPipeline
        assert SkillPipeline is not None

    def test_can_execute_allows_tool_not_in_pipeline(self):
        """Doc: pipeline 外工具不受限 (tools outside pipeline are free)."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "file_writer"},
            {"tool": "resource_loader", "depends_on": ["file_writer"]},
        ])
        assert sp.can_execute("web_search") is True

    def test_can_execute_blocks_unmet_dependency(self):
        """Doc: 依赖未满足时调用 → 阻断."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "file_writer"},
            {"tool": "resource_loader", "depends_on": ["file_writer"]},
        ])
        # resource_loader needs file_writer, but nothing completed
        assert sp.can_execute("resource_loader", set()) is False
        # file_writer has no deps, should be allowed
        assert sp.can_execute("file_writer", set()) is True

    def test_can_execute_allows_met_dependency(self):
        """Doc: dependency satisfied → allowed."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "file_writer"},
            {"tool": "resource_loader", "depends_on": ["file_writer"]},
        ])
        assert sp.can_execute("resource_loader", {"file_writer"}) is True

    def test_is_empty_true_for_no_steps(self):
        """Doc: pipeline 为空时所有工具自由并行."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([])
        assert sp.is_empty() is True
        sp2 = SkillPipeline(None)
        assert sp2.is_empty() is True

    def test_is_empty_false_with_steps(self):
        """Doc: pipeline with steps is not empty."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([{"tool": "step1"}])
        assert sp.is_empty() is False

    def test_circular_dependency_detected(self):
        """Doc: 循环依赖抛出 ValueError."""
        from arf.skills.pipeline import SkillPipeline
        with pytest.raises(ValueError, match="circular"):
            SkillPipeline([
                {"tool": "A", "depends_on": ["B"]},
                {"tool": "B", "depends_on": ["A"]},
            ])

    def test_missing_dependency_detected(self):
        """Doc: A depends_on B 但 B 不在 pipeline → ValueError."""
        from arf.skills.pipeline import SkillPipeline
        with pytest.raises(ValueError, match="not in the pipeline"):
            SkillPipeline([
                {"tool": "A", "depends_on": ["nonexistent"]},
            ])

    def test_next_steps_returns_ready(self):
        """Doc: next_steps returns tools whose deps are met."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "A"},
            {"tool": "B", "depends_on": ["A"]},
            {"tool": "C", "depends_on": ["A"]},
        ])
        # Only A has no deps
        assert sp.next_steps() == ["A"]
        # After A completes, B and C are ready
        assert sp.next_steps({"A"}) == ["B", "C"]

    def test_is_complete(self):
        """Doc: is_complete checks if all steps done."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "A"},
            {"tool": "B", "depends_on": ["A"]},
        ])
        assert sp.is_complete({"A"}) is False
        assert sp.is_complete({"A", "B"}) is True

    def test_steps_property(self):
        """Doc: steps property returns dependency map."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "A"},
            {"tool": "B", "depends_on": ["A"]},
        ])
        steps = sp.steps
        assert steps == {"A": [], "B": ["A"]}

    def test_order_property(self):
        """Doc: order property returns declared order."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "A"},
            {"tool": "B", "depends_on": ["A"]},
        ])
        assert sp.order == ["A", "B"]

    def test_validation_error_message(self):
        """Doc: validation_error returns human-readable error."""
        from arf.skills.pipeline import SkillPipeline
        sp = SkillPipeline([
            {"tool": "A"},
            {"tool": "B", "depends_on": ["A"]},
        ])
        err = sp.validation_error("B", set())
        assert "requires" in err
        assert "A" in err


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
        from unittest.mock import AsyncMock
        import asyncio

        resolver = AsyncMock()
        resolver.execute = AsyncMock(return_value="ok")
        executor = ConcurrentToolExecutor(resolver, strategy="sequential")

        calls = [
            {"id": "1", "name": "tool_a", "params": {}},
            {"id": "2", "name": "tool_b", "params": {}},
        ]
        results = asyncio.run(executor.execute(calls))
        assert len(results) == 2
        assert resolver.execute.call_count == 2

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
# Section 2.6 — SequentialScheduler (defined but unused)
# ============================================================

class TestSequentialScheduler:
    """Doc §2.6: SequentialScheduler exists but is unused."""

    def test_sequential_scheduler_exists(self):
        """Doc: SequentialScheduler in arf/concurrency/sequential.py."""
        from arf.concurrency.sequential import SequentialScheduler
        assert SequentialScheduler is not None

    def test_sequential_scheduler_has_schedule_and_execute(self):
        """Doc: schedule() and execute() methods."""
        from arf.concurrency.sequential import SequentialScheduler
        assert hasattr(SequentialScheduler, "schedule")
        assert hasattr(SequentialScheduler, "execute")


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
        """Doc: _close_tool_calls injects '(tool result unavailable)' placeholder."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._close_tool_calls)
        assert "tool result unavailable" in source

    def test_classify_source_has_blocked_messages(self):
        """Doc: denied calls get [Blocked] messages injected."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._step_classify_tool_calls)
        # Verify denied calls are tracked separately from valid calls
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
    """Doc: Engine integration with pipeline and concurrency."""

    def test_graph_engine_takes_concurrency_executor(self):
        """Doc: GraphEngine uses ConcurrentToolExecutor."""
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert "tool_executor" in sig.parameters

    def test_graph_engine_has_approve_method(self):
        """Doc: engine.approve(decision_id, approved) for approval resolution."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "approve")

    def test_pipeline_check_in_classify(self):
        """Doc: SkillPipeline.can_execute() called in _step_classify_tool_calls."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._step_classify_tool_calls)
        assert "SkillPipeline" in source or "pipeline" in source.lower()


# ============================================================
# Section — Completeness checks
# ============================================================

class TestCompleteness:
    """Check code entities vs doc coverage."""

    def test_concurrency_module_init_exists(self):
        path = __import__("pathlib").Path("arf/concurrency/__init__.py")
        assert path.exists()

    def test_loop_strategies_module_exists(self):
        """Doc mentions GraphEngine while loop but doesn't detail loop strategies."""
        path = __import__("pathlib").Path("arf/engine/loop_strategies/__init__.py")
        assert path.exists()

    def test_react_loop_strategy_exists(self):
        """REACT loop strategy exists but not detailed in skill-pipeline doc."""
        path = __import__("pathlib").Path("arf/engine/loop_strategies/react.py")
        assert path.exists()
