# Primitive Agent + Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace BaseAgent (~980 lines) + ControlPlane (~1338 lines) with PrimitiveAgent (6 primitives) + AgentHarness (execution skeleton + plugin scheduler + park/resume). All existing functionality becomes plugins.

**Architecture:** Build new files alongside old. Create foundation types first, then PrimitiveAgent, then AgentHarness with Plugin base. Provide a PluginAdapter shim so existing plugins run on the new harness during migration. Remove old code after critical plugins are ported.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, pytest. Reuses ModelAdapter, ModelDegrader, InMemoryEventBus, FileStateStore, ConcurrentToolExecutor, AgentEvent.

## Global Constraints

- Python 3.11+
- Pydantic v2 for configuration models
- All public API methods must have type annotations
- Protocol definitions in `arf/core/protocols/`
- Test doubles in `arf/testing/`
- Tests in `tests/`
- Commit style: `type(scope): description` with `Co-Authored-By: Claude Code with DeepSeek V4`
- Design spec: `docs/2026-06-20-primitive-agent-harness-design.md`

---

## File Map

| New / Modify | Path | Responsibility |
|-------------|------|----------------|
| NEW | `arf/agent/state.py` | AgentState, Message, WaitItem, ModelResult dataclasses |
| NEW | `arf/agent/primitive.py` | PrimitiveAgent — 6 primitives |
| NEW | `arf/harness/__init__.py` | Public API |
| NEW | `arf/harness/context.py` | PluginContext |
| NEW | `arf/harness/plugin_base.py` | Plugin base class |
| NEW | `arf/harness/engine.py` | AgentHarness — execution loop |
| NEW | `arf/harness/config.py` | HarnessConfig from harness.yaml |
| NEW | `arf/harness/loader.py` | Plugin YAML loader + event registration |
| NEW | `arf/tooling/__init__.py` | Public API |
| NEW | `arf/tooling/executor.py` | ToolExecutor — minimal, no validation |
| NEW | `arf/tooling/registry.py` | ToolRegistry — aggregate tools from sources |
| MODIFY | `arf/agent/config.py` | Simplify AgentConfig (remove plugin fields) |
| MODIFY | `arf/agent/__init__.py` | Export PrimitiveAgent, AgentState |
| NEW | `arf/harness/adapter.py` | PluginAdapter — old plugin → new Plugin shim |
| NEW | `arf/plugins/base.py` | Re-export Plugin from harness |
| NEW | `tests/test_agent_state.py` | Unit tests for state types |
| NEW | `tests/test_primitive_agent.py` | Unit tests for PrimitiveAgent |
| NEW | `tests/test_harness_engine.py` | Integration tests for AgentHarness |
| NEW | `tests/test_plugin_loading.py` | Plugin loading + registration tests |
| NEW | `tests/test_harness_park.py` | Park/resume tests |
| NEW | `tests/test_plugin_adapter.py` | Adapter shim tests |

---

### Task 1: Foundation Types — AgentState, Message, WaitItem, ModelResult

**Files:**
- Create: `arf/agent/state.py`
- Create: `tests/test_agent_state.py`

**Interfaces:**
- Produces: `AgentState`, `Message`, `WaitItem`, `ModelResult` dataclasses

- [ ] **Step 1: Write failing test for state creation**

