# Primitive Agent + Harness 实现计划

> **面向 agentic 工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐任务实现本计划。步骤使用复选框（`- [ ]`）语法进行跟踪。

**目标：** 用 PrimitiveAgent（6 个原语）+ AgentHarness（执行骨架 + 插件调度器 + park/resume）替换 BaseAgent（约 980 行）+ ControlPlane（约 1338 行）。所有现有功能均变为插件。

**架构：** 在旧文件旁构建新文件。先创建基础类型，然后是 PrimitiveAgent，再是带有 Plugin 基类的 AgentHarness。提供 PluginAdapter 垫片，以便迁移期间现有插件可在新 harness 上运行。待关键插件迁移完成后移除旧代码。

**技术栈：** Python 3.11+，Pydantic v2，asyncio，pytest。复用 ModelAdapter、ModelDegrader、InMemoryEventBus、FileStateStore、ConcurrentToolExecutor、AgentEvent。

## 全局约束

- Python 3.11+
- 使用 Pydantic v2 作为配置模型
- 所有公共 API 方法必须带有类型注解
- 协议定义位于 `arf/core/protocols/`
- 测试替身位于 `arf/testing/`
- 测试位于 `tests/`
- 提交风格：`type(scope): description`，附带 `Co-Authored-By: Claude Code with DeepSeek V4`
- 设计文档：`docs/2026-06-20-primitive-agent-harness-design.md`

---

## 文件映射

| 新建 / 修改 | 路径 | 职责 |
|-------------|------|------|
| 新建 | `arf/agent/state.py` | AgentState、Message、WaitItem、ModelResult 数据类 |
| 新建 | `arf/agent/primitive.py` | PrimitiveAgent —— 6 个原语 |
| 新建 | `arf/harness/__init__.py` | 公共 API |
| 新建 | `arf/harness/context.py` | PluginContext |
| 新建 | `arf/harness/plugin_base.py` | Plugin 基类 |
| 新建 | `arf/harness/engine.py` | AgentHarness —— 执行循环 |
| 新建 | `arf/harness/config.py` | 来自 harness.yaml 的 HarnessConfig |
| 新建 | `arf/harness/loader.py` | 插件 YAML 加载器 + 事件注册 |
| 新建 | `arf/tooling/__init__.py` | 公共 API |
| 新建 | `arf/tooling/executor.py` | ToolExecutor —— 最小化，无验证 |
| 新建 | `arf/tooling/registry.py` | ToolRegistry —— 聚合来自各来源的工具 |
| 修改 | `arf/agent/config.py` | 简化 AgentConfig（移除插件字段） |
| 修改 | `arf/agent/__init__.py` | 导出 PrimitiveAgent、AgentState |
| 新建 | `arf/harness/adapter.py` | PluginAdapter —— 旧插件 → 新 Plugin 垫片 |
| 新建 | `arf/plugins/base.py` | 从 harness 重新导出 Plugin |
| 新建 | `tests/test_agent_state.py` | 状态类型单元测试 |
| 新建 | `tests/test_primitive_agent.py` | PrimitiveAgent 单元测试 |
| 新建 | `tests/test_harness_engine.py` | AgentHarness 集成测试 |
| 新建 | `tests/test_plugin_loading.py` | 插件加载 + 注册测试 |
| 新建 | `tests/test_harness_park.py` | Park/resume 测试 |
| 新建 | `tests/test_plugin_adapter.py` | Adapter 垫片测试 |

---

### 任务 1：基础类型 —— AgentState、Message、WaitItem、ModelResult

**文件：**
- 新建：`arf/agent/state.py`
- 新建：`tests/test_agent_state.py`

**接口：**
- 产出：`AgentState`、`Message`、`WaitItem`、`ModelResult` 数据类

- [ ] **步骤 1：编写状态创建的失败测试**

```python
# tests/test_agent_state.py
import pytest
from arf.agent.state import AgentState, Message, WaitItem, ModelResult


def test_create_empty_agent_state():
    state = AgentState(
        agent_id="test-agent",
        session_id="",
        messages=[],
        waiting={},
        model_config={"api_base": "https://x.com/v1", "api_key_env": "KEY", "model_name": "m1", "context_window": 128000},
    )
    assert state.agent_id == "test-agent"
    assert state.session_id == ""
    assert state.messages == []
    assert state.waiting == {}


def test_message_creation():
    msg = Message(message_id="m1", role="user", content="hello")
    assert msg.message_id == "m1"
    assert msg.role == "user"
    assert msg.content == "hello"


def test_message_content_can_be_dict():
    msg = Message(message_id="m2", role="tool", content={"tool_call_id": "t1", "result": "ok"})
    assert msg.content["result"] == "ok"


def test_wait_item_creation():
    wi = WaitItem(wait_id="w1", hook_name="before_tools", reason="approval")
    assert wi.hook_name == "before_tools"


def test_model_result_creation():
    mr = ModelResult(content="hi", tool_calls=[], usage={}, finish_reason="stop")
    assert mr.content == "hi"
    assert mr.tool_calls == []


def test_model_result_with_tool_calls():
    mr = ModelResult(
        content="",
        tool_calls=[{"id": "t1", "name": "read_file", "params": {"path": "x.txt"}}],
        usage={"total_tokens": 100},
        finish_reason="tool_calls",
    )
    assert len(mr.tool_calls) == 1
    assert mr.tool_calls[0]["name"] == "read_file"
```

- [ ] **步骤 2：运行测试以确认失败**

运行：`pytest tests/test_agent_state.py -v`
预期：失败 —— 模块未找到

- [ ] **步骤 3：编写 `arf/agent/state.py`**

```python
"""Agent state types — message, wait, model result dataclasses."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    message_id: str
    role: str          # "system" | "user" | "assistant" | "tool"
    content: Any       # str for text, dict for structured data


@dataclass
class WaitItem:
    wait_id: str
    hook_name: str     # harness checkpoint name
    reason: str
    created_at: float = 0.0


@dataclass
class ModelResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, params}]
    usage: dict = field(default_factory=dict)
    finish_reason: str = "stop"


@dataclass
class AgentState:
    agent_id: str
    session_id: str
    messages: list[Message]
    waiting: dict[str, list[WaitItem]]   # hook_name -> [WaitItem, ...]
    model_config: dict                   # {api_base, api_key_env, model_name, context_window}
```

- [ ] **步骤 4：运行测试以确认通过**

运行：`pytest tests/test_agent_state.py -v`
预期：通过（6 个测试）

- [ ] **步骤 5：提交**

