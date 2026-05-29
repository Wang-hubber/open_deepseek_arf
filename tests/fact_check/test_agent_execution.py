"""Fact-check tests: Agent Execution Domain — docs/agent-execution.md vs arf/engine/ + arf/agent/.

Each test validates a specific claim made in the documentation against actual code.
PASS = doc/code consistent. FAIL = discrepancy found (fact-check finding).
"""

import asyncio
import inspect
import tempfile
from pathlib import Path

import pytest

from arf.core.state import AgentState
from tests.test_agent_execution import _build_real_engine, CountingStateStore
from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse


# ---------------------------------------------------------------------------
# 2.1 Session / Round / Turn 边界
# ---------------------------------------------------------------------------

class TestSessionRoundTurn:
    """Doc §2.1: Session/Round/Turn 三层嵌套边界."""

    def test_graph_engine_has_max_turns_default_50(self):
        """Doc: max_turns 默认 50."""
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert sig.parameters["max_turns"].default == 50

    def test_graph_engine_has_max_undo_depth_default_3(self):
        """Doc: max_undo_depth 默认 3."""
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert sig.parameters["max_undo_depth"].default == 3

    def test_cancel_event_parameter_exists(self):
        """Doc: cancel_event (asyncio.Event) 注入."""
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert "cancel_event" in sig.parameters

    def test_set_cancel_event_late_binding(self):
        """Doc: cancel_event 通过 set_cancel_event() 注入（延迟绑定）."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "set_cancel_event")
        sig = inspect.signature(GraphEngine.set_cancel_event)
        assert "event" in sig.parameters


# ---------------------------------------------------------------------------
# 2.3 状态机
# ---------------------------------------------------------------------------

class TestStateMachine:
    """Doc §2.3: Session/Round/Turn 状态转移."""

    def test_session_active_sessions_managed(self):
        """Doc: Session 状态 [INACTIVE] → [ACTIVE] → [CLOSED], BaseAgent tracks via _active_sessions."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "_active_sessions" in source

    def test_chat_resets_turn_to_zero(self):
        """Doc: current_turn 每 round 复位到 0 (BaseAgent.chat/astream)."""
        import ast
        base_path = Path("arf/agent/base.py")
        source = base_path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat":
                source_lines = source.split("\n")
                found_turn_zero = False
                for lineno in range(node.lineno, node.end_lineno + 1):
                    line = source_lines[lineno - 1].strip()
                    if "turn" in line and "= 0" in line and "reset" in line:
                        found_turn_zero = True
                        break
                assert found_turn_zero, "chat() should have turn = 0 reset comment"

    def test_current_turn_field_exists_in_agent_state(self):
        """Doc: Turn 计数器 current_turn: int."""
        state: AgentState = {"current_turn": 0}
        assert "current_turn" in state


# ---------------------------------------------------------------------------
# 2.4 双模主循环
# ---------------------------------------------------------------------------

class TestInvokeAstream:
    """Doc §2.4: invoke / astream 共享 Agent Loop."""

    def test_graph_engine_has_invoke(self):
        """Doc: GraphEngine.invoke() 返回 AgentState."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "invoke")
        sig = inspect.signature(GraphEngine.invoke)
        assert "state" in sig.parameters

    def test_graph_engine_has_astream(self):
        """Doc: GraphEngine.astream() 返回 AsyncGenerator[AgentEvent]."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "astream")
        sig = inspect.signature(GraphEngine.astream)
        assert "state" in sig.parameters

    def test_close_tool_calls_exists(self):
        """Doc: _close_tool_calls 消息序列完整性保证."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_close_tool_calls")

    def test_active_config_exists(self):
        """Doc: _active_config 多 Agent 配置解析."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_active_config")

    def test_step_classify_tool_calls_exists(self):
        """Doc: _step_classify_tool_calls Guard + Pipeline + Permission + Approval."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_step_classify_tool_calls")

    def test_invoke_calls_close_tool_calls_at_entry(self):
        """Doc: invoke() 入口调用 _close_tool_calls()."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine.invoke)
        assert "_close_tool_calls(state)" in source

    def test_astream_calls_close_tool_calls_at_entry(self):
        """Doc: astream() 入口调用 _close_tool_calls()."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine.astream)
        assert "_close_tool_calls(state)" in source

    def test_both_invoke_and_astream_call_next_step(self):
        """Doc: 两条路径共享 LoopStrategy.next_step() 分派."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        invoke_src = _inspect.getsource(GraphEngine.invoke)
        astream_src = _inspect.getsource(GraphEngine.astream)
        assert "next_step(state)" in invoke_src
        assert "next_step(state)" in astream_src


# ---------------------------------------------------------------------------
# 2.5 循环策略
# ---------------------------------------------------------------------------