```python
# tests/test_agent_state.py
import pytest
from arf.agent.state import AgentState, Message, WaitItem, ModelResult


def test_create_empty_agent_state():
    state = AgentState(
        agent_id="test-agent",
        session_id="s1",
        messages=[],
        waiting={},
        model_config={"api_base": "https://x.com/v1", "api_key_env": "KEY", "model_name": "m1", "context_window": 128000},
    )
    assert state.agent_id == "test-agent"
    assert state.session_id == "s1"
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

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_state.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write `arf/agent/state.py`**

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

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_state.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add arf/agent/state.py tests/test_agent_state.py
git commit -m "feat(agent): add AgentState, Message, WaitItem, ModelResult types

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 2: PrimitiveAgent — 6 Primitives

**Files:**
- Create: `arf/agent/primitive.py`
- Create: `tests/test_primitive_agent.py`
- Create: `tests/fixtures/fake_model_adapter.py` (or reuse existing)

**Interfaces:**
- Consumes: `AgentState`, `Message`, `WaitItem`, `ModelResult` from `arf/agent/state.py`
- Produces: `PrimitiveAgent` class with methods:
  - `__init__(self, agent_id: str, session_id: str, model_config: dict, call_model: Callable)`
  - `input(self, role: str, content: Any, position: str = "end") -> Message`
  - `async model_call(self) -> ModelResult`
  - `wait(self, hook_name: str, reason: str) -> WaitItem`
  - `finish_wait(self, wait_id: str, reason: str = "") -> dict[str, list[WaitItem]]`
  - `stop(self) -> AgentState`
  - `classmethod resume(cls, state: AgentState, call_model: Callable) -> PrimitiveAgent`

- [ ] **Step 1: Write failing tests**

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
        session_id="s1",
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

        ag = PrimitiveAgent("a1", "s1",
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
        ag1 = PrimitiveAgent("a1", "s1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ag1.input("user", "msg1")
        ag1.wait("before_tools", "approval")
        state = ag1.stop()

        ag2 = PrimitiveAgent.resume(state, fake_call_model)
        assert ag2.state.agent_id == "a1"
        assert ag2.state.session_id == "s1"
        assert len(ag2.state.messages) == 1
        assert ag2.state.messages[0].content == "msg1"
        assert len(ag2.state.waiting["before_tools"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_primitive_agent.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write `arf/agent/primitive.py`**

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
        session_id: str,
        model_config: dict,
        call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
    ) -> None:
        self.state = AgentState(
            agent_id=agent_id,
            session_id=session_id,
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
        """Reconstruct agent from state, including model connection."""
        agent = cls(
            agent_id=state.agent_id,
            session_id=state.session_id,
            model_config=state.model_config,
            call_model=call_model,
        )
        agent.state = state
        agent._active = True
        return agent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_primitive_agent.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add arf/agent/primitive.py tests/test_primitive_agent.py
git commit -m "feat(agent): add PrimitiveAgent with 6 primitives

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 3: PluginContext + Plugin Base Class

**Files:**
- Create: `arf/harness/__init__.py`
- Create: `arf/harness/context.py`
- Create: `arf/harness/plugin_base.py`
- Create: `tests/test_plugin_loading.py`

**Interfaces:**
- Consumes: `PrimitiveAgent` from `arf/agent/primitive.py`, `AgentEvent` from `arf/core/events.py`
- Produces:
  - `PluginContext(agent, hook_data, session_id, event_bus)` — with `emit(event_type, data)` method
  - `Plugin` base class with `name: str`, `events: list[dict]`, `async handle(event_name, ctx)`

- [ ] **Step 1: Write PluginContext**

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

- [ ] **Step 2: Write test for plugin registration**

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

        agent = PrimitiveAgent("a1", "s1",
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

        agent = PrimitiveAgent("a1", "s1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call)
        bus = InMemoryEventBus()
        ctx = PluginContext(agent=agent, session_id="s1", event_bus=bus)
        ctx.emit("test_event", {"key": "value"})
        assert bus.event_count() == 1
        assert bus.collected("test_event")[0].data["key"] == "value"
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_plugin_loading.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add arf/harness/ tests/test_plugin_loading.py
git commit -m "feat(harness): add PluginContext and Plugin base class

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 4: AgentHarness — Execution Loop + Checkpoint Scheduling

**Files:**
- Create: `arf/harness/engine.py`
- Create: `tests/test_harness_engine.py`

**Interfaces:**
- Consumes: `PrimitiveAgent`, `Plugin`, `PluginContext`, `AgentEvent`
- Produces: `AgentHarness` class:
  - `__init__(self, agent, plugins, tool_executor, event_bus, max_turns)`
  - `async run(self, user_message: str) -> AsyncIterator[AgentEvent]`
  - `async resolve_wait(self, wait_id: str, inject_message: dict | None) -> bool`

- [ ] **Step 1: Write AgentHarness**

```python
# arf/harness/engine.py
"""AgentHarness — execution skeleton + plugin scheduler + park/resume."""
from __future__ import annotations
import asyncio
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

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """Main execution loop. Yields AgentEvent for SSE streaming."""
        agent = self.agent
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