```bash
git add arf/agent/state.py tests/test_agent_state.py
git commit -m "feat(agent): add AgentState, Message, WaitItem, ModelResult types

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 2：PrimitiveAgent —— 6 个原语

**文件：**
- 新建：`arf/agent/primitive.py`
- 新建：`tests/test_primitive_agent.py`
- 新建：`tests/fixtures/fake_model_adapter.py`（或复用现有的）

**接口：**
- 消费：来自 `arf/agent/state.py` 的 `AgentState`、`Message`、`WaitItem`、`ModelResult`
- 产出：`PrimitiveAgent` 类，包含方法：
  - `__init__(self, agent_id: str, model_config: dict, call_model: Callable)` —— session_id 初始为 ""
  - `input(self, role: str, content: Any, position: str = "end") -> Message`
  - `async model_call(self) -> ModelResult`
  - `wait(self, hook_name: str, reason: str) -> WaitItem`
  - `finish_wait(self, wait_id: str, reason: str = "") -> dict[str, list[WaitItem]]`
  - `stop(self) -> AgentState`
  - `classmethod resume(cls, state: AgentState, call_model: Callable) -> PrimitiveAgent`

- [ ] **步骤 1：编写失败测试**

```python
# tests/test_primitive_agent.py
import pytest
from arf.agent.state import AgentState, Message, WaitItem, ModelResult
from arf.agent.primitive import PrimitiveAgent


def fake_call_model(messages, tools=None):
    return ModelResult(content="fake response", tool_calls=[], usage={}, finish_reason="stop")


@pytest.fixture
def agent():
    return PrimitiveAgent(
        agent_id="a1",
        model_config={"api_base": "https://x.com/v1", "api_key_env": "K", "model_name": "m", "context_window": 128000},
        call_model=fake_call_model,
    )


class TestInput:
    def test_input_appends_message_by_default(self, agent):
        msg = agent.input("user", "hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert len(agent.state.messages) == 1
        assert agent.state.messages[0].message_id == msg.message_id

    def test_input_generates_unique_message_ids(self, agent):
        m1 = agent.input("user", "a")
        m2 = agent.input("user", "b")
        assert m1.message_id != m2.message_id

    def test_input_position_begin(self, agent):
        agent.input("user", "first")
        agent.input("system", "inserted", position="begin")
        assert agent.state.messages[0].role == "system"
        assert agent.state.messages[0].content == "inserted"

    def test_input_position_index(self, agent):
        agent.input("user", "a")
        agent.input("user", "b")
        agent.input("system", "middle", position=1)
        assert agent.state.messages[1].content == "middle"


class TestModelCall:
    @pytest.mark.asyncio
    async def test_model_call_returns_result(self, agent):
        agent.input("user", "hi")
        result = await agent.model_call()
        assert result.content == "fake response"
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_model_call_passes_messages_to_call_model(self):
        captured = []

        async def capture_call(messages, tools=None):
            captured.append(messages)
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        ag = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=capture_call)
        ag.input("user", "test message")
        await ag.model_call()
        assert len(captured) == 1
        msgs = captured[0]
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "test message"


class TestWait:
    def test_wait_appends_to_state(self, agent):
        wi = agent.wait("before_tools", "need approval")
        assert wi.hook_name == "before_tools"
        assert agent.state.waiting["before_tools"][0] is wi

    def test_wait_generates_unique_ids(self, agent):
        w1 = agent.wait("before_tools", "a")
        w2 = agent.wait("before_tools", "b")
        assert w1.wait_id != w2.wait_id
        assert len(agent.state.waiting["before_tools"]) == 2


class TestFinishWait:
    def test_finish_wait_removes_item(self, agent):
        wi = agent.wait("before_tools", "x")
        remaining = agent.finish_wait(wi.wait_id)
        assert "before_tools" not in remaining or len(remaining.get("before_tools", [])) == 0

    def test_finish_wait_returns_updated_waiting(self, agent):
        w1 = agent.wait("before_tools", "a")
        w2 = agent.wait("before_tools", "b")
        remaining = agent.finish_wait(w1.wait_id)
        assert len(remaining["before_tools"]) == 1
        assert remaining["before_tools"][0].wait_id == w2.wait_id


class TestStop:
    def test_stop_returns_state(self, agent):
        agent.input("user", "hello")
        state = agent.stop()
        assert isinstance(state, AgentState)
        assert len(state.messages) == 1

    def test_stop_clears_agent(self, agent):
        agent.stop()
        # After stop, agent should not be usable for model_call
        # (model connection torn down)


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_restores_full_state(self):
        # Create agent, add messages + waits, stop, resume, verify
        ag1 = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ag1.input("user", "msg1")
        ag1.wait("before_tools", "approval")
        state = ag1.stop()

        ag2 = PrimitiveAgent.resume(state, fake_call_model)
        assert ag2.state.agent_id == "a1"
        assert ag2.state.session_id == ""
        assert len(ag2.state.messages) == 1
        assert ag2.state.messages[0].content == "msg1"
        assert len(ag2.state.waiting["before_tools"]) == 1
```

- [ ] **步骤 2：运行测试以确认失败**

运行：`pytest tests/test_primitive_agent.py -v`
预期：失败 —— 模块未找到

- [ ] **步骤 3：编写 `arf/agent/primitive.py`**

```python
"""PrimitiveAgent — 6 primitives: input, model_call, wait, finish_wait, stop, resume."""
from __future__ import annotations
import uuid
import time
from collections.abc import Callable, Awaitable
from typing import Any
from arf.agent.state import AgentState, Message, WaitItem, ModelResult


class PrimitiveAgent:
    """Passive message state machine with model calling capability.

    Knows nothing about tools, hooks, session/turn lifecycle, sandbox, or events.
    """

    def __init__(
        self,
        agent_id: str,
        model_config: dict,
        call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
    ) -> None:
        self.state = AgentState(
            agent_id=agent_id,
            session_id="",           # assigned by harness when session starts
            messages=[],
            waiting={},
            model_config=model_config,
        )
        self._call_model = call_model
        self._active = True

    # ── input ──────────────────────────────────────────

    def input(self, role: str, content: Any, position: str | int = "end") -> Message:
        """Inject a message into state.messages."""
        msg = Message(
            message_id=str(uuid.uuid4()),
            role=role,
            content=content,
        )
        if position == "end":
            self.state.messages.append(msg)
        elif position == "begin":
            self.state.messages.insert(0, msg)
        elif isinstance(position, int):
            self.state.messages.insert(position, msg)
        else:
            self.state.messages.append(msg)
        return msg

    # ── model_call ─────────────────────────────────────

    async def model_call(self) -> ModelResult:
        """Single LLM API call consuming state.messages."""
        if not self._active:
            raise RuntimeError("Agent has been stopped")
        messages = [
            {"role": m.role, "content": m.content}
            for m in self.state.messages
        ]
        return await self._call_model(messages, None)

    # ── wait ───────────────────────────────────────────

    def wait(self, hook_name: str, reason: str) -> WaitItem:
        """Append WaitItem to state.waiting[hook_name]. Synchronous, does not block."""
        wi = WaitItem(
            wait_id=str(uuid.uuid4()),
            hook_name=hook_name,
            reason=reason,
            created_at=time.time(),
        )
        self.state.waiting.setdefault(hook_name, []).append(wi)
        return wi

    # ── finish_wait ────────────────────────────────────

    def finish_wait(self, wait_id: str, reason: str = "") -> dict[str, list[WaitItem]]:
        """Remove WaitItem by id. Returns updated state.waiting."""
        for hook_name, items in list(self.state.waiting.items()):
            self.state.waiting[hook_name] = [wi for wi in items if wi.wait_id != wait_id]
            if not self.state.waiting[hook_name]:
                del self.state.waiting[hook_name]
        return self.state.waiting

    # ── stop ───────────────────────────────────────────

    def stop(self) -> AgentState:
        """Return current full state for persistence. Tears down model connection."""
        self._active = False
        return self.state

    # ── resume ─────────────────────────────────────────

    @classmethod
    def resume(
        cls, state: AgentState,
        call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
    ) -> PrimitiveAgent:
        """Reconstruct agent from state, including model connection and session_id."""
        agent = cls(
            agent_id=state.agent_id,
            model_config=state.model_config,
            call_model=call_model,
        )
        agent.state = state      # restore full state including session_id + messages + waiting
        agent._active = True
        return agent