class TestLoopStrategy:
    """Doc §2.5: LoopStrategy Protocol + ReActStrategy."""

    def test_protocol_has_three_methods(self):
        """Doc: LoopStrategy 协议: should_continue, should_break, next_step."""
        from arf.core.protocols.engine import LoopStrategy
        assert hasattr(LoopStrategy, "should_continue")
        assert hasattr(LoopStrategy, "should_break")
        assert hasattr(LoopStrategy, "next_step")

    def test_react_strategy_default_max_turns_50(self):
        """Doc: ReActStrategy(max_turns=50)."""
        from arf.engine.loop_strategies.react import ReActStrategy
        sig = inspect.signature(ReActStrategy.__init__)
        assert sig.parameters["max_turns"].default == 50

    def test_react_strategy_is_only_implementation(self):
        """Doc: ReActStrategy 是当前唯一实现."""
        plan_execute_path = Path("arf/engine/loop_strategies/plan_execute.py")
        assert not plan_execute_path.exists(), "plan_execute strategy not implemented"

    def test_should_continue_false_when_turn_exceeds_max(self):
        """Doc: should_continue — Entry gate: current_turn >= max_turns → False."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy(max_turns=3)
        assert strategy.should_continue({"current_turn": 0}) is True
        assert strategy.should_continue({"current_turn": 2}) is True
        assert strategy.should_continue({"current_turn": 3}) is False
        assert strategy.should_continue({"current_turn": 99}) is False

    def test_should_break_true_when_turn_exceeds_max(self):
        """Doc: should_break — Exit gate: current_turn >= max_turns → True."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy(max_turns=3)
        assert strategy.should_break({"current_turn": 0}) is False
        assert strategy.should_break({"current_turn": 2}) is False
        assert strategy.should_break({"current_turn": 3}) is True
        assert strategy.should_break({"current_turn": 99}) is True

    def test_should_continue_default_current_turn_zero(self):
        """Doc: should_continue 默认 current_turn 为 0."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy(max_turns=1)
        assert strategy.should_continue({}) is True

    def test_should_break_default_current_turn_zero(self):
        """Doc: should_break 默认 current_turn 为 0."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy(max_turns=1)
        assert strategy.should_break({}) is False

    # ---- next_step dispatch ----

    def test_next_step_empty_messages_returns_call_model(self):
        """Doc: next_step: 无消息 → call_model."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy()
        assert strategy.next_step({"messages": []}) == "call_model"
        assert strategy.next_step({}) == "call_model"

    def test_next_step_user_message_returns_call_model(self):
        """Doc: next_step: user/system → call_model."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy()
        assert strategy.next_step({"messages": [{"role": "user", "content": "hi"}]}) == "call_model"

    def test_next_step_system_message_returns_call_model(self):
        """Doc: next_step: system → call_model."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy()
        assert strategy.next_step({"messages": [{"role": "system", "content": "ctx"}]}) == "call_model"

    def test_next_step_assistant_with_tool_calls_returns_execute_tools(self):
        """Doc: next_step: assistant+tool_calls → execute_tools."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy()
        state = {"messages": [{"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}]}
        assert strategy.next_step(state) == "execute_tools"

    def test_next_step_tool_result_returns_call_model(self):
        """Doc: next_step: tool result → call_model (observe→think)."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy()
        assert strategy.next_step({"messages": [{"role": "tool", "content": "result"}]}) == "call_model"

    def test_next_step_assistant_text_only_returns_call_model(self):
        """Doc: next_step: assistant 纯文本 → call_model."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy()
        assert strategy.next_step({"messages": [{"role": "assistant", "content": "hello"}]}) == "call_model"

    # ---- L4: 边界行为 ----

    def test_max_turns_boundary_turn_49_allowed_50_blocked(self):
        """Doc: max_turns=50, turn 0-49 继续, turn 50 阻断."""
        from arf.engine.loop_strategies.react import ReActStrategy
        strategy = ReActStrategy(max_turns=50)
        assert strategy.should_continue({"current_turn": 49}) is True
        assert strategy.should_break({"current_turn": 49}) is False
        assert strategy.should_continue({"current_turn": 50}) is False
        assert strategy.should_break({"current_turn": 50}) is True

    def test_loop_termination_four_paths_exist(self):
        """Doc: 四个独立终止条件: should_continue=False, text-only break, should_break=True, _cancelled."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        invoke_src = _inspect.getsource(GraphEngine.invoke)
        astream_src = _inspect.getsource(GraphEngine.astream)
        assert "should_continue" in invoke_src
        assert "break" in invoke_src
        assert "should_break" in invoke_src
        assert "_cancelled" in invoke_src
        assert "should_continue" in astream_src
        assert "break" in astream_src
        assert "should_break" in astream_src
        assert "_cancelled" in astream_src


# ---------------------------------------------------------------------------
# 2.6 取消机制
# ---------------------------------------------------------------------------

class TestCancelMechanism:
    """Doc §2.6: asyncio.Event 取消信号."""

    def test_cancelled_method_exists(self):
        """Doc: _cancelled() 非阻塞检测."""
        from arf.engine.graph import GraphEngine
        assert hasattr(GraphEngine, "_cancelled")

    def test_cancelled_returns_false_when_no_event(self):
        """Doc: _cancelled 在无 cancel_event 时返回 False."""
        engine = _build_real_engine(fake_model=FakeModelAdapter())
        assert engine._cancelled() is False

    def test_cancelled_returns_false_when_event_not_set(self):
        """Doc: cancel_event 存在但未 set → False."""
        event = asyncio.Event()
        engine = _build_real_engine(fake_model=FakeModelAdapter(), cancel_event=event)
        assert engine._cancelled() is False

    def test_cancelled_returns_true_when_event_set(self):
        """Doc: cancel_event.set() → _cancelled() True."""
        event = asyncio.Event()
        event.set()
        engine = _build_real_engine(fake_model=FakeModelAdapter(), cancel_event=event)
        assert engine._cancelled() is True

    def test_set_cancel_event_late_binding_works(self):
        """Doc: set_cancel_event 延迟绑定后 _cancelled() 反映新状态."""
        engine = _build_real_engine(fake_model=FakeModelAdapter())
        assert engine._cancelled() is False
        event = asyncio.Event()
        engine.set_cancel_event(event)
        assert engine._cancelled() is False
        event.set()
        assert engine._cancelled() is True


# ---------------------------------------------------------------------------
# 2.7 会话断路器
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Doc §2.7: max_turns 会话断路器."""

    def test_active_config_returns_max_turns_for_main_agent(self):
        """Doc: _active_config 返回主 Agent 的 max_turns."""
        engine = _build_real_engine(
            fake_model=FakeModelAdapter(),
            max_turns=42,
        )
        state: AgentState = {"active_agent": ""}
        cfg = engine._active_config(state)
        assert cfg["max_turns"] == 42

    def test_active_config_returns_sub_agent_max_turns(self):
        """Doc: 多 Agent 场景: _active_config 返回子 Agent max_turns."""
        from arf.agent.config import AgentConfig, AdvancedConfig

        sub_cfg = AgentConfig(name="agent_b", advanced=AdvancedConfig(max_turns=25))

        engine = _build_real_engine(
            fake_model=FakeModelAdapter(),
            sub_agent_configs={
                "agent_b": {
                    "config": sub_cfg,
                    "system_prompt": "You are agent B",
                    "adapters": {},
                }
            },
        )
        state: AgentState = {"active_agent": "agent_b"}
        cfg = engine._active_config(state)
        assert cfg["max_turns"] == 25


# ---------------------------------------------------------------------------
# 2.8 工具执行
# ---------------------------------------------------------------------------

class TestToolExecution:
    """Doc §2.8: ConcurrentToolExecutor + 工具守卫流水线."""

    def test_concurrent_tool_executor_exists(self):
        """Doc: ConcurrentToolExecutor 在 arf/engine/tool_executor.py."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        assert ConcurrentToolExecutor is not None

    def test_concurrent_tool_executor_defaults(self):
        """Doc: 默认 strategy='parallel', max_concurrency=5."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        sig = inspect.signature(ConcurrentToolExecutor.__init__)
        assert sig.parameters["strategy"].default == "parallel"
        assert sig.parameters["max_concurrency"].default == 5

    def test_executor_injects_agent_mode_engine_state_store(self):
        """Doc: 工具 params 自动注入 _agent_mode, _engine, _state_store."""
        from arf.engine.tool_executor import ConcurrentToolExecutor
        sig = inspect.signature(ConcurrentToolExecutor.execute)
        assert "agent_mode" in sig.parameters
        assert "engine" in sig.parameters
        assert "state_store" in sig.parameters

    def test_approval_timeout_configurable(self):
        """Doc: Approval timeout is configurable via agent.yaml (human_loop.timeout)."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine._step_classify_tool_calls)
        assert "self.approval_timeout" in source

    def test_blocked_tool_injects_blocked_reason(self):
        """Doc: 被拒工具注入 [Blocked] reason."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine.invoke)
        assert "[Blocked]" in source


# ---------------------------------------------------------------------------
# 2.9 BaseAgent 装配
# ---------------------------------------------------------------------------

class TestBaseAgentAssembly:
    """Doc §2.9: BaseAgent 10步装配."""

    def test_base_agent_exists(self):
        """Doc: BaseAgent 在 arf/agent/base.py."""
        from arf.agent.base import BaseAgent
        assert BaseAgent is not None

    def test_base_agent_accepts_config_and_override_protocols(self):
        """Doc: BaseAgent.__init__(config, app_context, **override_protocols)."""
        from arf.agent.base import BaseAgent
        sig = inspect.signature(BaseAgent.__init__)
        params = list(sig.parameters.keys())
        assert "config" in params
        assert "override_protocols" in params

    def test_assembly_includes_event_bus(self):
        """Doc: 装配步骤1 — EventBus (InMemoryEventBus)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "event_bus" in source

    def test_assembly_includes_state_store(self):
        """Doc: 装配步骤2 — StateStore."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "state_store" in source

    def test_assembly_includes_guard_runner(self):
        """Doc: 装配步骤6 — Guardrails (DefaultGuardRunner)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "guard_runner" in source

    def test_assembly_includes_error_policy(self):
        """Doc: 装配步骤7 — ErrorPolicy (tool_retry=2, model_5xx=fallback)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "error_policy" in source

    def test_assembly_includes_hook_runner(self):
        """Doc: 装配步骤8 — SubprocessHookRunner."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "hook_runner" in source

    def test_assembly_includes_tool_executor(self):
        """Doc: 装配步骤9 — ConcurrentToolExecutor."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "tool_executor" in source

    def test_assembly_includes_loop_strategy(self):
        """Doc: 装配步骤10 — ReActStrategy(max_turns=50)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "loop_strategy" in source

    def test_planner_protocol_defined(self):
        """Doc: Planner 协议已定义但引擎侧尚未集成."""
        from arf.core.protocols.engine import Planner
        assert hasattr(Planner, "generate_plan")
        assert hasattr(Planner, "update_progress")
        assert hasattr(Planner, "detect_divergence")
        assert hasattr(Planner, "revise")

    def test_plan_execute_strategy_not_implemented(self):
        """Doc: plan_execute 循环策略未实现."""
        path = Path("arf/engine/loop_strategies/plan_execute.py")
        assert not path.exists(), "PlanExecute strategy not yet implemented"

    def test_planner_plugin_is_skeleton(self):
        """Doc: arf/plugins/planner/ 只有 skills/ 和 tools/ 骨架，无完整集成."""
        plugin_dir = Path("arf/plugins/planner")
        assert (plugin_dir / "skills" / "plan_execute.yaml").exists()
        assert (plugin_dir / "tools" / "planner" / "function.py").exists()
        assert (plugin_dir / "tools" / "planner" / "tool.yaml").exists()
        # No __init__.py strategy wiring at plugin root — skeleton only
        init_file = plugin_dir / "__init__.py"
        strategy_file = plugin_dir / "strategy.py"
        assert not (init_file.exists() or strategy_file.exists()), (
            "Planner plugin is skeleton only"
        )


# ---------------------------------------------------------------------------
# 2.10 Handoff
# ---------------------------------------------------------------------------

class TestHandoff:
    """Doc §2.10: HandoffManager 多 Agent 切换."""

    def test_handoff_manager_exists(self):
        """Doc: HandoffManager 在 arf/engine/handoff.py."""
        from arf.engine.handoff import HandoffManager
        assert HandoffManager is not None

    def test_handoff_detect_method_exists(self):
        """Doc: detect() 扫描 tool_results 寻找 handoff 信号."""
        from arf.engine.handoff import HandoffManager
        assert hasattr(HandoffManager, "detect")

    def test_handoff_resolve_method_exists(self):
        """Doc: resolve() 解析目标 Agent."""
        from arf.engine.handoff import HandoffManager
        assert hasattr(HandoffManager, "resolve")

    def test_handoff_build_target_context_exists(self):
        """Doc: build_target_context() 构建初始上下文."""
        from arf.engine.handoff import HandoffManager
        assert hasattr(HandoffManager, "build_target_context")

    def test_execute_handoff_saves_current_state(self):
        """Doc: Forward 流程步骤1 — 保存当前 Agent 状态到 StateStore."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine._execute_handoff)
        assert "state_store.put" in source
        assert "session_id" in source

    def test_execute_handoff_resets_current_turn(self):
        """Doc: Forward 流程步骤6 — 重置 current_turn = 0."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine._execute_handoff)
        assert '"current_turn"] = 0' in source

    def test_restore_from_handoff_extracts_assistant_message(self):
        """Doc: Return 流程步骤1 — 提取子 Agent 最后一条 assistant 消息."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine._restore_from_handoff)
        # The source scans reversed messages for role == "assistant"
        assert 'role' in source
        assert 'assistant' in source
        assert 'result_content' in source

    def test_restore_from_handoff_pops_handoff_task(self):
        """Doc: Return 流程步骤5 — 清除 handoff_task 字段."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine._restore_from_handoff)
        assert 'handoff_task' in source


# ---------------------------------------------------------------------------
# 2.11 持久化与回滚
# ---------------------------------------------------------------------------

class TestPersistenceRollback:
    """Doc §2.11: StateStore + RoundManager 双持久化机制."""

    # --- StateStore ---

    def test_file_state_store_exists(self):
        """Doc: FileStateStore — JSON 文件持久化."""
        from arf.engine.checkpoint import FileStateStore
        assert FileStateStore is not None

    def test_in_memory_state_store_exists(self):
        """Doc: InMemoryStateStore — 测试 double."""
        from arf.engine.checkpoint import InMemoryStateStore
        assert InMemoryStateStore is not None

    def test_file_state_store_strips_tool_results_on_put(self):
        """Doc: StateStore.put() 调用 data.pop('tool_results', None)."""
        from arf.engine.checkpoint import FileStateStore

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                store = FileStateStore(tmp)
                state: AgentState = {
                    "session_id": "test",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tool_results": {"tc1": {"success": True}},
                    "current_turn": 1,
                }
                await store.put("test", state)
                restored = await store.get("test")
                assert restored is not None
                assert "tool_results" not in restored
                assert "messages" in restored

        asyncio.run(_run())

    def test_file_state_store_atomic_write(self):
        """Doc: FileStateStore 原子写入（tmp 文件 + rename）."""
        from arf.engine.checkpoint import FileStateStore
        import inspect as _inspect
        source = _inspect.getsource(FileStateStore.put)
        assert "tmp" in source
        assert "rename" in source

    def test_in_memory_state_store_records_snapshots(self):
        """Doc: InMemoryStateStore.snapshots 记录每次 put() 调用."""

        async def _run():
            from arf.engine.checkpoint import InMemoryStateStore
            store = InMemoryStateStore()
            store.reset()
            await store.put("s1", {"current_turn": 0, "messages": []})
            await store.put("s1", {"current_turn": 1, "messages": []})
            assert len(store.snapshots) == 2

        asyncio.run(_run())

    # --- RoundManager ---

    def test_round_manager_exists(self):
        """Doc: RoundManager 在 arf/engine/round_manager.py."""
        from arf.engine.round_manager import RoundManager
        assert RoundManager is not None

    def test_round_manager_default_max_undo_depth_3(self):
        """Doc: RoundManager(max_undo_depth=3) 默认."""
        from arf.engine.round_manager import RoundManager
        sig = inspect.signature(RoundManager.__init__)
        assert sig.parameters["max_undo_depth"].default == 3

    def test_round_manager_begin_round_deep_copies(self):
        """Doc: begin_round() 深拷贝 AgentState."""
        from arf.engine.round_manager import RoundManager
        import inspect as _inspect
        source = _inspect.getsource(RoundManager.begin_round)
        assert "deepcopy" in source

    def test_round_manager_record_handoff_no_new_checkpoint(self):
        """Doc: record_handoff 不创建新 checkpoint."""
        from arf.engine.round_manager import RoundManager
        import inspect as _inspect
        source = _inspect.getsource(RoundManager.record_handoff)
        assert "handoff_count" in source
        assert "begin_round" not in source

    def test_round_manager_undo_pops_n_rounds(self):
        """Doc: undo(steps) pop N 个 round."""
        from arf.engine.round_manager import RoundManager
        import inspect as _inspect
        source = _inspect.getsource(RoundManager.undo)
        assert "pop()" in source

    def test_round_manager_restore_from_disk_exists(self):
        """Doc: _restore_from_disk 支持跨进程重启后 undo."""
        from arf.engine.round_manager import RoundManager
        assert hasattr(RoundManager, "_restore_from_disk")

    def test_round_manager_deque_maxlen(self):
        """Doc: deque(maxlen=max_undo_depth) 自动淘汰最旧 round."""
        from arf.engine.round_manager import RoundManager
        import inspect as _inspect
        source = _inspect.getsource(RoundManager.__init__)
        assert "maxlen" in source

    # --- GraphEngine undo ---

    def test_graph_engine_undo_emits_event(self):
        """Doc: GraphEngine.undo() 额外 emit undo_executed 事件."""
        from arf.engine.graph import GraphEngine
        import inspect as _inspect
        source = _inspect.getsource(GraphEngine.undo)
        assert "undo_executed" in source
        assert "_emit" in source


# ---------------------------------------------------------------------------
# 2.12 Hook 系统
# ---------------------------------------------------------------------------

class TestHookSystem:
    """Doc §2.12: SubprocessHookRunner 生命周期钩子."""

    def test_subprocess_hook_runner_exists(self):
        """Doc: SubprocessHookRunner 在 arf/hooks/runner.py."""
        from arf.hooks.runner import SubprocessHookRunner
        assert SubprocessHookRunner is not None

    def test_hook_fire_uses_asyncio_gather(self):
        """Doc: 同事件类型的 hook 并行执行 (asyncio.gather)."""
        from arf.hooks.runner import SubprocessHookRunner
        import inspect as _inspect
        source = _inspect.getsource(SubprocessHookRunner.fire)
        assert "asyncio.gather" in source

    def test_hook_timeout_kills_subprocess(self):
        """Doc: 超时杀死子进程，返回 exit_code=-1."""
        from arf.hooks.runner import SubprocessHookRunner
        import inspect as _inspect
        source = _inspect.getsource(SubprocessHookRunner.fire)
        assert "proc.kill()" in source

    def test_hook_exit_code_2_injection(self):
        """Doc: 退出码 2 + stdout → injected_message."""
        from arf.hooks.runner import SubprocessHookRunner
        import inspect as _inspect
        source = _inspect.getsource(SubprocessHookRunner.fire)
        assert "injected_message" in source
        assert "rc == 2" in source

    def test_hook_set_order_exists(self):
        """Doc: set_order() 控制同事件类型的执行顺序."""
        from arf.hooks.runner import SubprocessHookRunner
        assert hasattr(SubprocessHookRunner, "set_order")
        sig = inspect.signature(SubprocessHookRunner.set_order)
        assert "event_type" in sig.parameters
        assert "hook_names" in sig.parameters

    def test_hook_env_var_template(self):
        """Doc: $ARF_{CONTEXT_KEY} 自动替换."""
        from arf.hooks.runner import SubprocessHookRunner
        import inspect as _inspect
        source = _inspect.getsource(SubprocessHookRunner.fire)
        assert "$ARF_" in source

    def test_eight_hook_event_types_in_config(self):
        """Doc: 8种 Hook 事件类型."""
        from arf.core.config_base import HookDefinition
        from typing import get_args
        expected_types = {
            "session_start", "round_start", "round_end",
            "pre_tool_exec", "post_tool_exec",
            "pre_model_call", "post_model_call", "session_end",
        }
        hook_type_literal = HookDefinition.model_fields["type"].annotation
        actual_types = set(get_args(hook_type_literal))
        assert actual_types == expected_types, f"Expected {expected_types}, got {actual_types}"


# ---------------------------------------------------------------------------
# 2.13 EventBus 事件目录
# ---------------------------------------------------------------------------

class TestEventBus:
    """Doc §2.13: EventBus 事件目录."""

    def test_all_documented_events_exist(self):
        """Doc: 文档列出的事件类型全部在 AgentEvent 中定义."""
        from arf.core.events import EventType
        from typing import get_args
        actual_events = set(get_args(EventType))

        documented_events = {
            "session_start", "session_end",
            "user_input",
            "model_call_start", "model_call_end",
            "thinking_delta",
            "tool_call_start", "tool_call_end",
            "compaction_start", "compaction_end",
            "approval_required", "approval_resolved",
            "agent_switch",
            "guard_block", "guard_pass",
            "hook_start", "hook_end",
            "undo_executed",
            "rollback_executed",
            "error",
            "rate_limited",
            "circuit_opened", "circuit_half_open", "circuit_closed",
            "breaker_blocked",
        }

        missing_in_code = documented_events - actual_events
        assert not missing_in_code, f"Events documented but missing in code: {missing_in_code}"

    def test_agent_event_has_required_fields(self):
        """Doc: AgentEvent 含 type, data, session_id, agent_name, turn."""
        from arf.core.events import AgentEvent
        fields = AgentEvent.__dataclass_fields__
        assert "type" in fields
        assert "data" in fields
        assert "session_id" in fields
        assert "agent_name" in fields
        assert "turn" in fields
        assert "timestamp" in fields


# ---------------------------------------------------------------------------
# 2.14 配置
# ---------------------------------------------------------------------------

class TestConfigSync:
    """Doc §2.14: agent.yaml 配置字段与 config_base 对应."""

    def test_advanced_config_has_max_turns(self):
        """Doc: advanced.max_turns → AdvancedConfig.max_turns."""
        from arf.agent.config import AdvancedConfig
        fields = AdvancedConfig.model_fields
        assert "max_turns" in fields

    def test_advanced_config_has_loop_strategy(self):
        """Doc: advanced.loop_strategy → AdvancedConfig.loop_strategy."""
        from arf.agent.config import AdvancedConfig
        fields = AdvancedConfig.model_fields
        assert "loop_strategy" in fields

    def test_advanced_config_has_max_undo_depth(self):
        """Doc: advanced.max_undo_depth → AdvancedConfig.max_undo_depth."""
        from arf.agent.config import AdvancedConfig
        fields = AdvancedConfig.model_fields
        assert "max_undo_depth" in fields

    def test_concurrency_config_has_strategy_and_max_concurrency(self):
        """Doc: advanced.concurrency.strategy / max_concurrency."""
        from arf.core.config_base import ConcurrencyConfig
        fields = ConcurrencyConfig.model_fields
        assert "strategy" in fields
        assert "max_concurrency" in fields

    def test_handover_config_has_rules(self):
        """Doc: handover.rules → HandoverConfig.rules."""
        from arf.core.config_base import HandoverConfig
        fields = HandoverConfig.model_fields
        assert "rules" in fields

    def test_compaction_config_threshold_default(self):
        """Doc: CompactionConfig threshold 默认 0.75."""
        from arf.core.config_base import CompactionConfig
        assert CompactionConfig.model_fields["threshold"].default == 0.75

    def test_memory_config_defaults(self):
        """Doc: memory_max_tokens=2000, memory_top_k=5."""
        from arf.core.config_base import MemoryConfig
        assert MemoryConfig.model_fields["max_tokens"].default == 2000
        assert MemoryConfig.model_fields["top_k"].default == 5


# ---------------------------------------------------------------------------
# 3. 演进方向 — 确认未实现
# ---------------------------------------------------------------------------

class TestEvolutionClaims:
    """Doc §3: 演进方向 — 验证已识别但尚未实现的声称."""

    def test_plan_execute_config_literal_exists_but_no_impl(self):
        """Doc §3.1 说 plan_execute 选项不存在, 但 AdvancedConfig 实际有该 literal.

        这是事实发现: config 层面已接受 plan_execute, 但实现未就绪。
        文档应该改为 'loop_strategy: plan_execute 尚未实现 (但 config 已预留)'.
        """
        from arf.agent.config import AdvancedConfig
        from typing import get_args
        field = AdvancedConfig.model_fields["loop_strategy"]
        # Extract literal values from annotation
        annotation = field.annotation
        literal_values = set(get_args(annotation))
        # Doc says plan_execute not in config — but it IS
        assert "plan_execute" in literal_values, (
            "Finding: doc §3.1 claims plan_execute is not in config, "
            "but AdvancedConfig accepts it. Doc should say 'not yet implemented' "
            "not 'option does not exist'."
        )
        # The strategy file still doesn't exist
        assert not Path("arf/engine/loop_strategies/plan_execute.py").exists(), (
            "Implementation still missing — config wired but code not ready"
        )

    def test_react_strategy_next_step_doc_matches_behavior(self):
        """Doc: ReActStrategy.next_step() 注释描述 dispatch 逻辑."""
        from arf.engine.loop_strategies.react import ReActStrategy
        doc = (ReActStrategy.next_step.__doc__ or "")
        assert "call_model" in doc
        assert "execute_tools" in doc


# ---------------------------------------------------------------------------
# L3 行为测试 — 端到端验证 (asyncio.run)
# ---------------------------------------------------------------------------

class TestBehavioralEndToEnd:
    """L3-L5: 构造真实引擎实例，验证行为声称."""

    def test_text_only_response_triggers_break_and_checkpoint(self):
        """Doc: 模型返回纯文本 → break + State快照.put()."""

        async def _run():
            fake = FakeModelAdapter(default=FakeResponse(content="Hello!"))
            store = CountingStateStore()

            engine = _build_real_engine(
                fake_model=fake,
                state_store=store,
            )

            state: AgentState = {
                "session_id": "test",
                "messages": [{"role": "user", "content": "hi"}],
                "current_model": "quick",
                "current_turn": 0,
                "interaction_round": 0,
                "context_summary": "",
                "tool_results": {},
            }

            result = await engine.invoke(state)
            assert fake.call_count == 1
            assert store.put_call_count >= 1
            assert result["messages"][-1]["role"] == "assistant"
            assert result["messages"][-1]["content"] == "Hello!"

        asyncio.run(_run())

    def test_tool_calls_triggers_execute_then_continue(self):
        """Doc: 模型返回 tool_calls → 进入 execute_tools 阶段."""

        async def _run():
            fake = FakeModelAdapter(responses=[
                FakeResponse(content="", tool_calls=[{"id": "tc1", "name": "echo", "params": {"text": "hi"}}]),
                FakeResponse(content="Done."),
            ])
            store = CountingStateStore()

            engine = _build_real_engine(
                fake_model=fake,
                state_store=store,
            )

            state: AgentState = {
                "session_id": "test",
                "messages": [{"role": "user", "content": "echo hi"}],
                "current_model": "quick",
                "current_turn": 0,
                "interaction_round": 0,
                "context_summary": "",
                "tool_results": {},
            }

            result = await engine.invoke(state)
            assert fake.call_count == 2

        asyncio.run(_run())

    def test_max_turns_circuit_breaker_stops_loop(self):
        """Doc: max_turns=1, 第1个 turn 就被 should_continue 阻断.

        使用 side_effect 动态匹配 call_count 到 tool_call ID.
        """

        async def _run():
            fake = FakeModelAdapter(default=FakeResponse(
                content="",
                tool_calls=[{"id": "tc1", "name": "echo", "params": {}}],
            ))
            store = CountingStateStore()

            engine = _build_real_engine(
                fake_model=fake,
                state_store=store,
                max_turns=1,
            )

            state: AgentState = {
                "session_id": "test",
                "messages": [{"role": "user", "content": "loop"}],
                "current_model": "quick",
                "current_turn": 0,
                "interaction_round": 0,
                "context_summary": "",
                "tool_results": {},
            }

            result = await engine.invoke(state)
            # With max_turns=1, only 1 call_model before should_continue blocks
            assert fake.call_count == 1

        asyncio.run(_run())

    def test_cancel_stops_loop_at_boundary(self):
        """Doc: _cancelled() True → break 退出循环 (L3)."""

        async def _run():
            store = CountingStateStore()
            event = asyncio.Event()
            event.set()

            fake = FakeModelAdapter(default=FakeResponse(content="hi"))

            engine = _build_real_engine(
                fake_model=fake,
                state_store=store,
                cancel_event=event,
            )

            state: AgentState = {
                "session_id": "test",
                "messages": [{"role": "user", "content": "hi"}],
                "current_model": "quick",
                "current_turn": 0,
                "interaction_round": 0,
                "context_summary": "",
                "tool_results": {},
            }

            result = await engine.invoke(state)
            assert fake.call_count == 0  # cancelled before first model call

        asyncio.run(_run())

    def test_close_tool_calls_injects_synthetic_for_orphans(self):
        """Doc: _close_tool_calls 为孤儿 tool_call 注入合成结果 (L3)."""

        async def _run():
            engine = _build_real_engine(fake_model=FakeModelAdapter())

            state: AgentState = {
                "messages": [
                    {"role": "user", "content": "do it"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "orphan_1", "type": "function",
                         "function": {"name": "missing_tool", "arguments": "{}"}}
                    ]},
                ],
                "current_turn": 1,
            }

            result = engine._close_tool_calls(state)
            tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
            assert len(tool_msgs) >= 1
            orphan_msg = tool_msgs[0]
            assert orphan_msg["tool_call_id"] == "orphan_1"
            assert "tool result unavailable" in orphan_msg["content"]

        asyncio.run(_run())

    def test_close_tool_calls_no_change_when_all_matched(self):
        """Doc: _close_tool_calls 全部匹配时不修改消息 (L3)."""

        async def _run():
            engine = _build_real_engine(fake_model=FakeModelAdapter())

            state: AgentState = {
                "messages": [
                    {"role": "user", "content": "do it"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "tc1", "type": "function",
                         "function": {"name": "echo", "arguments": "{}"}}
                    ]},
                    {"role": "tool", "tool_call_id": "tc1", "content": "result"},
                ],
                "current_turn": 1,
            }

            original_len = len(state["messages"])
            result = engine._close_tool_calls(state)
            assert len(result["messages"]) == original_len

        asyncio.run(_run())

    def test_undo_restores_state_and_emits_event(self):
        """Doc: GraphEngine.undo() 恢复状态 + emit undo_executed (L5)."""

        async def _run():
            from arf.event_bus import InMemoryEventBus

            store = CountingStateStore()
            event_bus = InMemoryEventBus()

            engine = _build_real_engine(
                fake_model=FakeModelAdapter(),
                state_store=store,
                event_bus=event_bus,
            )

            state: AgentState = {
                "session_id": "test_undo",
                "agent_name": "main",
                "messages": [{"role": "user", "content": "before undo"}],
                "current_turn": 0,
            }
            engine._rounds.begin_round(state)

            state["messages"].append({"role": "assistant", "content": "response"})
            state["current_turn"] = 3

            restored = engine.undo(steps=1, session_id="test_undo")
            assert restored is not None
            assert len(restored["messages"]) == 1

            # Check undo_executed event emitted via EventBus
            events = event_bus.events_since(0)
            undo_events = [e for e in events if e.type == "undo_executed"]
            assert len(undo_events) == 1

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Compaction 默认值
# ---------------------------------------------------------------------------

class TestCompactionDefaults:
    """Doc §2.1/§2.9: Compaction 配置默认值."""

    def test_sliding_window_compactor_default_threshold(self):
        """Doc: SlidingWindowCompactor threshold 默认 0.75."""
        from arf.compaction.sliding_window import SlidingWindowCompactor
        # Can't use inspect.signature due to 'callable | None' type annotation
        c = SlidingWindowCompactor()
        assert c._threshold == 0.75

    def test_compaction_config_threshold_default(self):
        """Doc: CompactionConfig threshold 默认 0.75."""
        from arf.core.config_base import CompactionConfig
        assert CompactionConfig.model_fields["threshold"].default == 0.75


# ---------------------------------------------------------------------------
# 原则2: 可配置性
# ---------------------------------------------------------------------------

class TestConfigurabilityWiring:
    """原则2: agent.yaml → config_base → BaseAgent → 目标类 wiring 完整性."""

    def test_max_turns_wired_to_graph_engine(self):
        """Doc: advanced.max_turns → AdvancedConfig → BaseAgent → GraphEngine(max_turns=)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "max_turns" in source

    def test_max_undo_depth_wired_to_round_manager(self):
        """Doc: advanced.max_undo_depth → RoundManager(max_undo_depth=)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "max_undo_depth" in source

    def test_concurrency_wired_to_tool_executor(self):
        """Doc: advanced.concurrency → ConcurrentToolExecutor(strategy, max_concurrency)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "strategy" in source
        assert "max_concurrency" in source

    def test_loop_strategy_wired_to_react_strategy(self):
        """Doc: advanced.loop_strategy → ReActStrategy."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "loop_strategy" in source

    def test_compaction_threshold_wired_to_compactor(self):
        """Doc: CompactionConfig.threshold → SlidingWindowCompactor(threshold=)."""
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent.__init__)
        assert "threshold" in source

    def test_poll_interval_wired_to_reload_config(self):
        """原则2 VERIFIED: poll_interval 现在从 ReloadConfig 读取.

        之前硬编码为 5.0，现已修复为 reload_cfg.poll_interval。
        """
        from arf.agent.base import BaseAgent
        import inspect as _inspect
        source = _inspect.getsource(BaseAgent._build_resource_resolver)
        assert "reload_cfg.poll_interval" in source
        assert "poll_interval = 5.0" not in source

    def test_reload_config_has_poll_interval_field(self):
        """ReloadConfig.poll_interval 字段存在，证实断裂非配置缺失所致."""
        from arf.core.config_base import ReloadConfig
        assert "poll_interval" in ReloadConfig.model_fields


# ===========================================================================
# NEW FINDINGS — 2026-05-29 joint fact-check (agent-execution + trace + eval)
# ===========================================================================

class TestFindingsLoopStrategyProtocol:
    """Doc §2.5: LoopStrategy protocol 展示遗漏 next_step 方法."""

    def test_loop_strategy_protocol_has_next_step(self):
        """FIXED 2026-05-29: Doc 已补全 next_step. 协议有 3 个方法."""
        from arf.core.protocols.engine import LoopStrategy
        import inspect
        sig = inspect.signature(LoopStrategy.next_step)
        assert "state" in sig.parameters
        assert sig.return_annotation is str


class TestFindingsRawTurnsDefault:
    """Doc §2.10: raw_turns 默认值 5 不是 4."""

    def test_raw_turns_default_is_5(self):
        """HandoverContextConfig.raw_turns 默认值为 5, 不是文档曾声称的 4."""
        from arf.core.config_base import HandoverContextConfig
        cfg = HandoverContextConfig()
        assert cfg.raw_turns == 5, f"raw_turns default is {cfg.raw_turns}, expected 5"


class TestFindingsToolResultsPersistence:
    """Doc §2.11: tool_results 移除行为仅针对 FileStateStore."""

    def test_inmemory_state_store_does_not_remove_tool_results(self):
        """InMemoryStateStore.put() 不移除 tool_results, 仅 FileStateStore 移除."""
        from arf.engine.checkpoint import InMemoryStateStore
        src = inspect.getsource(InMemoryStateStore.put)
        assert "pop" not in src, (
            "FACT: InMemoryStateStore does NOT remove tool_results, "
            "only FileStateStore does."
        )

    def test_file_state_store_does_remove_tool_results(self):
        """FileStateStore.put() 确实移除 tool_results."""
        from arf.engine.checkpoint import FileStateStore
        src = inspect.getsource(FileStateStore.put)
        assert 'data.pop("tool_results"' in src or "data.pop('tool_results'" in src


class TestFindingsModelCallProtectorPath:
    """Doc §2.13: ModelCallProtector 路径应为 arf/protection/protector.py."""

    def test_model_call_protector_not_in_observability(self):
        """ModelCallProtector 在 arf/protection/protector.py, 非 arf/observability/."""
        observability_files = list(Path(__file__).parent.parent.parent.glob(
            "arf/observability/*.py"))
        protection_files = list(Path(__file__).parent.parent.parent.glob(
            "arf/protection/*.py"))
        assert any("protector.py" in str(f) for f in protection_files), (
            "ModelCallProtector is in arf/protection/protector.py"
        )
        assert not any("protection.py" in str(f) for f in observability_files), (
            "No protection.py in arf/observability/"
        )

    def test_doc_fixed_model_call_protector_path(self):
        """FIXED 2026-05-29: Doc 已修正为 arf/protection/protector.py."""
        doc_path = Path(__file__).parent.parent.parent / "docs" / "agent-execution.md"
        content = doc_path.read_text(encoding="utf-8")
        assert "observability/protection" not in content, (
            "FIX VERIFIED: No stale arf/observability/protection reference in doc"
        )