- [ ] **Step 2: Write integration test**

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


def make_agent(call_model, agent_id="a1", session_id="s1"):
    return PrimitiveAgent(
        agent_id=agent_id,
        session_id=session_id,
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

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_harness_engine.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add arf/harness/engine.py tests/test_harness_engine.py
git commit -m "feat(harness): add AgentHarness with execution loop and park/resume

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 5: ToolExecutor — Minimal, No Validation

**Files:**
- Create: `arf/tooling/__init__.py`
- Create: `arf/tooling/executor.py`
- Create: `arf/tooling/registry.py`

**Interfaces:**
- Consumes: `MCP` tool definitions (reuse existing McpClientManager)
- Produces:
  - `ToolRegistry(name, sources)` — aggregate tool definitions from directory + MCP + kernel
  - `ToolExecutor(registry)` — `async execute(tool_calls) -> dict[str, ToolResult]`

ToolExecutor is **minimal**: resolve tool → execute → return results. No validation, no guardrails, no path resolution — those are plugins.

- [ ] **Step 1: Write ToolRegistry + ToolExecutor**

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

- [ ] **Step 2: Write test**

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

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_tooling.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add arf/tooling/ tests/test_tooling.py
git commit -m "feat(tooling): add ToolRegistry and minimal ToolExecutor

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 6: PluginAdapter — Old Plugins on New Harness

**Files:**
- Create: `arf/harness/adapter.py`
- Create: `tests/test_plugin_adapter.py`

**Interfaces:**
- Consumes: Old-style plugin classes (objects with `name`, `hooks` dict, and hook handler methods)
- Produces: `PluginAdapter` wrapping old plugin as new `Plugin` interface

This is temporary scaffolding so we can run existing plugins (compaction, trace, approval) on the new harness while porting them.

- [ ] **Step 1: Write PluginAdapter**

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

- [ ] **Step 2: Test adapter with a mock old plugin**

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

        agent = PrimitiveAgent("a1", "s1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call)
        ctx = PluginContext(agent=agent, session_id="s1")

        await adapter.handle("pre_action", ctx)
        assert "pre_action" in old.calls

        await adapter.handle("post_action", ctx)
        assert "post_action" in old.calls
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_plugin_adapter.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add arf/harness/adapter.py tests/test_plugin_adapter.py
git commit -m "feat(harness): add PluginAdapter for old→new plugin shim

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 7: HarnessConfig — Load harness.yaml

**Files:**
- Create: `arf/harness/config.py`
- Create: `arf/harness/loader.py`

**Interfaces:**
- `HarnessConfig` — Pydantic model for harness.yaml
- `PluginLoader` — find and parse plugin.yaml files, return `Plugin` instances
- `load_harness(config_path: str) -> tuple[HarnessConfig, list[Plugin]]`

- [ ] **Step 1: Write config models + loader**

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

- [ ] **Step 2: Write test**

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

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_harness_config.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add arf/harness/config.py arf/harness/loader.py tests/test_harness_config.py
git commit -m "feat(harness): add HarnessConfig and plugin loader

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 8: Simplify AgentConfig

**Files:**
- Modify: `arf/agent/config.py`

Strip from AgentConfig all fields that belong to harness or plugins. Keep only: name, system_prompt, models, and model_defs.

- [ ] **Step 1: Identify fields to keep vs remove**

Current `AgentConfig` has fields like: name, models, model_defs, tools, skills, plugins, plugins_config, hooks, advanced, data_path, allow_paths, session_mode, mcp_servers.

After simplification: name, system_prompt, models, model_defs. All other fields move to harness.yaml or plugin config.

- [ ] **Step 2: Write simplified AgentConfig**

Modify `arf/agent/config.py` — strip to minimal agent config. Keep backward compat by making removed fields optional with defaults (they're no-ops, not errors).

- [ ] **Step 3: Verify existing tests still pass**

Run: `pytest tests/test_config.py -v`

- [ ] **Step 4: Commit**

```bash
git add arf/agent/config.py
git commit -m "refactor(agent): simplify AgentConfig to agent-only fields

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 9: Integration — Full Pipeline Test

**Files:**
- Create: `tests/test_harness_integration.py`

End-to-end test: PrimitiveAgent + AgentHarness + ToolRegistry + ToolExecutor + plugins.

- [ ] **Step 1: Write integration test**

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
    agent = PrimitiveAgent("a1", "s1",
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

    agent = PrimitiveAgent("a1", "s1",
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

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_harness_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_harness_integration.py
git commit -m "test(harness): add full pipeline integration tests

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 10: Port CompactionPlugin to New Plugin Model

**Files:**
- Create: `arf/plugins/compaction/plugin.yaml`
- Create: `arf/plugins/compaction/__init__.py` (or modify existing)
- Modify: `arf/compaction/sliding_window.py` (extract pure logic)

Port compaction from old hook model to new Plugin base class. Core compaction logic (SlidingWindowCompactor) is unchanged.

- [ ] **Step 1: Create plugin.yaml**

```yaml
# arf/plugins/compaction/plugin.yaml
name: compaction
events:
  - {hook_name: "before_model", event_name: "compact", mode: "blocking"}
config:
  threshold: 500
  keep_recent: 10
```

- [ ] **Step 2: Write new CompactionPlugin**

```python
# arf/plugins/compaction/__init__.py (new plugin module)
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
        # ... apply compaction logic using self._compactor ...
        # compacted = self._compactor.compact(messages)
        # ctx.agent.state.messages = compacted
        pass  # detailed implementation uses existing SlidingWindowCompactor logic
```

- [ ] **Step 3: Test compaction on new harness**

- [ ] **Step 4: Commit**

---

### Task 11: Port TracePlugin to New Plugin Model

Similar to compaction — create `arf/plugins/trace/plugin.yaml` and new TracePlugin class.

---

### Task 12: Remove Old Code

After critical plugins are ported and verified:
- Remove `arf/engine/control_plane.py`
- Remove `arf/agent/base.py` (old BaseAgent)
- Remove `arf/core/plugin_context.py` (old PluginContext)
- Remove `arf/core/plugin_runtime.py`
- Remove `arf/core/primitives.py` (old Primitive/Level/PrimitiveHandler)
- Remove `arf/engine/park_coordinator.py`
- Remove `arf/engine/gate.py`
- Clean up `arf/agent/__init__.py` exports
- Update `arf/__init__.py` exports

---

## Self-Review Checklist (after writing plan)

1. **Spec coverage** — Check each section of the spec against tasks above:
   - [x] AgentState + Message + WaitItem + ModelResult → Task 1
   - [x] 6 Primitives (input/model_call/wait/finish_wait/stop/resume) → Task 2
   - [x] 7 Checkpoints + execution loop → Task 4
   - [x] Park/resume mechanism → Task 4
   - [x] Plugin model (events list, handle, plugin.yaml) → Tasks 3, 7
   - [x] PluginContext → Task 3
   - [x] Configuration separation → Tasks 7, 8
   - [x] ToolExecutor (minimal, no validation) → Task 5
   - [x] ToolRegistry → Task 5
   - [x] PluginAdapter (old→new shim) → Task 6
   - [x] Integration test → Task 9

2. **Placeholder scan** — Tasks 10-12 are skeleton (actual migration depends on existing plugin code). This is intentional — they can't be fully specced without reading each plugin's internals.

3. **Type consistency** — `AgentState`, `Message`, `WaitItem`, `ModelResult` defined in Task 1, used throughout Tasks 2-9. `PluginContext` defined in Task 3, used in Tasks 4-11. `Plugin` base defined in Task 3, subclassed in Tasks 10-11. Signatures match.