```

- [ ] **步骤 4：运行测试以确认通过**

运行：`pytest tests/test_primitive_agent.py -v`
预期：通过（所有测试）

- [ ] **步骤 5：提交**

```bash
git add arf/agent/primitive.py tests/test_primitive_agent.py
git commit -m "feat(agent): add PrimitiveAgent with 6 primitives

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 3：PluginContext + Plugin 基类

**文件：**
- 新建：`arf/harness/__init__.py`
- 新建：`arf/harness/context.py`
- 新建：`arf/harness/plugin_base.py`
- 新建：`tests/test_plugin_loading.py`

**接口：**
- 消费：来自 `arf/agent/primitive.py` 的 `PrimitiveAgent`，来自 `arf/core/events.py` 的 `AgentEvent`
- 产出：
  - `PluginContext(agent, hook_data, session_id, event_bus)` —— 带有 `emit(event_type, data)` 方法
  - `Plugin` 基类，包含 `name: str`、`events: list[dict]`、`async handle(event_name, ctx)`

- [ ] **步骤 1：编写 PluginContext**

```python
# arf/harness/context.py
"""PluginContext — injected into plugins at each harness checkpoint."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from arf.core.events import AgentEvent

if TYPE_CHECKING:
    from arf.agent.primitive import PrimitiveAgent
    from arf.event_bus import InMemoryEventBus


class PluginContext:
    def __init__(
        self,
        agent: PrimitiveAgent,
        session_id: str,
        event_bus: InMemoryEventBus | None = None,
    ) -> None:
        self.agent = agent
        self.session_id = session_id
        self.hook_data: dict[str, Any] = {}
        self._event_bus = event_bus

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> AgentEvent:
        event = AgentEvent(
            type=event_type,
            data=data or {},
            session_id=self.session_id,
        )
        if self._event_bus:
            self._event_bus.emit(event)
        return event
```

```python
# arf/harness/plugin_base.py
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
```

```python
# arf/harness/__init__.py
"""Harness — execution skeleton, plugin scheduler, park/resume."""
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin

__all__ = ["PluginContext", "Plugin"]
```

- [ ] **步骤 2：编写插件注册测试**

```python
# tests/test_plugin_loading.py
import pytest
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext


class FakePlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="fake",
            events=[
                {"hook_name": "before_model", "event_name": "compact", "mode": "blocking"},
                {"hook_name": "after_model", "event_name": "log", "mode": "side"},
            ],
        )
        self.handled: list[tuple[str, str]] = []

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        self.handled.append((event_name, ctx.session_id))


class TestPlugin:
    def test_event_names_for_hook(self):
        p = FakePlugin()
        assert p.event_names_for_hook("before_model") == ["compact"]
        assert p.event_names_for_hook("after_model") == ["log"]
        assert p.event_names_for_hook("before_tools") == []

    def test_mode_for(self):
        p = FakePlugin()
        assert p.mode_for("before_model", "compact") == "blocking"
        assert p.mode_for("after_model", "log") == "side"

    @pytest.mark.asyncio
    async def test_handle_receives_context(self):
        from arf.agent.primitive import PrimitiveAgent

        def fake_call(messages, tools=None):
            from arf.agent.state import ModelResult
            return ModelResult(content="ok")

        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call)
        ctx = PluginContext(agent=agent, session_id="s1")
        p = FakePlugin()
        await p.handle("compact", ctx)
        assert p.handled == [("compact", "s1")]

    def test_plugin_context_emit(self):
        from arf.event_bus import InMemoryEventBus
        from arf.agent.primitive import PrimitiveAgent

        def fake_call(messages, tools=None):
            from arf.agent.state import ModelResult
            return ModelResult(content="ok")

        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call)
        bus = InMemoryEventBus()
        ctx = PluginContext(agent=agent, session_id="s1", event_bus=bus)
        ctx.emit("test_event", {"key": "value"})
        assert bus.event_count() == 1
        assert bus.collected("test_event")[0].data["key"] == "value"
```

- [ ] **步骤 3：运行测试**

运行：`pytest tests/test_plugin_loading.py -v`
预期：通过

- [ ] **步骤 4：提交**

```bash
git add arf/harness/ tests/test_plugin_loading.py
git commit -m "feat(harness): add PluginContext and Plugin base class

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 4：AgentHarness —— 执行循环 + 检查点调度

**文件：**
- 新建：`arf/harness/engine.py`
- 新建：`tests/test_harness_engine.py`

**接口：**
- 消费：`PrimitiveAgent`、`Plugin`、`PluginContext`、`AgentEvent`
- 产出：`AgentHarness` 类：
  - `__init__(self, agent, plugins, tool_executor, event_bus, max_turns)`
  - `async run(self, user_message: str, session_id: str | None = None) -> AsyncIterator[AgentEvent]`
  - `async resolve_wait(self, wait_id: str, inject_message: dict | None) -> bool`

- [ ] **步骤 1：编写 AgentHarness**

```python
# arf/harness/engine.py
"""AgentHarness — execution skeleton + plugin scheduler + park/resume."""
from __future__ import annotations
import asyncio
import uuid
import logging
from collections.abc import AsyncIterator
from typing import Any

from arf.agent.primitive import PrimitiveAgent
from arf.core.events import AgentEvent
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin

logger = logging.getLogger("arf.harness")

CHECKPOINTS = [
    "before_round", "before_model", "after_model",
    "before_tools", "after_tools", "after_round", "on_error",
]


class AgentHarness:
    def __init__(
        self,
        agent: PrimitiveAgent,
        plugins: list[Plugin],
        tool_executor: Any = None,
        event_bus: Any = None,
        max_turns: int = 50,
    ) -> None:
        self.agent = agent
        self._plugins = plugins
        self._tool_executor = tool_executor
        self._event_bus = event_bus
        self._max_turns = max_turns
        self._park_event: asyncio.Event | None = None
        self._parked: bool = False

        # Index plugins by hook_name for fast lookup
        self._by_hook: dict[str, list[Plugin]] = {c: [] for c in CHECKPOINTS}
        for p in plugins:
            for e in p.events:
                hook = e["hook_name"]
                if hook in self._by_hook:
                    self._by_hook[hook].append(p)

    # ── Plugin scheduling ───────────────────────────────

    def _make_ctx(self) -> PluginContext:
        return PluginContext(
            agent=self.agent,
            session_id=self.agent.state.session_id,
            event_bus=self._event_bus,
        )

    async def _run_blocking(self, hook_name: str, ctx: PluginContext) -> None:
        for p in self._by_hook.get(hook_name, []):
            for ename in p.event_names_for_hook(hook_name):
                if p.mode_for(hook_name, ename) == "blocking":
                    await p.handle(ename, ctx)

    def _run_side(self, hook_name: str, ctx: PluginContext) -> None:
        for p in self._by_hook.get(hook_name, []):
            for ename in p.event_names_for_hook(hook_name):
                if p.mode_for(hook_name, ename) == "side":
                    asyncio.create_task(self._safe_side(p, ename, ctx))

    async def _safe_side(self, plugin: Plugin, event_name: str, ctx: PluginContext) -> None:
        try:
            await plugin.handle(event_name, ctx)
        except Exception:
            logger.exception("Side plugin %s.%s failed", plugin.name, event_name)

    # ── Checkpoint ──────────────────────────────────────

    async def _checkpoint(self, hook_name: str, ctx: PluginContext) -> bool:
        """Run plugins at checkpoint, then check waiting. Returns True if should park."""
        ctx.hook_data["_current_hook"] = hook_name

        # 1. Run blocking plugins
        await self._run_blocking(hook_name, ctx)

        # 2. Run side plugins (fire and forget)
        self._run_side(hook_name, ctx)

        # 3. Check waiting for this hook_name
        waiting = self.agent.state.waiting.get(hook_name, [])
        return len(waiting) > 0

    # ── Execution Loop ──────────────────────────────────

    async def run(self, user_message: str, session_id: str | None = None) -> AsyncIterator[AgentEvent]:
        """Main execution loop. Yields AgentEvent for SSE streaming."""
        agent = self.agent

        # Assign session_id if this is a new session
        if not agent.state.session_id:
            agent.state.session_id = session_id or str(uuid.uuid4())

        ctx = self._make_ctx()

        # Inject user message
        agent.input("user", user_message)

        # --- before_round ---
        if await self._checkpoint("before_round", ctx):
            yield ctx.emit("parked", {"hook_name": "before_round", "waiting": agent.state.waiting})
            await self._do_park()
            if self._parked:
                return

        turn = 0
        while turn < self._max_turns:
            turn += 1

            # --- before_model ---
            if await self._checkpoint("before_model", ctx):
                yield ctx.emit("parked", {"hook_name": "before_model", "waiting": agent.state.waiting})
                await self._do_park()
                if self._parked:
                    return

            # --- model_call ---
            try:
                result = await agent.model_call()
            except Exception as exc:
                ctx.hook_data["exception"] = exc
                await self._checkpoint("on_error", ctx)
                yield ctx.emit("error", {"detail": str(exc)})
                break

            yield ctx.emit("model_call_end", {
                "content": result.content,
                "tool_calls": result.tool_calls,
                "finish_reason": result.finish_reason,
            })

            # --- after_model ---
            if await self._checkpoint("after_model", ctx):
                yield ctx.emit("parked", {"hook_name": "after_model", "waiting": agent.state.waiting})
                await self._do_park()
                if self._parked:
                    return

            # --- tool execution ---
            if result.tool_calls and self._tool_executor:
                # --- before_tools ---
                if await self._checkpoint("before_tools", ctx):
                    yield ctx.emit("parked", {"hook_name": "before_tools", "waiting": agent.state.waiting})
                    await self._do_park()
                    if self._parked:
                        return

                # Execute tools
                for tc in result.tool_calls:
                    yield ctx.emit("tool_call_start", {"name": tc["name"], "id": tc["id"]})

                try:
                    tool_results = await self._tool_executor.execute(result.tool_calls)
                except Exception as exc:
                    ctx.hook_data["exception"] = exc
                    await self._checkpoint("on_error", ctx)
                    yield ctx.emit("error", {"detail": str(exc)})
                    break

                for tc in result.tool_calls:
                    r = tool_results.get(tc["id"])
                    agent.input("tool", {
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "result": r.data if r and r.success else "",
                        "error": str(r.error) if r and r.error else "",
                    })
                    yield ctx.emit("tool_call_end", {
                        "name": tc["name"], "id": tc["id"],
                        "success": r.success if r else False,
                    })

                # --- after_tools ---
                if await self._checkpoint("after_tools", ctx):
                    yield ctx.emit("parked", {"hook_name": "after_tools", "waiting": agent.state.waiting})
                    await self._do_park()
                    if self._parked:
                        return

                continue  # loop back to before_model

            break  # no tool_calls → round done

        # --- after_round ---
        if await self._checkpoint("after_round", ctx):
            yield ctx.emit("parked", {"hook_name": "after_round", "waiting": agent.state.waiting})
            await self._do_park()

    # ── Park / Resume ────────────────────────────────────

    async def _do_park(self) -> None:
        """Block until external resolve_wait() empties all waiting groups."""
        self._park_event = asyncio.Event()
        self._parked = True
        await self._park_event.wait()

    async def resolve_wait(self, wait_id: str, inject_message: dict | None = None) -> bool:
        """External call: finish a wait + optionally inject a message. Returns True if park resolves."""
        if inject_message:
            self.agent.input(
                role=inject_message.get("role", "user"),
                content=inject_message.get("content", ""),
            )
        self.agent.finish_wait(wait_id)

        if not self.agent.state.waiting:
            self._parked = False
            if self._park_event:
                self._park_event.set()
            return True
        return False
```

- [ ] **步骤 2：编写集成测试**

```python
# tests/test_harness_engine.py
import pytest
from arf.agent.state import AgentState, Message, WaitItem, ModelResult
from arf.agent.primitive import PrimitiveAgent
from arf.harness.engine import AgentHarness
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.event_bus import InMemoryEventBus


class FakeToolResult:
    def __init__(self, success=True, data="ok", error=""):
        self.success = success
        self.data = data
        self.error = error
        self.duration_ms = 10


class FakeToolExecutor:
    def __init__(self):
        self.calls: list[list] = []

    async def execute(self, tool_calls):
        self.calls.append(tool_calls)
        return {tc["id"]: FakeToolResult() for tc in tool_calls}


def make_agent(call_model, agent_id="a1"):
    return PrimitiveAgent(
        agent_id=agent_id,
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=call_model,
    )


class TestHarnessBasicFlow:
    @pytest.mark.asyncio
    async def test_run_text_only_response(self):
        def fake_call(messages, tools=None):
            return ModelResult(content="Hello, user!", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[], tool_executor=None)
        events = [e async for e in harness.run("hi")]

        assert any(e.type == "model_call_end" for e in events)
        model_end = next(e for e in events if e.type == "model_call_end")
        assert model_end.data["content"] == "Hello, user!"

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self):
        turn = 0

        def fake_call(messages, tools=None):
            nonlocal turn
            turn += 1
            if turn == 1:
                return ModelResult(content="", tool_calls=[{"id": "t1", "name": "read_file", "params": {"path": "x.txt"}}], usage={}, finish_reason="tool_calls")
            return ModelResult(content="File contents: hello", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        tool_exec = FakeToolExecutor()
        harness = AgentHarness(agent, plugins=[], tool_executor=tool_exec)

        events = [e async for e in harness.run("read x.txt")]

        assert len(tool_exec.calls) == 1
        assert tool_exec.calls[0][0]["name"] == "read_file"
        model_ends = [e for e in events if e.type == "model_call_end"]
        assert len(model_ends) == 2


class TestHarnessPlugins:
    @pytest.mark.asyncio
    async def test_plugin_runs_at_checkpoint(self):
        def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        class TracePlugin(Plugin):
            def __init__(self):
                super().__init__("trace", [
                    {"hook_name": "after_model", "event_name": "trace_model", "mode": "side"},
                ])
                self.traced: list[str] = []

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                self.traced.append(event_name)

        trace = TracePlugin()
        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[trace], tool_executor=None)
        events = [e async for e in harness.run("hi")]

        assert "trace_model" in trace.traced


class TestHarnessPark:
    @pytest.mark.asyncio
    async def test_agent_wait_triggers_park(self):
        def fake_call(messages, tools=None):
            # Agent calls wait on before_model
            return ModelResult(content="ok, but wait", tool_calls=[], usage={}, finish_reason="stop")

        class WaitingPlugin(Plugin):
            def __init__(self):
                super().__init__("waiter", [
                    {"hook_name": "before_model", "event_name": "check_wait", "mode": "blocking"},
                ])

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                ctx.agent.wait("before_model", "test wait")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[WaitingPlugin()], tool_executor=None)

        # Start run in background
        import asyncio
        events = []

        async def collect():
            async for e in harness.run("hi"):
                events.append(e)

        task = asyncio.ensure_future(collect())
        await asyncio.sleep(0.1)  # let it park

        assert harness._parked
        assert len(events) > 0
        parked_event = next(e for e in events if e.type == "parked")
        assert "before_model" in str(parked_event.data)

        # Resolve
        wait_id = list(agent.state.waiting["before_model"])[0].wait_id
        resolved = await harness.resolve_wait(wait_id)
        assert resolved

        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

- [ ] **步骤 3：运行测试**

运行：`pytest tests/test_harness_engine.py -v`
预期：通过

- [ ] **步骤 4：提交**

```bash
git add arf/harness/engine.py tests/test_harness_engine.py
git commit -m "feat(harness): add AgentHarness with execution loop and park/resume

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 5：ToolExecutor —— 最小化，无验证

**文件：**
- 新建：`arf/tooling/__init__.py`
- 新建：`arf/tooling/executor.py`
- 新建：`arf/tooling/registry.py`

**接口：**
- 消费：MCP 工具定义（复用现有 McpClientManager）
- 产出：
  - `ToolRegistry(name, sources)` —— 聚合来自目录、MCP 和内核的工具定义
  - `ToolExecutor(registry)` —— `async execute(tool_calls) -> dict[str, ToolResult]`

ToolExecutor 是 **最小化** 的：解析工具 → 执行 → 返回结果。不包含验证、护栏、路径解析 —— 这些属于插件。

- [ ] **步骤 1：编写 ToolRegistry + ToolExecutor**

```python
# arf/tooling/registry.py
"""ToolRegistry — aggregate tool definitions from directory, MCP, and kernel sources."""
from __future__ import annotations
from typing import Any


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}       # name -> tool_def
        self._executors: dict[str, Any] = {}    # name -> callable

    def register(self, name: str, definition: dict, executor: Any) -> None:
        self._tools[name] = definition
        self._executors[name] = executor

    def register_batch(self, tools: list[dict], executor_map: dict[str, Any]) -> None:
        for t in tools:
            name = t["name"]
            exec_fn = executor_map.get(name)
            if exec_fn:
                self.register(name, t, exec_fn)

    def get(self, name: str) -> dict | None:
        return self._tools.get(name)

    def get_executor(self, name: str) -> Any | None:
        return self._executors.get(name)

    def list_definitions(self) -> list[dict]:
        return list(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools
```

```python
# arf/tooling/executor.py
"""ToolExecutor — execute tool calls. Minimal, no validation."""
from __future__ import annotations
import asyncio
import time
import logging
from typing import Any

from arf.tooling.registry import ToolRegistry

logger = logging.getLogger("arf.tooling")


class ToolResult:
    def __init__(self, success: bool, data: Any = None, error: str = "", duration_ms: float = 0):
        self.success = success
        self.data = data
        self.error = error
        self.duration_ms = duration_ms


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, timeout: float = 60.0) -> None:
        self._registry = registry
        self._timeout = timeout

    async def execute(self, tool_calls: list[dict]) -> dict[str, ToolResult]:
        tasks = []
        for tc in tool_calls:
            tasks.append(self._execute_one(tc))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: dict[str, ToolResult] = {}
        for tc, r in zip(tool_calls, results):
            if isinstance(r, Exception):
                out[tc["id"]] = ToolResult(success=False, error=str(r))
            else:
                out[tc["id"]] = r
        return out

    async def _execute_one(self, tc: dict) -> ToolResult:
        name = tc.get("name", "")
        params = tc.get("params", {})
        executor = self._registry.get_executor(name)
        if executor is None:
            return ToolResult(success=False, error=f"Tool not found: {name}")

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                executor(**params) if callable(executor) else executor.execute(params),
                timeout=self._timeout,
            )
            elapsed = (time.monotonic() - start) * 1000
            if isinstance(result, dict):
                return ToolResult(success=True, data=result, duration_ms=elapsed)
            return ToolResult(success=True, data=str(result), duration_ms=elapsed)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Tool '{name}' timed out after {self._timeout}s")
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(success=False, error=str(exc), duration_ms=elapsed)
```

```python
# arf/tooling/__init__.py
"""Tooling — tool registry and executor."""
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor, ToolResult

__all__ = ["ToolRegistry", "ToolExecutor", "ToolResult"]
```

- [ ] **步骤 2：编写测试**

```python
# tests/test_tooling.py
import pytest
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor, ToolResult


def test_register_and_lookup():
    reg = ToolRegistry()
    async def echo(**params):
        return params
    reg.register("echo", {"name": "echo", "description": "echoes"}, echo)
    assert "echo" in reg
    assert reg.get("echo")["description"] == "echoes"
    assert reg.get_executor("echo") is echo


@pytest.mark.asyncio
async def test_execute_single_tool():
    reg = ToolRegistry()
    async def echo(message="hi", **kw):
        return {"message": message}
    reg.register("echo", {"name": "echo", "description": ""}, echo)

    executor = ToolExecutor(reg)
    results = await executor.execute([{"id": "t1", "name": "echo", "params": {"message": "hello"}}])

    assert "t1" in results
    assert results["t1"].success
    assert results["t1"].data["message"] == "hello"


@pytest.mark.asyncio
async def test_execute_missing_tool():
    reg = ToolRegistry()
    executor = ToolExecutor(reg)
    results = await executor.execute([{"id": "t1", "name": "nonexistent", "params": {}}])
    assert not results["t1"].success
    assert "not found" in results["t1"].error


@pytest.mark.asyncio
async def test_execute_multiple_tools():
    reg = ToolRegistry()
    async def add(a=0, b=0, **kw):
        return {"result": a + b}
    async def mul(a=0, b=0, **kw):
        return {"result": a * b}
    reg.register("add", {"name": "add"}, add)
    reg.register("mul", {"name": "mul"}, mul)

    executor = ToolExecutor(reg)
    results = await executor.execute([
        {"id": "1", "name": "add", "params": {"a": 2, "b": 3}},
        {"id": "2", "name": "mul", "params": {"a": 4, "b": 5}},
    ])
    assert results["1"].data["result"] == 5
    assert results["2"].data["result"] == 20
```

- [ ] **步骤 3：运行测试**

运行：`pytest tests/test_tooling.py -v`
预期：通过

- [ ] **步骤 4：提交**

```bash
git add arf/tooling/ tests/test_tooling.py
git commit -m "feat(tooling): add ToolRegistry and minimal ToolExecutor

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 6：PluginAdapter —— 在 Harness 上运行旧插件

**文件：**
- 新建：`arf/harness/adapter.py`
- 新建：`tests/test_plugin_adapter.py`

**接口：**
- 消费：旧样式插件类（具有 `name`、`hooks` 字典和钩子处理方法的对象）
- 产出：将旧插件包装为新 `Plugin` 接口的 `PluginAdapter`

这是一个临时垫片，以便在迁移期间将现有插件（compaction、trace、approval）运行在新 harness 上。

- [ ] **步骤 1：编写 PluginAdapter**

```python
# arf/harness/adapter.py
"""PluginAdapter — wrap old-style plugins for new AgentHarness checkpoints.

Temporary: remove after all plugins are ported to the new Plugin base class.
"""
from __future__ import annotations
import logging
from typing import Any
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext

logger = logging.getLogger("arf.harness.adapter")

# Map old hook event names → new harness checkpoints
HOOK_TO_CHECKPOINT: dict[str, str] = {
    "session_start": "before_round",
    "round_start":   "before_round",
    "turn_start":    "before_model",
    "pre_action":    "before_model",
    "post_action":   "after_model",
    "tool_output":   "after_tools",
    "turn_end":      "after_model",
    "round_end":     "after_round",
    "session_end":   "after_round",
    "session_park":  "after_round",
    "task_completed":"after_round",
    "error":         "on_error",
}

CHECKPOINT_TO_OLD_HOOK: dict[str, str] = {
    "before_round": "round_start",
    "before_model": "pre_action",
    "after_model":  "post_action",
    "before_tools": "pre_action",
    "after_tools":  "tool_output",
    "after_round":  "round_end",
    "on_error":     "error",
}


class PluginAdapter(Plugin):
    """Wrap an old-style plugin to work with AgentHarness."""

    def __init__(self, old_plugin: Any) -> None:
        self._old = old_plugin
        name = getattr(old_plugin, "name", old_plugin.__class__.__name__)

        # Build events list from old plugin's hooks dict
        old_hooks: dict[str, str] = getattr(old_plugin, "hooks", {})
        events = []
        for old_hook, mode in old_hooks.items():
            checkpoint = HOOK_TO_CHECKPOINT.get(old_hook)
            if checkpoint:
                events.append({
                    "hook_name": checkpoint,
                    "event_name": old_hook,
                    "mode": "blocking" if mode == "blocking" else "side",
                })

        super().__init__(name=name, events=events)

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        """Delegate to old plugin's hook handler method."""
        old = self._old

        # Populate ctx with old-style expectations
        ctx.hook_data.setdefault("state", ctx.agent.state)
        ctx.hook_data.setdefault("messages", ctx.agent.state.messages)
        ctx.hook_data.setdefault("session_id", ctx.session_id)

        # Call old plugin's hook handler
        handler = getattr(old, "on_" + event_name, None)
        if handler:
            await handler(ctx)
        else:
            # Try generic fire method
            fire = getattr(old, "fire", None)
            if fire:
                await fire(event_name, ctx)
```

- [ ] **步骤 2：使用模拟旧插件测试适配器**

```python
# tests/test_plugin_adapter.py
import pytest
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.context import PluginContext
from arf.harness.adapter import PluginAdapter


class MockOldPlugin:
    """Simulates old-style plugin with hooks dict."""
    name = "mock_old"
    hooks = {"pre_action": "blocking", "post_action": "side"}

    def __init__(self):
        self.calls: list[str] = []

    async def on_pre_action(self, ctx):
        self.calls.append("pre_action")

    async def on_post_action(self, ctx):
        self.calls.append("post_action")


class TestPluginAdapter:
    @pytest.mark.asyncio
    async def test_adapter_maps_events(self):
        old = MockOldPlugin()
        adapter = PluginAdapter(old)

        assert adapter.name == "mock_old"
        assert len(adapter.events) == 2

        before_events = adapter.event_names_for_hook("before_model")
        assert "pre_action" in before_events

        after_events = adapter.event_names_for_hook("after_model")
        assert "post_action" in after_events

    @pytest.mark.asyncio
    async def test_adapter_delegates_to_old_handler(self):
        old = MockOldPlugin()
        adapter = PluginAdapter(old)

        def fake_call(messages, tools=None):
            return ModelResult(content="ok")

        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call)
        ctx = PluginContext(agent=agent, session_id="s1")

        await adapter.handle("pre_action", ctx)
        assert "pre_action" in old.calls

        await adapter.handle("post_action", ctx)
        assert "post_action" in old.calls
```

- [ ] **步骤 3：运行测试**

运行：`pytest tests/test_plugin_adapter.py -v`
预期：通过

- [ ] **步骤 4：提交**

```bash
git add arf/harness/adapter.py tests/test_plugin_adapter.py
git commit -m "feat(harness): add PluginAdapter for old→new plugin shim

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 7：HarnessConfig —— 加载 harness.yaml

**文件：**
- 新建：`arf/harness/config.py`
- 新建：`arf/harness/loader.py`

**接口：**
- `HarnessConfig` —— harness.yaml 的 Pydantic 模型
- `PluginLoader` —— 查找并解析 plugin.yaml 文件，返回 `Plugin` 实例
- `load_harness(config_path: str) -> tuple[HarnessConfig, list[Plugin]]`

- [ ] **步骤 1：编写配置模型 + 加载器**

```python
# arf/harness/config.py
"""HarnessConfig — Pydantic model for harness.yaml."""
from __future__ import annotations
from pydantic import BaseModel


class ToolSource(BaseModel):
    type: str                        # "directory" | "mcp" | "kernel"
    path: str = ""                   # for directory
    url: str = ""                    # for mcp
    names: list[str] = []            # for kernel


class HarnessConfig(BaseModel):
    plugins: list[str] = []
    tools: list[ToolSource] = []
    max_turns: int = 50
    tool_timeout: float = 60.0

    @classmethod
    def from_yaml(cls, path: str) -> HarnessConfig:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)
```

```python
# arf/harness/loader.py
"""PluginLoader — find and parse plugin.yaml files."""
from __future__ import annotations
from pathlib import Path
from typing import Any


def load_plugin_yaml(plugin_dir: str, name: str) -> dict | None:
    """Load a single plugin's plugin.yaml by name. Returns config dict or None."""
    path = Path(plugin_dir) / name / "plugin.yaml"
    if not path.exists():
        return None
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def discover_plugins(plugin_dir: str, enabled: list[str]) -> list[dict]:
    """Discover enabled plugins from plugin_dir. Returns list of plugin configs."""
    configs = []
    for name in enabled:
        cfg = load_plugin_yaml(plugin_dir, name)
        if cfg:
            cfg.setdefault("name", name)
            configs.append(cfg)
    return configs


def instantiate_plugins(configs: list[dict], plugin_classes: dict[str, type] | None = None) -> list[Any]:
    """Instantiate plugins from configs.

    Looks up plugin_classes by name. Falls back to importing from
    arf.plugins.<name> if not found in plugin_classes.
    """
    plugins = []
    for cfg in configs:
        name = cfg["name"]
        events = cfg.get("events", [])
        config = cfg.get("config", {})

        cls = None
        if plugin_classes and name in plugin_classes:
            cls = plugin_classes[name]

        if cls is not None:
            plugins.append(cls(name=name, events=events, config=config))
        else:
            # Try dynamic import
            try:
                mod = __import__(f"arf.plugins.{name}", fromlist=["Plugin"])
                plugin_cls = getattr(mod, "Plugin", None)
                if plugin_cls:
                    plugins.append(plugin_cls(name=name, events=events, config=config))
            except ImportError:
                pass

    return plugins
```

- [ ] **步骤 2：编写测试**

```python
# tests/test_harness_config.py
import tempfile
import os
from pathlib import Path
import pytest
from arf.harness.config import HarnessConfig
from arf.harness.loader import load_plugin_yaml, discover_plugins


class TestHarnessConfig:
    def test_load_minimal_config(self):
        yaml_content = """
plugins:
  - trace
max_turns: 20
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            cfg = HarnessConfig.from_yaml(path)
            assert cfg.plugins == ["trace"]
            assert cfg.max_turns == 20
        finally:
            os.unlink(path)


class TestPluginLoader:
    def test_discover_plugins(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        trace_dir = plugin_dir / "trace"
        trace_dir.mkdir(parents=True)
        (trace_dir / "plugin.yaml").write_text("""
name: trace
events:
  - {hook_name: "after_model", event_name: "trace_model", mode: "side"}
config:
  output: jsonl
""")

        configs = discover_plugins(str(plugin_dir), ["trace"])
        assert len(configs) == 1
        assert configs[0]["name"] == "trace"
        assert configs[0]["events"][0]["hook_name"] == "after_model"
```

- [ ] **步骤 3：运行测试**

运行：`pytest tests/test_harness_config.py -v`
预期：通过

- [ ] **步骤 4：提交**

```bash
git add arf/harness/config.py arf/harness/loader.py tests/test_harness_config.py
git commit -m "feat(harness): add HarnessConfig and plugin loader

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 8：简化 AgentConfig

**文件：**
- 修改：`arf/agent/config.py`

从 AgentConfig 中移除所有属于 harness 或插件的字段。仅保留：name、system_prompt、models 和 model_defs。

- [ ] **步骤 1：确定保留 vs 移除的字段**

当前的 `AgentConfig` 包含类似字段：name、models、model_defs、tools、skills、plugins、plugins_config、hooks、advanced、data_path、allow_paths、session_mode、mcp_servers。

简化后：name、system_prompt、models、model_defs。所有其他字段移至 harness.yaml 或插件配置。

- [ ] **步骤 2：编写简化的 AgentConfig**

修改 `arf/agent/config.py` —— 精简为仅 agent 配置。通过使移除的字段可选并带默认值（它们是空操作而非错误）来保持向后兼容。

- [ ] **步骤 3：验证现有测试仍然通过**

运行：`pytest tests/test_config.py -v`

- [ ] **步骤 4：提交**

```bash
git add arf/agent/config.py
git commit -m "refactor(agent): simplify AgentConfig to agent-only fields

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 9：集成 —— 完整管道测试

**文件：**
- 新建：`tests/test_harness_integration.py`

端到端测试：PrimitiveAgent + AgentHarness + ToolRegistry + ToolExecutor + 插件。

- [ ] **步骤 1：编写集成测试**

```python
# tests/test_harness_integration.py
import pytest
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.engine import AgentHarness
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor
from arf.event_bus import InMemoryEventBus


@pytest.mark.asyncio
async def test_full_pipeline_text_only():
    """PrimitiveAgent + Harness → single round, text output."""
    def fake_call(messages, tools=None):
        return ModelResult(content="Hello, world!", tool_calls=[], usage={"total_tokens": 10}, finish_reason="stop")

    # Track plugin calls
    class TestPlugin(Plugin):
        def __init__(self):
            super().__init__("test", [
                {"hook_name": "after_model", "event_name": "after_model_log", "mode": "side"},
            ])
            self.events_received: list[str] = []

        async def handle(self, event_name: str, ctx: PluginContext) -> None:
            self.events_received.append(event_name)

    plugin = TestPlugin()
    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call)
    bus = InMemoryEventBus()
    harness = AgentHarness(agent, plugins=[plugin], tool_executor=None, event_bus=bus)

    events = [e async for e in harness.run("hello")]

    # Agent has 2 messages: user input + assistant response
    assert len(agent.state.messages) == 2
    assert agent.state.messages[0].role == "user"
    assert agent.state.messages[1].role == "assistant"
    assert agent.state.messages[1].content == "Hello, world!"

    # Plugin was called
    assert "after_model_log" in plugin.events_received

    # Events were emitted
    assert bus.event_count() >= 1
    model_events = bus.collected("model_call_end")
    assert len(model_events) >= 1


@pytest.mark.asyncio
async def test_full_pipeline_with_tools():
    """PrimitiveAgent + Harness + ToolExecutor → tool call round trip."""
    turn = 0

    def fake_call(messages, tools=None):
        nonlocal turn
        turn += 1
        if turn == 1:
            return ModelResult(
                content="",
                tool_calls=[{"id": "t1", "name": "greet", "params": {"name": "World"}}],
                usage={}, finish_reason="tool_calls",
            )
        return ModelResult(content="Greeting sent!", tool_calls=[], usage={}, finish_reason="stop")

    registry = ToolRegistry()
    async def greet(name="", **kw):
        return {"greeting": f"Hello, {name}!"}
    registry.register("greet", {"name": "greet", "description": "Send greeting"}, greet)

    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call)
    harness = AgentHarness(agent, plugins=[], tool_executor=ToolExecutor(registry))

    events = [e async for e in harness.run("greet World")]

    # Messages: user, assistant(tool_calls), tool_result, assistant(response)
    assert len(agent.state.messages) == 4
    assert agent.state.messages[2].role == "tool"
    assert agent.state.messages[3].content == "Greeting sent!"

    tool_starts = [e for e in events if e.type == "tool_call_start"]
    tool_ends = [e for e in events if e.type == "tool_call_end"]
    assert len(tool_starts) == 1
    assert len(tool_ends) == 1
```

- [ ] **步骤 2：运行集成测试**

运行：`pytest tests/test_harness_integration.py -v`
预期：通过

- [ ] **步骤 3：提交**

```bash
git add tests/test_harness_integration.py
git commit -m "test(harness): add full pipeline integration tests

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### 任务 10：将 CompactionPlugin 迁移到新插件模型

**文件：**
- 新建：`arf/plugins/compaction/plugin.yaml`
- 新建：`arf/plugins/compaction/__init__.py`（或修改现有）
- 修改：`arf/compaction/sliding_window.py`（提取纯逻辑）

将 compaction 从旧钩子模型迁移到新的 Plugin 基类。核心压缩逻辑（SlidingWindowCompactor）保持不变。

- [ ] **步骤 1：创建 plugin.yaml**

```yaml
# arf/plugins/compaction/plugin.yaml
name: compaction
events:
  - {hook_name: "before_model", event_name: "compact", mode: "blocking"}
config:
  threshold: 500
  keep_recent: 10
```

- [ ] **步骤 2：编写新的 CompactionPlugin**

```python
# arf/plugins/compaction/__init__.py (新插件模块)
"""CompactionPlugin — compact messages before model call."""
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.compaction.sliding_window import SlidingWindowCompactor


class CompactionPlugin(Plugin):
    def __init__(self, name="compaction", events=None, config=None):
        super().__init__(name=name, events=events or [
            {"hook_name": "before_model", "event_name": "compact", "mode": "blocking"},
        ], config=config or {})
        threshold = self.config.get("threshold", 500)
        keep_recent = self.config.get("keep_recent", 10)
        self._compactor = SlidingWindowCompactor(threshold=threshold, keep_recent=keep_recent)

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "compact":
            await self._do_compact(ctx)

    async def _do_compact(self, ctx: PluginContext) -> None:
        messages = ctx.agent.state.messages
        # ... 使用 self._compactor 应用压缩逻辑 ...
        # compacted = self._compactor.compact(messages)
        # ctx.agent.state.messages = compacted
        pass  # 详细实现使用现有的 SlidingWindowCompactor 逻辑
```

- [ ] **步骤 3：在新 harness 上测试压缩**

- [ ] **步骤 4：提交**

---

### 任务 11：将 TracePlugin 迁移到新插件模型

与 compaction 类似 —— 创建 `arf/plugins/trace/plugin.yaml` 和新的 TracePlugin 类。

---

### 任务 12：移除旧代码

在关键插件迁移并验证后：
- 移除 `arf/engine/control_plane.py`
- 移除 `arf/agent/base.py`（旧 BaseAgent）
- 移除 `arf/core/plugin_context.py`（旧 PluginContext）
- 移除 `arf/core/plugin_runtime.py`
- 移除 `arf/core/primitives.py`（旧 Primitive/Level/PrimitiveHandler）
- 移除 `arf/engine/park_coordinator.py`
- 移除 `arf/engine/gate.py`
- 清理 `arf/agent/__init__.py` 导出
- 更新 `arf/__init__.py` 导出

---

## 自我评审清单（编写计划后）

1. **规范覆盖** —— 对照上述任务检查规范的每个部分：
   - [x] AgentState + Message + WaitItem + ModelResult → 任务 1
   - [x] 6 个原语（input/model_call/wait/finish_wait/stop/resume）→ 任务 2
   - [x] 7 个检查点 + 执行循环 → 任务 4
   - [x] Park/resume 机制 → 任务 4
   - [x] 插件模型（events 列表、handle、plugin.yaml）→ 任务 3、7
   - [x] PluginContext → 任务 3
   - [x] 配置分离 → 任务 7、8
   - [x] ToolExecutor（最小化，无验证）→ 任务 5
   - [x] ToolRegistry → 任务 5
   - [x] PluginAdapter（旧→新垫片）→ 任务 6
   - [x] 集成测试 → 任务 9

2. **占位符扫描** —— 任务 10–12 是骨架（实际迁移依赖于现有插件代码）。这是有意为之 —— 在不阅读每个插件的内部实现的情况下无法完全详细规划。

3. **类型一致性** —— `AgentState`、`Message`、`WaitItem`、`ModelResult` 在任务 1 中定义，在任务 2–9 中使用。`PluginContext` 在任务 3 中定义，在任务 4–11 中使用。`Plugin` 基类在任务 3 中定义，在任务 10–11 中被继承。签名保持一致。
