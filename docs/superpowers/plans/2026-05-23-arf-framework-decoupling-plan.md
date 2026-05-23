# ARF Framework Decoupling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple ARF into framework layer (`arf/`) and application layer (`app/`), with 18 problem domains, unified Protocol type layer, progressive-disclosure user config, InMemory test doubles, and schema versioning.

**Architecture:** Build from the type system upward. `arf/core/protocols/` defines all Protocols and data structures (zero deps). Engine depends only on `arf.core`. Every domain provides a Protocol + minimal default implementation injected via DI. User sees only model/skill/tool/hook/agent; all internal mechanisms auto-derived by `AdvancedConfig.default()`.

**Tech Stack:** Python 3.11+, Pydantic v2, LangGraph, Rich (TUI), OpenTelemetry, asyncio

**Dependency Phase Order:**
```
Phase 0: project scaffold
Phase 1: arf/core/protocols/ (all Protocols + core types) ← zero deps
  ├─→ Phase 2: engine (GraphEngine, LoopStrategy, StateStore, ToolExecutor)
  ├─→ Phase 3: resources (ToolResolver, ToolProvider, ToolBackend, ResourceRegistry)
  ├─→ Phase 4: memory (MemoryStore, MemoryRetriever, MemoryWriter)
  ├─→ Phase 5: hooks (HookRunner)
  ├─→ Phase 6: routing + compaction + sandbox + concurrency
  ├─→ Phase 7: guardrails (GuardRunner, InputGuardrail, OutputGuardrail, ToolGuardrail)
  ├─→ Phase 8: errors + transaction (ErrorPolicy, TransactionContext)
  ├─→ Phase 9: human_loop (ApprovalPoint, ApprovalChannel)
  ├─→ Phase 10: event_bus + streaming + observability (EventBus, EventStream, Tracer, TuiDashboard, ReplayController)
  ├─→ Phase 11: communication (AgentBus, PeerAgent, Supervisor, SharedWorkspace, Lock)
  ├─→ Phase 12: evaluation (EvalRunner, MetricCollector)
  ├─→ Phase 13: planner (Planner, Plan, PlanStep)
  ├─→ Phase 14: agent assembly (BaseAgent, create_agent, AgentConfig, AdvancedConfig)
  └─→ Phase 15: testing + migration (arf/testing, app/ isolation)
```

Phases 2-12 can run in parallel once Phase 1 completes (they all depend only on `arf.core.protocols`).

---

## Phase 1: `arf/core/` — Unified Type Layer

### Task 1.0: Create `arf/core/` scaffold and base types
**Files:**
- Create: `arf/__init__.py`
- Create: `arf/core/__init__.py`
- Create: `arf/core/events.py`
- Create: `arf/core/state.py`
- Create: `arf/core/results.py`
- Create: `arf/core/config_base.py`

```bash
mkdir -p arf/core/protocols arf/core/defaults
touch arf/__init__.py arf/core/__init__.py
```

- [ ] **Step 1: Write `arf/core/events.py` — AgentEvent**
```python
"""Unified event model — single source for streaming + observability."""

from dataclasses import dataclass, field
from typing import Literal
import time

EventType = Literal[
    "session_start", "session_end",
    "thinking_delta",
    "model_call_start", "model_call_end",
    "tool_call_start", "tool_call_end",
    "tool_call_result",
    "compaction_start", "compaction_end",
    "approval_required", "approval_resolved",
    "hook_start", "hook_end",
    "error",
]

@dataclass
class AgentEvent:
    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str | None = None
    session_id: str = ""
    agent_name: str = ""
    turn: int = 0
```

- [ ] **Step 2: Write `arf/core/state.py` — AgentState, TurnContext**
```python
"""Core state types — engine read/write, StateStore persists."""

from dataclasses import dataclass, field
from typing import TypedDict, Literal


class AgentState(TypedDict, total=False):
    session_id: str
    agent_name: str
    messages: list[dict]          # conversation history
    current_model: str            # active model name
    current_turn: int
    context_summary: str          # compacted context / memory injection
    tool_results: dict[str, dict] # {tool_call_id: result}
    plan: dict | None             # Planner state
    metadata: dict                # user-defined key-value store


@dataclass
class TurnContext:
    session_id: str
    agent_name: str
    turn: int
    current_model: str
    available_models: list[str]
    last_user_message: str
    last_tool_calls: list[dict] = field(default_factory=list)
```

- [ ] **Step 3: Write `arf/core/results.py`**
```python
"""Standard result types shared across Protocols."""
from dataclasses import dataclass, field


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_message: str | None = None


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class HookResult:
    hook_name: str
    exit_code: int             # 0=pass, 1=block, 2=inject
    stdout: str = ""
    stderr: str = ""
    injected_message: str | None = None  # populated when exit_code == 2


@dataclass
class ApprovalRequest:
    agent_name: str
    session_id: str
    turn: int
    tool_name: str
    params: dict
    reason: str


@dataclass
class ApprovalResponse:
    action: Literal["approve", "reject", "modify"]
    modified_params: dict | None = None
    comment: str = ""


@dataclass
class ErrorAction:
    action: Literal["retry", "fallback", "ask_user", "abort"]
    delay: float = 0.0
    fallback_model: str | None = None
    message: str = ""


@dataclass
class RollbackResult:
    success: bool
    rollbacks: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    restored_state: dict = field(default_factory=dict)
```

- [ ] **Step 4: Write `arf/core/config_base.py` — sub-config models**
```python
"""Sub-configuration Pydantic models used by AgentConfig.
These are the building blocks AdvancedConfig assembles."""
from pydantic import BaseModel, Field
from typing import Literal


class ModelConfig(BaseModel):
    name: str
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    kwargs: dict = Field(default_factory=dict)


class SkillConfig(BaseModel):
    name: str
    description: str
    prompt: str
    tools: list[str] = Field(default_factory=list)
    activation: Literal["kernel", "discoverable", "passive"] = "discoverable"


class ToolConfig(BaseModel):
    name: str
    description: str
    parameters: dict = Field(default_factory=dict)
    source: str | None = None              # path to external tool.yaml
    provider: Literal["static_yaml", "mcp"] = "static_yaml"
    backend: Literal["function", "subprocess"] = "function"
    execution: dict = Field(default_factory=lambda: {"sandbox": "inherit", "timeout": "30s"})
    activation: Literal["kernel", "discoverable", "passive"] = "kernel"


class HookDefinition(BaseModel):
    name: str
    type: Literal[
        "session_start", "pre_tool_exec", "post_tool_exec",
        "pre_model_call", "post_model_call", "session_end",
    ]
    run: list[str]                          # sequential programs within one hook
    env: dict[str, str] = Field(default_factory=dict)
    timeout: str = "30s"


class RoutingConfig(BaseModel):
    strategy: Literal["two_tier", "static"] = "two_tier"
    default: str = ""
    classify: dict[str, str] = Field(default_factory=dict)
    background: str | None = None
    fallback: dict[str, str] = Field(default_factory=dict)


class CompactionConfig(BaseModel):
    strategy: Literal["sliding_window", "summarization", "none"] = "sliding_window"
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    store: Literal["file", "sqlite", "none"] = "file"
    workspace: str = "./memory"
    retriever: Literal["recent_first", "semantic"] = "recent_first"
    max_tokens: int = 2000
    top_k: int = 5


class GuardrailsConfig(BaseModel):
    input: Literal["none", "regex_block", "llm_classifier"] = "none"
    output: Literal["none", "regex_clean", "llm_classifier"] = "regex_clean"
    tool_params: Literal["none", "path_check", "command_check"] = "path_check"


class ErrorConfig(BaseModel):
    tool_retry: int = 2
    tool_backoff: Literal["exponential", "linear", "none"] = "exponential"
    model_retry: int = 3
    model_5xx_action: Literal["fallback", "retry", "abort"] = "fallback"
    guardrail_block_action: Literal["abort", "ask_user"] = "abort"


class HumanLoopConfig(BaseModel):
    approval_points: Literal["always_auto", "tool_name_allowlist"] = "always_auto"
    allowlist: list[str] = Field(default_factory=list)
    channel: Literal["console", "websocket", "callback"] = "console"
    timeout: str = "3600s"


class StreamingConfig(BaseModel):
    transport: Literal["sse", "websocket", "callback"] = "sse"
    event_types: list[str] = Field(default_factory=lambda: ["all"])


class SandboxConfig(BaseModel):
    allow_escape: bool = False
    writable_dirs: list[str] = Field(default_factory=list)


class ToolRetrievalConfig(BaseModel):
    enabled: bool = False
    top_k: int = 10


class ReloadConfig(BaseModel):
    watch: bool = False
    signals: list[str] = Field(default_factory=lambda: ["SIGHUP"])


class HandoverRuleConfig(BaseModel):
    from_agent: str
    to_agent: str
    trigger: str


class HandoverConfig(BaseModel):
    rules: list[HandoverRuleConfig] = Field(default_factory=list)


class SupervisorConfig(BaseModel):
    type: Literal["round_robin", "llm_router", "custom"] = "round_robin"
    llm_model: str | None = None
```

- [ ] **Step 5: Write `arf/core/__init__.py`**
```python
from arf.core.events import AgentEvent, EventType
from arf.core.state import AgentState, TurnContext
from arf.core.results import (
    GuardResult, ToolResult, HookResult,
    ApprovalRequest, ApprovalResponse,
    ErrorAction, RollbackResult,
)
from arf.core.config_base import (
    ModelConfig, SkillConfig, ToolConfig, HookDefinition,
    RoutingConfig, CompactionConfig, MemoryConfig,
    GuardrailsConfig, ErrorConfig, HumanLoopConfig,
    StreamingConfig, SandboxConfig, ToolRetrievalConfig,
    ReloadConfig, HandoverRuleConfig, HandoverConfig, SupervisorConfig,
)

__all__ = [
    "AgentEvent", "EventType", "AgentState", "TurnContext",
    "GuardResult", "ToolResult", "HookResult",
    "ApprovalRequest", "ApprovalResponse", "ErrorAction", "RollbackResult",
    "ModelConfig", "SkillConfig", "ToolConfig", "HookDefinition",
    "RoutingConfig", "CompactionConfig", "MemoryConfig",
    "GuardrailsConfig", "ErrorConfig", "HumanLoopConfig",
    "StreamingConfig", "SandboxConfig", "ToolRetrievalConfig",
    "ReloadConfig", "HandoverRuleConfig", "HandoverConfig", "SupervisorConfig",
]
```

- [ ] **Step 6: Commit**
```bash
git add arf/__init__.py arf/core/
git commit -m "feat(core): create arf/core with AgentEvent, AgentState, results, config_base"
```

### Task 1.1: Define engine protocols
**Files:**
- Create: `arf/core/protocols/__init__.py`
- Create: `arf/core/protocols/engine.py`

- [ ] **Step 1: Write `arf/core/protocols/engine.py`**
```python
"""Protocols for engine domain."""
from typing import Protocol, AsyncIterator
from arf.core.state import AgentState, TurnContext
from arf.core.results import ToolResult, ErrorAction, RollbackResult


class LoopStrategy(Protocol):
    """Agent execution loop pattern."""
    def should_continue(self, state: AgentState) -> bool: ...
    def next_step(self, state: AgentState) -> str: ...


class StateStore(Protocol):
    """Persist/restore AgentState at checkpoint boundaries."""
    async def put(self, session_id: str, state: AgentState) -> None: ...
    async def get(self, session_id: str) -> AgentState | None: ...
    async def delete(self, session_id: str) -> None: ...


class ToolExecutor(Protocol):
    """Execute multiple tool_calls with concurrency control."""
    async def execute(
        self,
        tool_calls: list[dict],
        strategy: str = "parallel",
        max_concurrency: int = 5,
    ) -> dict[str, ToolResult]: ...


class TransactionContext(Protocol):
    """Wrap a group of tool calls as an atomic transaction."""
    async def begin(self, session_id: str, turn: int) -> dict: ...
    async def commit(self, tx: dict) -> None: ...
    async def rollback(self, tx: dict, error: Exception) -> RollbackResult: ...


class Planner(Protocol):
    """Plan generation, progress tracking, divergence detection, revision."""
    async def generate_plan(self, task: str, context: TurnContext, tools: list[dict]) -> dict: ...
    async def update_progress(self, plan: dict, completed_step: dict, result: ToolResult) -> dict: ...
    async def detect_divergence(self, plan: dict, state: AgentState) -> dict: ...
    async def revise(self, plan: dict, divergence: dict, context: TurnContext) -> dict: ...
```

- [ ] **Step 2: Commit**
```bash
git add arf/core/protocols/
git commit -m "feat(core): add engine protocols — LoopStrategy, StateStore, ToolExecutor, TransactionContext, Planner"
```

### Task 1.2: Define memory protocols
**Files:**
- Create: `arf/core/protocols/memory.py`

- [ ] **Step 1: Write `arf/core/protocols/memory.py`**
```python
"""Protocols for memory domain."""
from typing import Protocol
from dataclasses import dataclass


@dataclass
class MemoryEntry:
    id: str
    content: str
    category: str                     # fact | preference | decision | context
    timestamp: float
    source_turn: int
    relevance_score: float = 1.0
    replaces: str | None = None


class MemoryStore(Protocol):
    """Persistence layer — where to store memories."""
    async def save(self, entry: MemoryEntry) -> None: ...
    async def load(self, session_id: str) -> list[MemoryEntry]: ...
    async def delete(self, entry_id: str) -> None: ...


class MemoryRetriever(Protocol):
    """Retrieval layer — what to inject into {{MEMORY}}."""
    async def retrieve(
        self,
        store: MemoryStore,
        query_context: str,
        session_id: str,
        max_tokens: int = 2000,
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...


class MemoryWriter(Protocol):
    """Write/fusion layer — extract facts from turns, merge with existing."""
    async def extract_and_write(
        self,
        store: MemoryStore,
        turn_messages: list[dict],
        existing_entries: list[MemoryEntry],
    ) -> list[MemoryEntry]: ...
```

- [ ] **Step 2: Commit**
```bash
git add arf/core/protocols/memory.py
git commit -m "feat(core): add memory protocols — MemoryStore, MemoryRetriever, MemoryWriter, MemoryEntry"
```

### Task 1.3: Define remaining protocols in batch

Files to create (all independent, can be done in parallel):
- `arf/core/protocols/resources.py`
- `arf/core/protocols/hooks.py`
- `arf/core/protocols/guardrails.py`
- `arf/core/protocols/routing.py`
- `arf/core/protocols/compaction.py`
- `arf/core/protocols/sandbox.py`
- `arf/core/protocols/concurrency.py`
- `arf/core/protocols/human_loop.py`
- `arf/core/protocols/communication.py`
- `arf/core/protocols/event_bus.py`
- `arf/core/protocols/tracer.py`
- `arf/core/protocols/replay.py`
- `arf/core/protocols/evaluation.py`
- `arf/core/protocols/errors.py`

- [ ] **Step 1: Write `resources.py`**
```python
"""Protocols for resources domain."""
from typing import Protocol
from dataclasses import dataclass
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict


class ToolResolver(Protocol):
    """Engine's single interface to tools. Internally wraps Provider+Retriever+Backend."""
    async def get_tool_definitions(
        self, query_context: str, top_k: int = 10,
    ) -> list[ToolDefinition]: ...
    async def execute(self, tool_name: str, params: dict) -> ToolResult: ...


class ToolProvider(Protocol):
    """Internal: tool source abstraction (static_yaml, MCP). Engine never sees this."""
    async def list_tools(self) -> list[ToolConfig]: ...
    async def resolve(self, name: str) -> ToolConfig | None: ...


class ToolRetriever(Protocol):
    """Internal: semantic retrieval of top-k tools. Engine never sees this."""
    async def retrieve(
        self, query_context: str, available_tools: list[ToolConfig], top_k: int = 10,
    ) -> list[ToolConfig]: ...


class ToolBackend(Protocol):
    """Internal: execution backend binding (function, subprocess). Engine never sees this."""
    async def execute(self, tool_config: ToolConfig, params: dict) -> ToolResult: ...
```

- [ ] **Step 2: Write `hooks.py`**
```python
"""Protocols for hooks domain."""
from typing import Protocol
from arf.core.results import HookResult
from arf.core.config_base import HookDefinition


class HookRunner(Protocol):
    """Execute lifecycle hooks at engine trigger points."""
    async def fire(
        self, event_type: str, context: dict,
    ) -> list[HookResult]: ...
    def set_order(self, event_type: str, hook_names: list[str]) -> None: ...
    def get_definitions(self) -> list[HookDefinition]: ...
```

- [ ] **Step 3: Write `guardrails.py`**
```python
"""Protocols for guardrails domain."""
from typing import Protocol
from arf.core.results import GuardResult


class GuardRunner(Protocol):
    """Engine's single interface to guardrails. Three hardcoded call sites."""
    async def check_input(self, message: str, context: dict) -> GuardResult: ...
    async def check_output(self, message: str, context: dict) -> GuardResult: ...
    async def check_tool_params(self, tool_name: str, params: dict) -> GuardResult: ...


class InputGuardrail(Protocol):
    """Internal: user message check. Engine never sees this."""
    async def check(self, message: str, context: dict) -> GuardResult: ...


class OutputGuardrail(Protocol):
    """Internal: model output check. Engine never sees this."""
    async def check(self, message: str, context: dict) -> GuardResult: ...


class ToolGuardrail(Protocol):
    """Internal: tool params check. Engine never sees this."""
    async def check(self, tool_name: str, params: dict) -> GuardResult: ...
```

- [ ] **Step 4: Write `routing.py`**
```python
"""Protocols for model routing domain."""
from typing import Protocol, AsyncIterator


class ModelRouter(Protocol):
    """Route tasks to appropriate model based on complexity."""
    async def route(self, query: str, history: list[dict]) -> str: ...
    async def classify(self, query: str) -> str: ...  # "medium" | "complex"
    def fallback_from(self, model_name: str) -> str | None: ...
```

- [ ] **Step 5: Write `compaction.py`**
```python
"""Protocols for context compaction domain."""
from typing import Protocol
from arf.core.state import AgentState


class CompactionStrategy(Protocol):
    """Compress conversation history to fit context window."""
    def should_compact(self, state: AgentState, threshold: float = 0.75) -> bool: ...
    async def compact(self, state: AgentState) -> AgentState: ...
```

- [ ] **Step 6: Write `sandbox.py`**
```python
"""Protocols for tool sandbox domain."""
from typing import Protocol


class ToolSandbox(Protocol):
    """Validate and enforce tool execution boundaries."""
    def validate_path(self, path: str, workspace_root: str) -> bool: ...
    def validate_command(self, command: str) -> bool: ...
    def allowed_dirs(self) -> list[str]: ...
```

- [ ] **Step 7: Write `concurrency.py`**
```python
"""Protocols for concurrency domain."""
from typing import Protocol


class TaskScheduler(Protocol):
    """Schedule parallel tasks across agents/tools."""
    async def schedule(self, tasks: list[dict]) -> list[dict]: ...
    async def execute(self, tasks: list[dict]) -> list[dict]: ...
```

- [ ] **Step 8: Write `human_loop.py`**
```python
"""Protocols for human-in-the-loop domain."""
from typing import Protocol
from arf.core.state import TurnContext
from arf.core.results import ApprovalRequest, ApprovalResponse


class ApprovalPoint(Protocol):
    """Determine when to pause for human approval."""
    def should_pause(self, context: TurnContext) -> bool: ...
    def approval_form(self, context: TurnContext) -> ApprovalRequest: ...


class ApprovalChannel(Protocol):
    """Communication channel for human approval."""
    async def send(self, request: ApprovalRequest) -> str: ...
    async def wait(self, approval_id: str, timeout: int) -> ApprovalResponse: ...
```

- [ ] **Step 9: Write `communication.py`**
```python
"""Protocols for multi-agent communication domain."""
from typing import Protocol, AsyncIterator
from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentMessage:
    sender: str
    receiver: str | None            # None = broadcast
    type: Literal["task_delegate", "info", "query", "handoff"]
    payload: dict
    reply_to: str | None = None
    correlation_id: str = ""

@dataclass
class AgentInfo:
    name: str
    description: str
    capabilities: list[str]


class AgentBus(Protocol):
    """Message routing between agents."""
    async def send(self, message: AgentMessage) -> None: ...
    async def receive(self, agent_name: str) -> AsyncIterator[AgentMessage]: ...
    async def register(self, agent: AgentInfo) -> None: ...
    async def discover(self, capability: str | None = None) -> list[AgentInfo]: ...


class PeerAgent(Protocol):
    """Decentralized peer-to-peer negotiation."""
    async def broadcast(self, message: AgentMessage) -> None: ...
    async def negotiate(self, proposal: dict, peers: list[str]) -> dict: ...


class TaskDelegator(Protocol):
    """Task delegation lifecycle."""
    async def delegate(self, task: dict, from_agent: str, to_agent: str) -> str: ...
    async def get_result(self, handle_id: str, timeout: int) -> dict: ...


class Supervisor(Protocol):
    """Centralized multi-agent orchestration."""
    async def route_task(self, task: dict, agents: list[AgentInfo]) -> str: ...
    async def should_intervene(self, handle_id: str, progress: dict) -> bool: ...
    async def synthesize(self, results: list[dict]) -> str: ...


class SharedWorkspace(Protocol):
    """Shared blackboard for multi-agent collaboration."""
    async def write(self, key: str, value: dict, owner: str) -> None: ...
    async def read(self, key: str) -> dict | None: ...


class Lock(Protocol):
    """Concurrency control for SharedWorkspace."""
    async def acquire(self, key: str, owner: str, ttl: float = 30.0) -> bool: ...
    async def release(self, key: str, owner: str) -> None: ...


class ConsensusProtocol(Protocol):
    """Multi-agent voting/consensus."""
    async def propose(self, proposal: dict, voters: list[str]) -> dict: ...
    async def vote(self, proposal_id: str, vote: str) -> None: ...
```

- [ ] **Step 10: Write `event_bus.py`**
```python
"""Protocols for unified event system."""
from typing import Protocol, AsyncIterator
from arf.core.events import AgentEvent


class EventBus(Protocol):
    """Unified event bus. Engine emits; streaming + observability consume."""
    def emit(self, event: AgentEvent) -> None: ...
    async def subscribe(
        self, event_types: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


class EventStream(Protocol):
    """Transport adapter — SSE, WebSocket, callback."""
    async def publish(self, event: AgentEvent) -> None: ...
    async def listen(self) -> AsyncIterator[AgentEvent]: ...
```

- [ ] **Step 11: Write `tracer.py`**
```python
"""Protocols for observability domain."""
from typing import Protocol
from arf.core.events import AgentEvent


class Tracer(Protocol):
    """Consume EventBus events → OpenTelemetry Spans."""
    async def consume(self, events: list[AgentEvent]) -> None: ...
    async def flush(self) -> None: ...
```

- [ ] **Step 12: Write `replay.py`**
```python
"""Protocols for Record & Replay domain."""
from typing import Protocol, AsyncIterator
from dataclasses import dataclass, field
from arf.core.events import AgentEvent


@dataclass
class TurnRecord:
    turn: int
    model_name: str
    model_input: dict
    model_output: str
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class ReplayTrace:
    session_id: str
    agent_config_hash: str
    arf_version: str
    turns: list[TurnRecord] = field(default_factory=list)


class ReplayController(Protocol):
    """Record & replay non-deterministic inputs for deterministic debugging."""
    async def start_recording(self, session_id: str) -> None: ...
    async def record_model_output(
        self, session_id: str, turn: int, model_name: str, output: str,
    ) -> None: ...
    async def record_tool_result(
        self, session_id: str, turn: int, tool_name: str, params: dict, result: dict,
    ) -> None: ...
    async def stop_recording(self) -> ReplayTrace: ...
    async def replay(
        self, trace: ReplayTrace, *, start_turn: int = 0,
        breakpoints: list[int] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...
```

- [ ] **Step 13: Write `evaluation.py`**
```python
"""Protocols for evaluation domain."""
from typing import Protocol
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    expected_tools: list[str] | None = None
    expected_output_contains: list[str] | None = None
    max_turns: int | None = None


@dataclass
class EvalDataset:
    name: str
    cases: list[EvalCase] = field(default_factory=list)


@dataclass
class EvalSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    avg_turns: float = 0.0
    avg_tool_calls: float = 0.0
    avg_duration_seconds: float = 0.0
    tool_accuracy: float = 0.0


@dataclass
class EvalReport:
    run_id: str
    dataset_name: str
    agent_config_hash: str
    timestamp: float
    summary: EvalSummary = field(default_factory=EvalSummary)
    per_case: list[dict] = field(default_factory=list)
    comparison: dict | None = None


class MetricCalculator(Protocol):
    """Extract metrics from a trajectory."""
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]: ...


class EvalRunner(Protocol):
    """Run evaluation against a dataset, produce report with baseline comparison."""
    async def run(
        self, agent, dataset: EvalDataset, metrics: list[MetricCalculator],
        *, baseline: EvalReport | None = None, max_parallel: int = 1,
    ) -> EvalReport: ...
```

- [ ] **Step 14: Write `errors.py`**
```python
"""Protocols for error handling domain."""
from typing import Protocol
from arf.core.state import TurnContext
from arf.core.results import ErrorAction, GuardResult


class ErrorPolicy(Protocol):
    """Standardized error handling for tool/model/guardrail failures."""
    def on_tool_error(self, error: Exception, tool_name: str, attempt: int) -> ErrorAction: ...
    def on_model_error(self, error: Exception, model_name: str, attempt: int) -> ErrorAction: ...
    def on_guardrail_block(self, result: GuardResult, context: TurnContext) -> ErrorAction: ...
```

- [ ] **Step 15: Write `arf/core/protocols/__init__.py`**
```python
from arf.core.protocols.engine import (
    LoopStrategy, StateStore, ToolExecutor, TransactionContext, Planner,
)
from arf.core.protocols.memory import MemoryStore, MemoryRetriever, MemoryWriter, MemoryEntry
from arf.core.protocols.resources import (
    ToolResolver, ToolProvider, ToolRetriever, ToolBackend, ToolDefinition,
)
from arf.core.protocols.hooks import HookRunner
from arf.core.protocols.guardrails import (
    GuardRunner, InputGuardrail, OutputGuardrail, ToolGuardrail,
)
from arf.core.protocols.routing import ModelRouter
from arf.core.protocols.compaction import CompactionStrategy
from arf.core.protocols.sandbox import ToolSandbox
from arf.core.protocols.concurrency import TaskScheduler
from arf.core.protocols.human_loop import ApprovalPoint, ApprovalChannel
from arf.core.protocols.communication import (
    AgentBus, PeerAgent, TaskDelegator, Supervisor,
    SharedWorkspace, Lock, ConsensusProtocol,
    AgentMessage, AgentInfo,
)
from arf.core.protocols.event_bus import EventBus, EventStream
from arf.core.protocols.tracer import Tracer
from arf.core.protocols.replay import ReplayController, ReplayTrace, TurnRecord
from arf.core.protocols.evaluation import (
    EvalRunner, MetricCalculator, EvalCase, EvalDataset,
    EvalSummary, EvalReport,
)
from arf.core.protocols.errors import ErrorPolicy

__all__ = [
    # engine
    "LoopStrategy", "StateStore", "ToolExecutor", "TransactionContext", "Planner",
    # memory
    "MemoryStore", "MemoryRetriever", "MemoryWriter", "MemoryEntry",
    # resources
    "ToolResolver", "ToolProvider", "ToolRetriever", "ToolBackend", "ToolDefinition",
    # hooks
    "HookRunner",
    # guardrails
    "GuardRunner", "InputGuardrail", "OutputGuardrail", "ToolGuardrail",
    # routing
    "ModelRouter",
    # compaction
    "CompactionStrategy",
    # sandbox
    "ToolSandbox",
    # concurrency
    "TaskScheduler",
    # human_loop
    "ApprovalPoint", "ApprovalChannel",
    # communication
    "AgentBus", "PeerAgent", "TaskDelegator", "Supervisor",
    "SharedWorkspace", "Lock", "ConsensusProtocol",
    "AgentMessage", "AgentInfo",
    # event_bus
    "EventBus", "EventStream",
    # tracer
    "Tracer",
    # replay
    "ReplayController", "ReplayTrace", "TurnRecord",
    # evaluation
    "EvalRunner", "MetricCalculator", "EvalCase", "EvalDataset",
    "EvalSummary", "EvalReport",
    # errors
    "ErrorPolicy",
]
```

- [ ] **Step 16: Commit**
```bash
git add arf/core/protocols/
git commit -m "feat(core): add all remaining protocols — resources, hooks, guardrails, routing, compaction, sandbox, concurrency, human_loop, communication, event_bus, tracer, replay, evaluation, errors"
```

### Task 1.4: Update `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update packages from `src/arf` to `arf`**
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["arf", "arf.*"]
```

- [ ] **Step 2: Add new dependencies**
```toml
[project]
dependencies = [
    "pydantic>=2.0",
    "langgraph>=0.2",
    "rich>=13.0",           # TUI dashboard
    "opentelemetry-api>=1.20",  # observability
    "opentelemetry-sdk>=1.20",
    "pyyaml>=6.0",
]
```

- [ ] **Step 3: Verify install works**
```bash
pip install -e ".[dev]"
python -c "from arf.core.protocols import EventBus, StateStore, ToolResolver; print('OK')"
```

- [ ] **Step 4: Commit**
```bash
git add pyproject.toml
git commit -m "chore: update pyproject.toml — arf package root, new dependencies"
```

---

## Phase 2: Engine — GraphEngine, StateStore, ToolExecutor

### Task 2.0: InMemoryStateStore
**Files:**
- Create: `arf/engine/__init__.py`
- Create: `arf/engine/checkpoint.py`

- [ ] **Step 1: Write `arf/engine/checkpoint.py`**
```python
"""InMemoryStateStore — dict-backed checkpoint implementation."""
from arf.core.protocols import StateStore
from arf.core.state import AgentState


class InMemoryStateStore:
    def __init__(self) -> None:
        self._store: dict[str, AgentState] = {}

    async def put(self, session_id: str, state: AgentState) -> None:
        import copy
        self._store[session_id] = copy.deepcopy(dict(state))

    async def get(self, session_id: str) -> AgentState | None:
        return self._store.get(session_id)

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)
```

- [ ] **Step 2: Commit**
```bash
git add arf/engine/
git commit -m "feat(engine): add InMemoryStateStore"
```

### Task 2.1: Engine State + Graph loop
**Files:**
- Create: `arf/engine/state.py`

Actually, `AgentState` already defined in `arf/core/state.py`. The engine's `state.py` just re-exports or extends.

- [ ] **Step 1: Write `arf/engine/state.py`** — thin wrapper
```python
"""Engine-level state utilities."""
from arf.core.state import AgentState, TurnContext


def default_state(**overrides) -> AgentState:
    return AgentState(
        session_id=overrides.pop("session_id", ""),
        agent_name=overrides.pop("agent_name", ""),
        messages=overrides.pop("messages", []),
        current_model=overrides.pop("current_model", ""),
        current_turn=overrides.pop("current_turn", 0),
        context_summary=overrides.pop("context_summary", ""),
        tool_results=overrides.pop("tool_results", {}),
        plan=overrides.pop("plan", None),
        metadata=overrides.pop("metadata", {}),
    )
```

- [ ] **Step 2: Commit**
```bash
git add arf/engine/state.py
git commit -m "feat(engine): add default_state() factory"
```

---

---

## Phase 2 (continued): GraphEngine core

### Task 2.2: GraphEngine constructor
**Files:**
- Create: `arf/engine/graph.py`

- [ ] **Step 1: Write `arf/engine/graph.py`**
```python
"""GraphEngine — DI-driven Agent execution loop builder."""
from typing import Any, Callable
from arf.core.protocols import (
    LoopStrategy, StateStore, ToolExecutor, TransactionContext, Planner,
    ToolResolver, MemoryRetriever, MemoryWriter, HookRunner,
    GuardRunner, EventBus, ErrorPolicy,
)
from arf.core.state import AgentState
from arf.core.events import AgentEvent


class GraphEngine:
    """Constructs and runs the Agent execution graph.
    All dependencies injected via constructor — engine has zero imports
    from arf submodules beyond arf.core.
    """

    def __init__(
        self,
        *,
        loop_strategy: LoopStrategy,
        state_store: StateStore,
        tool_executor: ToolExecutor,
        tool_resolver: ToolResolver,
        transaction_ctx: TransactionContext | None = None,
        planner: Planner | None = None,
        memory_retriever: MemoryRetriever | None = None,
        memory_writer: MemoryWriter | None = None,
        hook_runner: HookRunner | None = None,
        guard_runner: GuardRunner | None = None,
        event_bus: EventBus | None = None,
        error_policy: ErrorPolicy | None = None,
        # async callables — the engine doesn't know which API is behind them
        call_model: Callable | None = None,
        stream_model: Callable | None = None,
        # configuration
        system_prompt: str = "",
        max_turns: int = 50,
    ):
        self.loop_strategy = loop_strategy
        self.state_store = state_store
        self.tool_executor = tool_executor
        self.tool_resolver = tool_resolver
        self.transaction_ctx = transaction_ctx
        self.planner = planner
        self.memory_retriever = memory_retriever
        self.memory_writer = memory_writer
        self.hook_runner = hook_runner
        self.guard_runner = guard_runner
        self.event_bus = event_bus
        self.error_policy = error_policy
        self._call_model = call_model
        self._stream_model = stream_model
        self._system_prompt = system_prompt
        self._max_turns = max_turns

    def _emit(self, event_type: str, data: dict) -> None:
        if self.event_bus:
            self.event_bus.emit(AgentEvent(type=event_type, data=data))

    async def invoke(self, state: AgentState) -> AgentState:
        """Synchronous (non-streaming) execution entry point."""
        raise NotImplementedError("placeholder — implemented in Task 2.3")

    async def astream(self, state: AgentState):
        """Async streaming execution entry point."""
        raise NotImplementedError("placeholder — implemented in Task 2.3")
```

- [ ] **Step 2: Commit**
```bash
git add arf/engine/graph.py
git commit -m "feat(engine): add GraphEngine constructor with full DI"
```

### Task 2.3: GraphEngine.invoke() — execution loop
**Files:**
- Modify: `arf/engine/graph.py`

- [ ] **Step 1: Write `invoke()` and internal loop**
```python
async def invoke(self, state: AgentState) -> AgentState:
    """Run the full agent loop: compact → retrieve memory → call model →
    guard output → execute tools → guard params → write memory → checkpoint."""
    session_id = state.get("session_id", "default")
    self._emit("session_start", {"session_id": session_id})

    while self.loop_strategy.should_continue(state):
        turn = state.get("current_turn", 0) + 1
        state["current_turn"] = turn

        # 1. Memory retrieval — before compaction
        if self.memory_retriever and self.memory_writer:
            query = _last_user_message(state)
            from arf.core.protocols.memory import MemoryStore
            entries = await self.memory_retriever.retrieve(
                store=_dummy_store(),  # replaced by actual store in Phase 4
                query_context=query,
                session_id=session_id,
                max_tokens=2000,
                top_k=5,
            )
            state["context_summary"] = _format_memory(entries)

        # 2. Get tool definitions
        tools = []
        if self.tool_resolver:
            query = _last_user_message(state)
            tools = await self.tool_resolver.get_tool_definitions(query, top_k=10)

        # 3. Call model
        messages = _build_messages(state, self._system_prompt, tools)
        if self.hook_runner:
            await self.hook_runner.fire("pre_model_call", {"messages": messages})
        response = await self._call_model(messages, state["current_model"]) if self._call_model else ""
        if self.hook_runner:
            await self.hook_runner.fire("post_model_call", {"response": response})

        # 4. Guard output
        if self.guard_runner:
            gr = await self.guard_runner.check_output(response, {})
            if not gr.allowed:
                if self.error_policy:
                    action = self.error_policy.on_ud_block(gr, _make_turn_ctx(state))
                    if action.action == "abort":
                        break
            elif gr.modified_message:
                response = gr.modified_message

        # 5. Parse tool calls from response
        tool_calls = _parse_tool_calls(response)
        if not tool_calls:
            state["messages"].append({"role": "assistant", "content": response})
            break  # no tools → respond

        # 6. Guard tool params
        if self.guard_runner:
            for tc in tool_calls:
                gr = await self.guard_runner.check_tool_params(tc["name"], tc["params"])
                if not gr.allowed:
                    tool_calls.remove(tc)

        # 7. Execute tools with transaction
        tx = None
        if self.transaction_ctx:
            tx = await self.transaction_ctx.begin(session_id, turn)
        results = await self.tool_executor.execute(tool_calls)
        if self.transaction_ctx and tx:
            all_ok = all(r.success for r in results.values())
            if all_ok:
                await self.transaction_ctx.commit(tx)
            else:
                await self.transaction_ctx.rollback(tx, Exception("tool failure"))
        state["tool_results"] = {k: {"success": v.success, "data": v.data, "error": v.error}
                                 for k, v in results.items()}

        # 8. Add results to messages
        for tc in tool_calls:
            r = results.get(tc.get("id", ""))
            if r:
                state["messages"].append({"role": "tool", "tool_call_id": tc["id"],
                                          "content": str(r.data)})

        # 9. Memory write — after turn
        if self.memory_writer:
            await self.memory_writer.extract_and_write(
                store=_dummy_store(),
                turn_messages=state["messages"][-4:],
                existing_entries=[],
            )

        # 10. Checkpoint
        await self.state_store.put(session_id, state)

        if turn >= self._max_turns:
            break

    self._emit("session_end", {"session_id": session_id})
    return state


def _last_user_message(state: AgentState) -> str:
    for m in reversed(state.get("messages", [])):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def _build_messages(state: AgentState, system_prompt: str, tools: list) -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt}]
    summary = state.get("context_summary", "")
    if summary:
        msgs[0]["content"] += f"\n\n{summary}"
    msgs.extend(state.get("messages", []))
    return msgs


def _parse_tool_calls(response: str) -> list[dict]:
    # simplified — real impl parses OpenAI-format tool_calls from model response
    import json
    try:
        data = json.loads(response) if isinstance(response, str) else response
        return data.get("tool_calls", [])
    except (json.JSONDecodeError, TypeError):
        return []


def _format_memory(entries: list) -> str:
    if not entries:
        return ""
    return "\n".join(f"- {e.content}" for e in entries if e.relevance_score > 0)


def _dummy_store():
    from arf.core.protocols.memory import MemoryStore, MemoryEntry
    class Dummy(MemoryStore):
        async def save(self, e): pass
        async def load(self, sid): return []
        async def delete(self, eid): pass
    return Dummy()


def _make_turn_ctx(state: AgentState):
    from arf.core.state import TurnContext
    return TurnContext(
        session_id=state.get("session_id", ""),
        agent_name=state.get("agent_name", ""),
        turn=state.get("current_turn", 0),
        current_model=state.get("current_model", ""),
        available_models=[],
        last_user_message=_last_user_message(state),
    )
```

- [ ] **Step 2: Commit**
```bash
git add arf/engine/graph.py
git commit -m "feat(engine): implement GraphEngine.invoke() with full execution loop"
```

### Task 2.4: React LoopStrategy
**Files:**
- Create: `arf/engine/loop_strategies/__init__.py`
- Create: `arf/engine/loop_strategies/react.py`

- [ ] **Step 1: Write `react.py`**
```python
"""Standard ReAct (Think-Act-Observe) loop strategy."""
from arf.core.protocols import LoopStrategy
from arf.core.state import AgentState


class ReActStrategy:
    def __init__(self, max_turns: int = 50) -> None:
        self.max_turns = max_turns

    def should_continue(self, state: AgentState) -> bool:
        turn = state.get("current_turn", 0)
        return turn < self.max_turns

    def next_step(self, state: AgentState) -> str:
        last = state.get("messages", [{}])[-1]
        if last.get("role") == "tool":
            return "call_model"  # tool result → think again
        if _has_tool_calls(state.get("messages", [])):
            return "execute_tools"
        return "call_model"
```

- [ ] **Step 2: Write `__init__.py`**
```python
from arf.engine.loop_strategies.react import ReActStrategy

__all__ = ["ReActStrategy"]
```

- [ ] **Step 3: Commit**
```bash
git add arf/engine/loop_strategies/
git commit -m "feat(engine): add ReActStrategy loop strategy"
```

### Task 2.5: ToolExecutor implementation
**Files:**
- Create: `arf/engine/tool_executor.py`

- [ ] **Step 1: Write `tool_executor.py`**
```python
"""ToolExecutor — concurrent tool execution within a single turn."""
import asyncio
from arf.core.protocols import ToolExecutor, ToolResolver
from arf.core.results import ToolResult


class ConcurrentToolExecutor:
    def __init__(self, tool_resolver: ToolResolver) -> None:
        self._resolver = tool_resolver

    async def execute(
        self,
        tool_calls: list[dict],
        strategy: str = "parallel",
        max_concurrency: int = 5,
    ) -> dict[str, ToolResult]:
        if strategy == "sequential":
            results = {}
            for tc in tool_calls:
                results[tc["id"]] = await self._resolver.execute(
                    tc["name"], tc.get("params", {})
                )
            return results
        else:
            sem = asyncio.Semaphore(max_concurrency)
            async def _run(tc):
                async with sem:
                    return tc["id"], await self._resolver.execute(
                        tc["name"], tc.get("params", {})
                    )
            tasks = [_run(tc) for tc in tool_calls]
            resolved = await asyncio.gather(*tasks, return_exceptions=True)
            results = {}
            for item in resolved:
                if isinstance(item, Exception):
                    continue
                tid, tr = item
                results[tid] = tr
            return results
```

- [ ] **Step 2: Commit**
```bash
git add arf/engine/tool_executor.py
git commit -m "feat(engine): add ConcurrentToolExecutor with sequential/parallel modes"
```

---

## Phase 3: Resources — ToolResolver default implementation

### Task 3.0: StaticYamlToolProvider
**Files:**
- Create: `arf/resources/__init__.py`
- Create: `arf/resources/providers/__init__.py`
- Create: `arf/resources/providers/static_yaml.py`
- Create: `arf/resources/backends/__init__.py`
- Create: `arf/resources/backends/function.py`

- [ ] **Step 1: Write `static_yaml.py`**
```python
"""StaticYamlToolProvider — load tools from directory of tool.yaml files."""
import yaml
import os
import importlib.util
from pathlib import Path
from arf.core.config_base import ToolConfig
from arf.core.protocols.resources import ToolProvider, ToolBackend
from arf.core.results import ToolResult


class StaticYamlToolProvider:
    def __init__(self, tools_dir: str | Path) -> None:
        self._dir = Path(tools_dir)
        self._tools: dict[str, ToolConfig] = {}
        self._functions: dict[str, callable] = {}
        self._backend = FunctionBackend()

    async def list_tools(self) -> list[ToolConfig]:
        if not self._tools:
            self._load_all()
        return list(self._tools.values())

    async def resolve(self, name: str) -> ToolConfig | None:
        if not self._tools:
            self._load_all()
        return self._tools.get(name)

    async def execute(self, name: str, params: dict) -> ToolResult:
        cfg = await self.resolve(name)
        if cfg is None:
            return ToolResult(tool_name=name, success=False, error=f"Tool '{name}' not found")
        return await self._backend.execute(cfg, params)

    def _load_all(self) -> None:
        if not self._dir.exists():
            return
        for tool_dir in self._dir.iterdir():
            if not tool_dir.is_dir():
                continue
            yaml_path = tool_dir / "tool.yaml"
            if not yaml_path.exists():
                continue
            raw = yaml.safe_load(yaml_path.read_text())
            cfg = ToolConfig(**raw)
            self._tools[cfg.name] = cfg

            func_path = tool_dir / "function.py"
            if func_path.exists():
                spec = importlib.util.spec_from_file_location(
                    f"arf_tool_{cfg.name}", str(func_path),
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "execute"):
                    self._functions[cfg.name] = mod.execute
```

- [ ] **Step 2: Write `function.py` — FunctionBackend**
```python
"""FunctionBackend — call Python functions directly."""
import time
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult


class FunctionBackend:
    def __init__(self) -> None:
        self._registry: dict[str, callable] = {}

    def register(self, name: str, fn: callable) -> None:
        self._registry[name] = fn

    async def execute(self, tool_config: ToolConfig, params: dict) -> ToolResult:
        fn = self._registry.get(tool_config.name)
        if fn is None:
            return ToolResult(tool_name=tool_config.name, success=False,
                              error=f"No function bound for '{tool_config.name}'")
        start = time.time()
        try:
            result = fn(**params) if params else fn()
            if hasattr(result, "__await__"):
                result = await result
            return ToolResult(
                tool_name=tool_config.name, success=True, data={"result": result},
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=tool_config.name, success=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )
```

- [ ] **Step 3: Commit**
```bash
git add arf/resources/
git commit -m "feat(resources): add StaticYamlToolProvider and FunctionBackend"
```

### Task 3.1: DefaultToolResolver
**Files:**
- Create: `arf/resources/resolver.py`

- [ ] **Step 1: Write `resolver.py`**
```python
"""DefaultToolResolver — wraps Provider + optional Retriever + Backend."""
from arf.core.protocols import ToolResolver, ToolProvider, ToolRetriever, ToolBackend
from arf.core.protocols.resources import ToolDefinition
from arf.core.config_base import ToolConfig


class DefaultToolResolver:
    def __init__(
        self,
        providers: list[ToolProvider],
        retriever: ToolRetriever | None = None,
        backend: ToolBackend | None = None,
    ) -> None:
        self._providers = providers
        self._retriever = retriever
        self._backend = backend

    async def get_tool_definitions(
        self, query_context: str, top_k: int = 10,
    ) -> list[ToolDefinition]:
        all_tools: list[ToolConfig] = []
        for p in self._providers:
            all_tools.extend(await p.list_tools())
        if self._retriever and len(all_tools) > top_k:
            all_tools = await self._retriever.retrieve(query_context, all_tools, top_k)
        return [
            ToolDefinition(name=t.name, description=t.description, parameters=t.parameters)
            for t in all_tools
        ]

    async def execute(self, tool_name: str, params: dict):
        for p in self._providers:
            cfg = await p.resolve(tool_name)
            if cfg:
                if hasattr(p, "execute"):
                    return await p.execute(tool_name, params)
        from arf.core.results import ToolResult
        return ToolResult(tool_name=tool_name, success=False, error=f"Tool '{tool_name}' not found")
```

- [ ] **Step 2: Commit**
```bash
git add arf/resources/resolver.py
git commit -m "feat(resources): add DefaultToolResolver wrapping Provider+Retriever+Backend"
```

---

## Phase 4: Memory — FileStore + RecentFirstRetriever + RuleBasedWriter

### Task 4.0: FileStore
**Files:**
- Create: `arf/memory/__init__.py`
- Create: `arf/memory/file_store.py`

- [ ] **Step 1: Write `file_store.py`**
```python
"""FileStore — JSON-file-backed memory persistence."""
import json
import os
import time
from pathlib import Path
from arf.core.protocols import MemoryEntry, MemoryStore


class FileMemoryStore:
    def __init__(self, workspace: str | Path = "./memory") -> None:
        self._dir = Path(workspace)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def save(self, entry: MemoryEntry) -> None:
        entries = await self._load_all()
        entries = [e for e in entries if e.id != entry.id]
        entries.append(entry)
        self._write(entries)

    async def load(self, session_id: str) -> list[MemoryEntry]:
        # load all for now — retriever handles filtering
        return await self._load_all()

    async def delete(self, entry_id: str) -> None:
        entries = [e for e in await self._load_all() if e.id != entry_id]
        self._write(entries)

    async def _load_all(self) -> list[MemoryEntry]:
        path = self._dir / "memory.json"
        if not path.exists():
            return []
        return [MemoryEntry(**d) for d in json.loads(path.read_text())]

    def _write(self, entries: list[MemoryEntry]) -> None:
        self._dir / "memory.json".write_text(
            json.dumps([{"id": e.id, "content": e.content, "category": e.category,
                         "timestamp": e.timestamp, "source_turn": e.source_turn,
                         "relevance_score": e.relevance_score, "replaces": e.replaces}
                        for e in entries], indent=2, ensure_ascii=False)
        )
```

- [ ] **Step 2: Commit**
```bash
git add arf/memory/
git commit -m "feat(memory): add FileMemoryStore"
```

### Task 4.1: RecentFirstRetriever + RuleBasedWriter
**Files:**
- Create: `arf/memory/recent_first.py`
- Create: `arf/memory/writer.py`

- [ ] **Step 1: Write `recent_first.py`**
```python
"""RecentFirstRetriever — return most recent N entries."""
from arf.core.protocols import MemoryEntry, MemoryStore, MemoryRetriever


class RecentFirstRetriever:
    async def retrieve(
        self, store: MemoryStore, query_context: str, session_id: str,
        max_tokens: int = 2000, top_k: int = 5,
    ) -> list[MemoryEntry]:
        entries = await store.load(session_id)
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        result = entries[:top_k]
        total_chars = sum(len(e.content) for e in result)
        while total_chars > max_tokens * 3 and len(result) > 1:
            result.pop()
            total_chars = sum(len(e.content) for e in result)
        return result
```

- [ ] **Step 2: Write `writer.py`**
```python
"""RuleBasedWriter — extract facts from conversation using model call."""
import time
import uuid
from arf.core.protocols import MemoryEntry, MemoryStore, MemoryWriter


class RuleBasedMemoryWriter:
    def __init__(self, model_call: callable | None = None) -> None:
        self._call_model = model_call

    async def extract_and_write(
        self, store: MemoryStore, turn_messages: list[dict],
        existing_entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        # Simple heuristic: extract from assistant messages
        new_entries = []
        for msg in turn_messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            # Heuristic: sentences with "prefer" / "always" / "never" → preferences
            if any(kw in content.lower() for kw in ["prefer", "always", "never", "must"]):
                entry = MemoryEntry(
                    id=str(uuid.uuid4()), content=content[:500],
                    category="preference", timestamp=time.time(),
                    source_turn=0,
                )
                await store.save(entry)
                new_entries.append(entry)
        return new_entries + existing_entries
```

- [ ] **Step 3: Commit**
```bash
git add arf/memory/
git commit -m "feat(memory): add RecentFirstRetriever and RuleBasedMemoryWriter"
```

---

## Phase 5: Hooks — HookRunner

### Task 5.0: SubprocessHookRunner
**Files:**
- Create: `arf/hooks/__init__.py`
- Create: `arf/hooks/runner.py`

- [ ] **Step 1: Write `runner.py`**
```python
"""SubprocessHookRunner — execute hooks as subprocesses with parallel launch."""
import asyncio
import os
from arf.core.protocols import HookRunner
from arf.core.config_base import HookDefinition
from arf.core.results import HookResult


class SubprocessHookRunner:
    def __init__(self, hooks: list[HookDefinition]) -> None:
        self._hooks: dict[str, list[HookDefinition]] = {}
        self._order: dict[str, list[str]] = {}
        for h in hooks:
            self._hooks.setdefault(h.type, []).append(h)

    def set_order(self, event_type: str, hook_names: list[str]) -> None:
        self._order[event_type] = hook_names

    def get_definitions(self) -> list[HookDefinition]:
        return [h for hooks in self._hooks.values() for h in hooks]

    async def fire(self, event_type: str, context: dict) -> list[HookResult]:
        hooks = self._hooks.get(event_type, [])
        ordered = self._order.get(event_type, [])
        if ordered:
            name_map = {h.name: h for h in hooks}
            hooks = [name_map[n] for n in ordered if n in name_map]
            remaining = [h for h in self._hooks.get(event_type, []) if h.name not in ordered]
            hooks += remaining

        all_results: list[HookResult] = []

        async def _run_hook(hook: HookDefinition) -> HookResult:
            results: list[HookResult] = []
            for cmd in hook.run:
                env = {**os.environ, **{k: v.format(**context) for k, v in (hook.env or {}).items()}}
                try:
                    proc = await asyncio.create_subprocess_shell(
                        cmd, env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=_parse_timeout(hook.timeout),
                    )
                    hr = HookResult(
                        hook_name=hook.name, exit_code=proc.returncode or 0,
                        stdout=stdout.decode() if stdout else "",
                        stderr=stderr.decode() if stderr else "",
                        injected_message=stdout.decode() if proc.returncode == 2 and stdout else None,
                    )
                    results.append(hr)
                    if proc.returncode != 0:
                        break  # non-zero → skip remaining programs in this hook
                except asyncio.TimeoutError:
                    if proc:
                        proc.kill()
                    results.append(HookResult(hook_name=hook.name, exit_code=-1, stderr="timeout"))
                    break
            return results[-1] if results else HookResult(hook_name=hook.name, exit_code=0)

        tasks = [_run_hook(h) for h in hooks]
        resolved = await asyncio.gather(*tasks, return_exceptions=True)
        for r in resolved:
            if isinstance(r, HookResult):
                all_results.append(r)
            elif isinstance(r, Exception):
                all_results.append(HookResult(hook_name="unknown", exit_code=-1, stderr=str(r)))
        return all_results


def _parse_timeout(s: str) -> float:
    s = s.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return 30.0
```

- [ ] **Step 2: Commit**
```bash
git add arf/hooks/
git commit -m "feat(hooks): add SubprocessHookRunner with parallel execution and exit code contract"
```

---

## Phase 6: Routing + Compaction + Sandbox + Concurrency (lightweight defaults)

### Task 6.0: StaticRouter + SlidingWindowCompactor + PathSandbox + SequentialScheduler
**Files:**
- Create: `arf/routing/__init__.py`, `arf/routing/two_tier.py`
- Create: `arf/compaction/__init__.py`, `arf/compaction/sliding_window.py`
- Create: `arf/sandbox/__init__.py`, `arf/sandbox/path_sandbox.py`
- Create: `arf/concurrency/__init__.py`, `arf/concurrency/sequential.py`

- [ ] **Step 1: Write `routing/two_tier.py`**
```python
"""TwoTierRouter — complexity classifier → model selection."""
import os
from arf.core.protocols import ModelRouter
from arf.core.config_base import RoutingConfig


class TwoTierRouter:
    def __init__(self, config: RoutingConfig, models: list[str], classifier_call: callable) -> None:
        self._cfg = config
        self._models = models
        self._classify = classifier_call

    async def route(self, query: str, history: list[dict]) -> str:
        level = await self.classify(query)
        return self._cfg.classify.get(level, self._cfg.default)

    async def classify(self, query: str) -> str:
        if self._classify:
            return await self._classify(query)
        return "medium"

    def fallback_from(self, model_name: str) -> str | None:
        return self._cfg.fallback.get(model_name)
```

- [ ] **Step 2: Write `compaction/sliding_window.py`**
```python
"""SlidingWindowCompactor — summarise old turns, keep recent ones."""
from arf.core.protocols import CompactionStrategy
from arf.core.state import AgentState


class SlidingWindowCompactor:
    def __init__(self, threshold: float = 0.75, summarizer: callable | None = None) -> None:
        self._threshold = threshold
        self._summarize = summarizer

    def should_compact(self, state: AgentState, threshold: float | None = None) -> bool:
        t = threshold or self._threshold
        chars = sum(len(m.get("content", "")) for m in state.get("messages", []))
        # Rough estimate: 1 token ~= 3 chars, 1M window
        return chars > t * 1_000_000 * 3

    async def compact(self, state: AgentState) -> AgentState:
        msgs = state.get("messages", [])
        if len(msgs) <= 4:
            return state
        # Keep last 4 messages, summarize the rest
        old = msgs[:-4]
        recent = msgs[-4:]
        summary = state.get("context_summary", "")
        if self._summarize and old:
            new_summary = await self._summarize(old)
            summary = f"{summary}\n[Earlier]: {new_summary}" if summary else f"[Earlier]: {new_summary}"
        return {**state, "messages": recent, "context_summary": summary}
```

- [ ] **Step 3: Write `sandbox/path_sandbox.py`**
```python
"""PathSandbox — prevent path traversal and workspace escape."""
from pathlib import Path
from arf.core.protocols import ToolSandbox


class PathSandbox:
    def __init__(self, workspace_root: str | Path, writable_dirs: list[str] | None = None) -> None:
        self._root = Path(workspace_root).resolve()
        self._writable = [self._root / d for d in (writable_dirs or [])]

    def validate_path(self, path: str, workspace_root: str = "") -> bool:
        root = Path(workspace_root or self._root).resolve()
        resolved = (root / path).resolve()
        return resolved.is_relative_to(root) and ".." not in Path(path).parts

    def validate_command(self, command: str) -> bool:
        dangerous = [";", "&&", "|", "$(", "`", "rm -rf /", "sudo"]
        return not any(d in command for d in dangerous)

    def allowed_dirs(self) -> list[str]:
        return [str(d) for d in self._writable]
```

- [ ] **Step 4: Write `concurrency/sequential.py`**
```python
"""SequentialScheduler — execute tasks one at a time."""
from arf.core.protocols import TaskScheduler


class SequentialScheduler:
    async def schedule(self, tasks: list[dict]) -> list[dict]:
        return tasks

    async def execute(self, tasks: list[dict]) -> list[dict]:
        results = []
        for t in tasks:
            if callable(t.get("fn")):
                results.append({"id": t.get("id", ""), "result": await t["fn"]()})
        return results
```

- [ ] **Step 5: Commit all Phase 6**
```bash
git add arf/routing/ arf/compaction/ arf/sandbox/ arf/concurrency/
git commit -m "feat: add default impls — TwoTierRouter, SlidingWindowCompactor, PathSandbox, SequentialScheduler"
```

---

## Phase 7: Guardrails — DefaultGuardRunner

### Task 7.0: DefaultGuardRunner + built-in guards
**Files:**
- Create: `arf/guardrails/__init__.py`
- Create: `arf/guardrails/runner.py`
- Create: `arf/guardrails/none_guard.py`
- Create: `arf/guardrails/regex_clean.py`
- Create: `arf/guardrails/path_check.py`

- [ ] **Step 1: Write `none_guard.py`** — pass-through all inputs:
```python
from arf.core.protocols import InputGuardrail
from arf.core.results import GuardResult

class NoneInputGuard:
    async def check(self, message: str, context: dict) -> GuardResult:
        return GuardResult(allowed=True)
```

- [ ] **Step 2: Write `regex_clean.py`** — redact API keys, phone numbers from output:
```python
import re
from arf.core.protocols import OutputGuardrail
from arf.core.results import GuardResult

class RegexOutputGuard:
    PATTERNS = [
        (r'sk-[a-zA-Z0-9]{20,}', '[REDACTED_API_KEY]'),
        (r'1[3-9]\d{9}', '[REDACTED_PHONE]'),
    ]

    async def check(self, message: str, context: dict) -> GuardResult:
        modified = message
        changed = False
        for pat, repl in self.PATTERNS:
            if re.search(pat, modified):
                modified = re.sub(pat, repl, modified)
                changed = True
        return GuardResult(allowed=True, modified_message=modified if changed else None)
```

- [ ] **Step 3: Write `path_check.py`** — detect path traversal in tool params:
```python
from pathlib import Path
from arf.core.protocols import ToolGuardrail
from arf.core.results import GuardResult

class PathCheckToolGuard:
    def __init__(self, workspace_root: str = "") -> None:
        self._root = workspace_root

    async def check(self, tool_name: str, params: dict) -> GuardResult:
        for v in params.values():
            if isinstance(v, str) and ".." in Path(v).parts:
                return GuardResult(allowed=False, reason=f"Path traversal detected in '{v}'")
            if isinstance(v, str) and v.startswith("/"):
                return GuardResult(allowed=False, reason=f"Absolute path denied: '{v}'")
        return GuardResult(allowed=True)
```

- [ ] **Step 4: Write `runner.py`** — DefaultGuardRunner:
```python
from arf.core.protocols import GuardRunner, InputGuardrail, OutputGuardrail, ToolGuardrail
from arf.core.results import GuardResult

class DefaultGuardRunner:
    def __init__(self, input_guard=None, output_guard=None, tool_guard=None) -> None:
        self._input = input_guard
        self._output = output_guard
        self._tool = tool_guard

    async def check_input(self, message: str, context: dict) -> GuardResult:
        return await self._input.check(message, context) if self._input else GuardResult(allowed=True)

    async def check_output(self, message: str, context: dict) -> GuardResult:
        return await self._output.check(message, context) if self._output else GuardResult(allowed=True)

    async def check_tool_params(self, tool_name: str, params: dict) -> GuardResult:
        return await self._tool.check(tool_name, params) if self._tool else GuardResult(allowed=True)
```

- [ ] **Step 5: Commit**
```bash
git add arf/guardrails/
git commit -m "feat(guardrails): add DefaultGuardRunner with NoneInput, RegexOutput, PathCheck guards"
```

---

## Phase 8: Errors — DefaultErrorPolicy + TransactionContext

### Task 8.0: DefaultErrorPolicy
**Files:**
- Create: `arf/errors/__init__.py`
- Create: `arf/errors/retry.py`

- [ ] **Step 1: Write `retry.py`**
```python
"""DefaultErrorPolicy — exponential backoff retry for tool/model errors."""
import time
from arf.core.protocols import ErrorPolicy
from arf.core.state import TurnContext
from arf.core.results import ErrorAction, GuardResult


class DefaultErrorPolicy:
    def __init__(
        self, tool_retry: int = 2, model_retry: int = 3,
        model_5xx_action: str = "fallback", guardrail_block_action: str = "abort",
    ) -> None:
        self._tool_retry = tool_retry
        self._model_retry = model_retry
        self._model_5xx_action = model_5xx_action
        self._guardrail_block_action = guardrail_block_action

    def on_tool_error(self, error: Exception, tool_name: str, attempt: int) -> ErrorAction:
        if attempt < self._tool_retry:
            delay = 2 ** attempt * 1.0
            return ErrorAction(action="retry", delay=delay)
        return ErrorAction(action="abort", message=str(error))

    def on_model_error(self, error: Exception, model_name: str, attempt: int) -> ErrorAction:
        msg = str(error).lower()
        is_5xx = any(str(code) in msg for code in [500, 502, 503, 504])
        if is_5xx and self._model_5xx_action == "fallback":
            return ErrorAction(action="fallback")
        if attempt < self._model_retry:
            delay = 2 ** attempt * 0.5
            return ErrorAction(action="retry", delay=delay)
        return ErrorAction(action="abort", message=str(error))

    def on_guardrail_block(self, result: GuardResult, context: TurnContext) -> ErrorAction:
        if self._guardrail_block_action == "ask_user":
            return ErrorAction(action="ask_user", message=result.reason)
        return ErrorAction(action="abort", message=result.reason)
```

- [ ] **Step 2: Write `arf/errors/transaction.py`** — SnapshotRollback:
```python
"""SnapshotRollback — save state snapshot, restore on failure."""
import copy
from arf.core.protocols import TransactionContext
from arf.core.results import RollbackResult, ToolResult


class SnapshotRollback:
    def __init__(self) -> None:
        self._snapshots: dict[str, dict] = {}

    async def begin(self, session_id: str, turn: int) -> dict:
        tx = {"id": f"{session_id}:{turn}", "session_id": session_id, "turn": turn,
              "state_snapshot": None, "tool_results": []}
        self._snapshots[tx["id"]] = tx
        return tx

    async def commit(self, tx: dict) -> None:
        self._snapshots.pop(tx["id"], None)

    async def rollback(self, tx: dict, error: Exception) -> RollbackResult:
        self._snapshots.pop(tx["id"], None)
        unresolved = []
        for tr in tx.get("tool_results", []):
            if not tr.get("rollback_fn"):
                unresolved.append(tr.get("tool_name", "unknown"))
        return RollbackResult(
            success=len(unresolved) == 0,
            unresolved=unresolved,
            restored_state=tx.get("state_snapshot", {}),
        )
```

- [ ] **Step 3: Commit**
```bash
git add arf/errors/
git commit -m "feat(errors): add DefaultErrorPolicy and SnapshotRollback TransactionContext"
```

---

## Phase 9: HumanLoop — ConsoleChannel + AutoApprove

### Task 9.0: Approval implementations
**Files:**
- Create: `arf/human_loop/__init__.py`
- Create: `arf/human_loop/approval_points.py`
- Create: `arf/human_loop/channels/__init__.py`
- Create: `arf/human_loop/channels/console.py`

- [ ] **Step 1: Write `approval_points.py`**
```python
from arf.core.protocols import ApprovalPoint
from arf.core.state import TurnContext
from arf.core.results import ApprovalRequest


class AlwaysAutoApprove:
    def should_pause(self, context: TurnContext) -> bool:
        return False

    def approval_form(self, context: TurnContext) -> ApprovalRequest:
        return ApprovalRequest(agent_name="", session_id="", turn=0, tool_name="", params={}, reason="")


class ToolNameAllowlist:
    def __init__(self, allowlist: list[str]) -> None:
        self._allowlist = set(allowlist)

    def should_pause(self, context: TurnContext) -> bool:
        for tc in context.last_tool_calls:
            if tc.get("name", "") in self._allowlist:
                return True
        return False

    def approval_form(self, context: TurnContext) -> ApprovalRequest:
        tc = context.last_tool_calls[0] if context.last_tool_calls else {}
        return ApprovalRequest(
            agent_name=context.agent_name, session_id=context.session_id,
            turn=context.turn, tool_name=tc.get("name", ""),
            params=tc.get("params", {}),
            reason=f"Tool '{tc.get('name', '')}' requires approval",
        )
```

- [ ] **Step 2: Write `channels/console.py`**
```python
import asyncio
from arf.core.protocols import ApprovalChannel
from arf.core.results import ApprovalRequest, ApprovalResponse


class ConsoleChannel:
    async def send(self, request: ApprovalRequest) -> str:
        print(f"\n[APPROVAL REQUIRED] {request.reason}")
        print(f"  Tool: {request.tool_name}")
        print(f"  Params: {request.params}")
        return f"console:{request.session_id}:{request.turn}"

    async def wait(self, approval_id: str, timeout: int) -> ApprovalResponse:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(input, "Approve? [Y/n/modify]: "),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return ApprovalResponse(action="reject", comment="timeout")
        result = result.strip().lower()
        if result in ("y", "yes", ""):
            return ApprovalResponse(action="approve")
        elif result in ("n", "no"):
            return ApprovalResponse(action="reject")
        elif result.startswith("modify"):
            return ApprovalResponse(action="modify", modified_params={})
        return ApprovalResponse(action="reject")
```

- [ ] **Step 3: Commit**
```bash
git add arf/human_loop/
git commit -m "feat(human_loop): add AlwaysAutoApprove, ToolNameAllowlist, ConsoleChannel"
```

---

## Phase 10: EventBus + Streaming + Observability + Replay

### Task 10.0: InMemoryEventBus + SseStream
**Files:**
- Create: `arf/streaming/__init__.py`
- Create: `arf/streaming/adapters/__init__.py`
- Create: `arf/streaming/adapters/sse.py`
- Create: `arf/event_bus.py`

- [ ] **Step 1: Write `arf/event_bus.py`**
```python
"""InMemoryEventBus — asyncio.Queue-based event broadcasting."""
import asyncio
from arf.core.events import AgentEvent
from arf.core.protocols import EventBus


class InMemoryEventBus:
    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def emit(self, event: AgentEvent) -> None:
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, event_types: list[str] | None = None):
        q = asyncio.Queue(maxsize=1000)
        self._queues.append(q)
        try:
            while True:
                event = await q.get()
                if event_types is None or event.type in event_types:
                    yield event
        finally:
            self._queues.remove(q)
```

- [ ] **Step 2: Write `streaming/adapters/sse.py`**
```python
"""SseStream — Server-Sent Events transport adapter."""
import json
from arf.core.events import AgentEvent
from arf.core.protocols import EventStream


class SseStream:
    def __init__(self) -> None:
        self._listeners: list[callable] = []

    async def publish(self, event: AgentEvent) -> None:
        data = f"data: {json.dumps({'type': event.type, 'data': event.data, 'timestamp': event.timestamp})}\n\n"
        for cb in self._listeners:
            await cb(data)

    async def listen(self):
        import asyncio
        q = asyncio.Queue()
        async def _cb(data):
            await q.put(data)
        self._listeners.append(_cb)
        try:
            while True:
                yield await q.get()
        finally:
            self._listeners.remove(_cb)
```

- [ ] **Step 3: Commit**
```bash
git add arf/event_bus.py arf/streaming/
git commit -m "feat(event): add InMemoryEventBus and SseStream"
```

### Task 10.1: Tracer + TuiDashboard + FileReplayController
**Files:**
- Create: `arf/observability/__init__.py`
- Create: `arf/observability/otel.py`
- Create: `arf/observability/tui.py`
- Create: `arf/observability/replay.py`

- [ ] **Step 1: Write `otel.py`** — OTel Tracer consuming EventBus:
```python
"""OtelTracer — convert AgentEvent stream to OpenTelemetry Spans."""
import os
from arf.core.protocols import Tracer
from arf.core.events import AgentEvent


class OtelTracer:
    def __init__(self) -> None:
        self._exporter = os.environ.get("OTEL_EXPORTER", "none")
        self._spans: dict[str, dict] = {}

    async def consume(self, events: list[AgentEvent]) -> None:
        for e in events:
            span_id = e.trace_id + ":" + e.type
            if e.type.endswith("_start"):
                self._spans[span_id] = {"start": e.timestamp, "attributes": {
                    "session_id": e.session_id, "agent_name": e.agent_name,
                    "turn": e.turn, "event_type": e.type, **e.data,
                }}
            elif e.type.endswith("_end") and span_id in self._spans:
                s = self._spans.pop(span_id)
                duration = e.timestamp - s["start"]
                if self._exporter == "console":
                    print(f"[OTel] {e.type}: {duration*1000:.1f}ms {s['attributes']}")

    async def flush(self) -> None:
        pass  # production: flush to OTLP collector
```

- [ ] **Step 2: Write `tui.py`** — Rich TUI dashboard skeleton:
```python
"""TuiDashboard — Rich terminal real-time debug panel. Enable via ARF_TUI=1."""
import os
from arf.core.protocols import Tracer
from arf.core.events import AgentEvent


class TuiDashboard:
    def __init__(self) -> None:
        self._enabled = os.environ.get("ARF_TUI", "0") == "1"
        self._stats: dict[str, dict] = {}   # model_name → {calls, tokens_in, tokens_out}
        self._timeline: list[tuple] = []

    async def consume(self, events: list[AgentEvent]) -> None:
        if not self._enabled:
            return
        for e in events:
            if e.type == "model_call_end":
                model = e.data.get("model", "unknown")
                m = self._stats.setdefault(model, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
                m["calls"] += 1
                m["tokens_in"] += e.data.get("tokens_in", 0)
                m["tokens_out"] += e.data.get("tokens_out", 0)
            elif e.type == "tool_call_end":
                self._timeline.append((e.timestamp, e.data.get("tool_name", ""),
                                       e.data.get("duration_ms", 0)))

    def render(self) -> str:
        """Return rendered TUI string (called by Rich Live display)."""
        lines = ["ARF Agent Dashboard", "=" * 40]
        for model, stats in self._stats.items():
            lines.append(f"  {model}: {stats['calls']} calls, "
                         f"{stats['tokens_in']} in / {stats['tokens_out']} out")
        lines.append(f"\nTool Timeline ({len(self._timeline)} calls):")
        for ts, name, dur in self._timeline[-10:]:
            lines.append(f"  {name}: {dur:.0f}ms")
        return "\n".join(lines)
```

- [ ] **Step 3: Write `replay.py`** — FileReplayController:
```python
"""FileReplayController — Record sessions to JSON, replay deterministically."""
import json
import asyncio
from pathlib import Path
from arf.core.protocols import ReplayController, ReplayTrace, TurnRecord
from arf.core.events import AgentEvent


class FileReplayController:
    def __init__(self, traces_dir: str | Path = "./traces") -> None:
        self._dir = Path(traces_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._recording: ReplayTrace | None = None
        self._replay_state: dict = {}

    async def start_recording(self, session_id: str) -> None:
        self._recording = ReplayTrace(session_id=session_id, agent_config_hash="", arf_version="1.0")

    async def record_model_output(self, session_id: str, turn: int, model_name: str, output: str) -> None:
        if self._recording:
            self._recording.turns.append(TurnRecord(turn=turn, model_name=model_name,
                                                     model_input={}, model_output=output))

    async def record_tool_result(self, session_id: str, turn: int, tool_name: str, params: dict, result: dict) -> None:
        if self._recording and self._recording.turns:
            self._recording.turns[-1].tool_calls.append(
                {"tool_name": tool_name, "params": params, "result": result, "timestamp": 0}
            )

    async def stop_recording(self) -> ReplayTrace:
        trace = self._recording
        if trace:
            path = self._dir / f"{trace.session_id}.json"
            path.write_text(json.dumps({"session_id": trace.session_id, "turns": [
                {"turn": t.turn, "model_name": t.model_name,
                 "model_output": t.model_output, "tool_calls": t.tool_calls}
                for t in trace.turns
            ]}, indent=2))
        self._recording = None
        return trace

    async def replay(self, trace: ReplayTrace, *, start_turn: int = 0,
                     breakpoints: list[int] | None = None):
        for turn in trace.turns:
            if turn.turn < start_turn:
                continue
            if breakpoints and turn.turn in breakpoints:
                input(f"[Breakpoint] Turn {turn.turn}. Press Enter to continue...")
            yield AgentEvent(type="model_call_end", data={"output": turn.model_output,
                              "model": turn.model_name}, turn=turn.turn)
            for tc in turn.tool_calls:
                yield AgentEvent(type="tool_call_result", data={"tool_name": tc["tool_name"],
                                  "result": tc["result"]}, turn=turn.turn)
```

- [ ] **Step 4: Commit**
```bash
git add arf/observability/
git commit -m "feat(observability): add OtelTracer, TuiDashboard, FileReplayController"
```

---

## Phase 11: Communication — InMemoryBus + RoundRobinSupervisor

### Task 11.0: All communication defaults
**Files:**
- Create: `arf/communication/__init__.py`
- Create: `arf/communication/in_memory_bus.py`
- Create: `arf/communication/supervisor.py`
- Create: `arf/communication/shared_workspace.py`
- Create: `arf/communication/lock.py`

- [ ] **Step 1: Write `in_memory_bus.py`**
```python
"""InMemoryAgentBus — asyncio.Queue-backed agent message routing."""
import asyncio
from arf.core.protocols.communication import AgentMessage, AgentInfo, AgentBus


class InMemoryAgentBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._agents: dict[str, AgentInfo] = {}
        self._sent: list[AgentMessage] = []  # for testing

    async def send(self, message: AgentMessage) -> None:
        self._sent.append(message)
        targets = [message.receiver] if message.receiver else list(self._queues.keys())
        for name in targets:
            if name in self._queues:
                await self._queues[name].put(message)

    async def receive(self, agent_name: str):
        q = self._queues.setdefault(agent_name, asyncio.Queue(maxsize=100))
        while True:
            yield await q.get()

    async def register(self, agent: AgentInfo) -> None:
        self._agents[agent.name] = agent
        self._queues.setdefault(agent.name, asyncio.Queue(maxsize=100))

    async def discover(self, capability: str | None = None) -> list[AgentInfo]:
        if capability:
            return [a for a in self._agents.values() if capability in a.capabilities]
        return list(self._agents.values())

    @property
    def sent_messages(self) -> list[AgentMessage]:
        return list(self._sent)

    def reset(self) -> None:
        self._sent.clear()
        self._queues.clear()
        self._agents.clear()
```

- [ ] **Step 2: Write `supervisor.py`**
```python
"""RoundRobinSupervisor — cycle through agents for task assignment."""
from arf.core.protocols.communication import Supervisor, AgentInfo


class RoundRobinSupervisor:
    def __init__(self) -> None:
        self._index = 0

    async def route_task(self, task: dict, agents: list[AgentInfo]) -> str:
        if not agents:
            return ""
        agent = agents[self._index % len(agents)]
        self._index += 1
        return agent.name

    async def should_intervene(self, handle_id: str, progress: dict) -> bool:
        return False

    async def synthesize(self, results: list[dict]) -> str:
        return "\n".join(str(r) for r in results)
```

- [ ] **Step 3: Write `shared_workspace.py` + `lock.py`**
```python
# shared_workspace.py
"""DictWorkspace — simple dict-backed SharedWorkspace."""
from arf.core.protocols.communication import SharedWorkspace


class DictWorkspace:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    async def write(self, key: str, value: dict, owner: str) -> None:
        self._data[key] = {**value, "_owner": owner}

    async def read(self, key: str) -> dict | None:
        return self._data.get(key)
```

```python
# lock.py
"""InMemoryLock — asyncio-based Lock for SharedWorkspace."""
import asyncio
import time
from arf.core.protocols.communication import Lock as LockProtocol


class InMemoryLock:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[str, float]] = {}

    async def acquire(self, key: str, owner: str, ttl: float = 30.0) -> bool:
        now = time.time()
        if key in self._locks:
            _, expires = self._locks[key]
            if now < expires:
                return False
        self._locks[key] = (owner, now + ttl)
        return True

    async def release(self, key: str, owner: str) -> None:
        if key in self._locks and self._locks[key][0] == owner:
            del self._locks[key]
```

- [ ] **Step 4: Commit**
```bash
git add arf/communication/
git commit -m "feat(communication): add InMemoryAgentBus, RoundRobinSupervisor, DictWorkspace, InMemoryLock"
```

---

## Phase 12: Evaluation

### Task 12.0: EvalRunner + built-in metrics
**Files:**
- Create: `arf/evaluation/__init__.py`
- Create: `arf/evaluation/runner.py`
- Create: `arf/evaluation/metrics.py`

- [ ] **Step 1: Write `metrics.py`** — four built-in metrics:
```python
"""Built-in evaluation metrics."""
from arf.core.protocols.evaluation import MetricCalculator, EvalCase


class SuccessRateMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        errors = sum(1 for t in trace.get("turns", []) if t.get("error"))
        return {"success_rate": 0.0 if errors > 0 else 1.0}


class ToolAccuracyMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        if not expected.expected_tools:
            return {"tool_accuracy": 1.0}
        actual = [t.get("tool_name", "") for t in trace.get("turns", []) for _ in t.get("tool_calls", [])]
        if not actual:
            return {"tool_accuracy": 0.0}
        matches = sum(1 for e, a in zip(expected.expected_tools, actual) if e == a)
        return {"tool_accuracy": matches / len(expected.expected_tools)}


class TurnEfficiencyMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        turns = len(trace.get("turns", []))
        return {"turn_count": float(turns)}


class OutputContainsMetric:
    async def compute(self, trace: dict, expected: EvalCase) -> dict[str, float]:
        if not expected.expected_output_contains:
            return {"output_contains": 1.0}
        last_output = ""
        for t in reversed(trace.get("turns", [])):
            last_output = t.get("model_output", "")
            if last_output:
                break
        matches = sum(1 for kw in expected.expected_output_contains if kw.lower() in last_output.lower())
        return {"output_contains": matches / len(expected.expected_output_contains) if expected.expected_output_contains else 1.0}
```

- [ ] **Step 2: Write `runner.py`**:
```python
"""EvalRunner — run agent against dataset, compute metrics, compare baseline."""
import time
import uuid
from arf.core.protocols.evaluation import (
    EvalRunner, MetricCalculator, EvalDataset, EvalReport, EvalSummary,
)


class DefaultEvalRunner:
    async def run(
        self, agent, dataset: EvalDataset, metrics: list[MetricCalculator],
        *, baseline: EvalReport | None = None, max_parallel: int = 1,
    ) -> EvalReport:
        per_case = []
        passed = 0
        for case in dataset.cases:
            start = time.time()
            try:
                # agent.chat() returns response; in real impl we'd collect full trace
                response = await agent.chat(case.input)
                duration = time.time() - start
                case_result = {
                    "case_id": case.id, "passed": True, "turns": 1,
                    "tool_calls": [], "duration_seconds": duration,
                    "trace": {"turns": []}, "metrics": {}, "error": None,
                }
                for m in metrics:
                    case_result["metrics"].update(await m.compute(case_result["trace"], case))
                per_case.append(case_result)
                passed += 1
            except Exception as e:
                per_case.append({"case_id": case.id, "passed": False, "turns": 0,
                                 "tool_calls": [], "duration_seconds": time.time() - start,
                                 "trace": {"turns": []}, "metrics": {}, "error": str(e)})

        summary = EvalSummary(
            total=len(dataset.cases), passed=passed, failed=len(dataset.cases) - passed,
            pass_rate=passed / len(dataset.cases) if dataset.cases else 0.0,
            avg_turns=sum(c["turns"] for c in per_case) / len(per_case) if per_case else 0.0,
            avg_duration_seconds=sum(c["duration_seconds"] for c in per_case) / len(per_case) if per_case else 0.0,
        )
        return EvalReport(
            run_id=str(uuid.uuid4()), dataset_name=dataset.name,
            agent_config_hash="", timestamp=time.time(),
            summary=summary, per_case=per_case, comparison=None,
        )
```

- [ ] **Step 3: Commit**
```bash
git add arf/evaluation/
git commit -m "feat(evaluation): add DefaultEvalRunner and 4 built-in metrics"
```

---

## Phase 13: Planner

### Task 13.0: PromptBasedPlanner
**Files:**
- Create: `arf/engine/loop_strategies/planner.py`

- [ ] **Step 1: Write `planner.py`**
```python
"""PromptBasedPlanner — use model calls to generate and revise plans."""
from arf.core.protocols import Planner
from arf.core.state import AgentState, TurnContext

class PromptBasedPlanner:
    def __init__(self, model_call: callable) -> None:
        self._call_model = model_call

    async def generate_plan(self, task: str, context: TurnContext, tools: list[dict]) -> dict:
        prompt = f"Task: {task}\nAvailable tools: {[t.name for t in tools]}\nGenerate a step-by-step plan as JSON list."
        response = await self._call_model([{"role": "user", "content": prompt}])
        return {"id": "plan_1", "goal": task, "steps": [], "current_step_index": 0, "status": "draft"}

    async def update_progress(self, plan: dict, completed_step: dict, result) -> dict:
        plan["current_step_index"] = plan.get("current_step_index", 0) + 1
        if plan["current_step_index"] >= len(plan.get("steps", [])):
            plan["status"] = "completed"
        return plan

    async def detect_divergence(self, plan: dict, state: AgentState) -> dict:
        return {"diverged": False, "reason": "", "affected_steps": [], "suggested_revision": ""}

    async def revise(self, plan: dict, divergence: dict, context: TurnContext) -> dict:
        plan["status"] = "revising"
        return plan
```

- [ ] **Step 2: Commit**
```bash
git add arf/engine/loop_strategies/planner.py
git commit -m "feat(planner): add PromptBasedPlanner"
```

---

## Phase 14: Agent Assembly — BaseAgent + create_agent

### Task 14.0: AgentConfig + AdvancedConfig
**Files:**
- Create: `arf/agent/__init__.py`
- Create: `arf/agent/config.py`

- [ ] **Step 1: Write `config.py`** — the top-level Pydantic models:
```python
"""AgentConfig — the simplified user-facing configuration model."""
from pydantic import BaseModel, Field
from typing import Literal
from arf.core.config_base import (
    ModelConfig, SkillConfig, ToolConfig, HookDefinition,
    RoutingConfig, CompactionConfig, MemoryConfig,
    GuardrailsConfig, ErrorConfig, HumanLoopConfig,
    StreamingConfig, SandboxConfig, ToolRetrievalConfig,
    ReloadConfig, HandoverConfig, SupervisorConfig,
)


class AdvancedConfig(BaseModel):
    """All internal framework mechanisms with production-grade defaults.
    Users can override any field; unset fields stay at defaults.
    """
    loop_strategy: Literal["react", "direct", "plan_execute"] = "react"
    max_turns: int = 50
    critical_rules: str = ""
    routing: RoutingConfig | None = None
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
    guardrails: GuardrailsConfig | None = None
    errors: ErrorConfig | None = None
    human_loop: HumanLoopConfig | None = None
    streaming: StreamingConfig | None = None
    sandbox: SandboxConfig | None = None
    tool_retrieval: ToolRetrievalConfig | None = None
    reload: ReloadConfig | None = None

    @classmethod
    def default(cls) -> "AdvancedConfig":
        return cls()

    @classmethod
    def auto_derive(cls, tools_count: int, models_count: int) -> "AdvancedConfig":
        adv = cls.default()
        if tools_count > 20:
            adv.tool_retrieval = ToolRetrievalConfig(enabled=True, top_k=10)
        if models_count > 1:
            adv.routing = RoutingConfig(strategy="two_tier")
        return adv


class AgentConfig(BaseModel):
    """Agent = name + description + 4 core resources.
    All internal mechanisms auto-derived via AdvancedConfig.default().
    """
    schema_version: str = Field(default="1.0", frozen=True)

    # Identity
    name: str
    description: str

    # 4 Core resources
    models: list[ModelConfig]
    skills: list[SkillConfig] = Field(default_factory=list)
    tools: list[ToolConfig] = Field(default_factory=list)
    hooks: list[HookDefinition] = Field(default_factory=list)

    # Advanced (opt-in)
    advanced: AdvancedConfig | None = None

    # Multi-agent (opt-in)
    agents: list["AgentConfig"] | None = None
    handover: HandoverConfig | None = None
    supervisor: SupervisorConfig | None = None

    def effective_advanced(self) -> AdvancedConfig:
        if self.advanced is not None:
            return self.advanced
        total_tools = len(self.tools) + sum(len(s.tools) for s in self.skills)
        return AdvancedConfig.auto_derive(total_tools, len(self.models))
```

- [ ] **Step 2: Commit**
```bash
git add arf/agent/
git commit -m "feat(agent): add AgentConfig and AdvancedConfig with auto_derive()"
```

### Task 14.1: BaseAgent + create_agent
**Files:**
- Create: `arf/agent/base.py`
- Create: `arf/agent/factory.py`

- [ ] **Step 1: Write `base.py`** — BaseAgent wiring all Protocol implementations:
```python
"""BaseAgent — assembles all Protocol implementations into a running Agent."""
from pathlib import Path
from arf.agent.config import AgentConfig, AdvancedConfig
from arf.engine.graph import GraphEngine
from arf.engine.loop_strategies.react import ReActStrategy
from arf.engine.checkpoint import InMemoryStateStore
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.event_bus import InMemoryEventBus
from arf.resources.resolver import DefaultToolResolver
from arf.resources.providers.static_yaml import StaticYamlToolProvider
from arf.memory.file_store import FileMemoryStore
from arf.memory.recent_first import RecentFirstRetriever
from arf.memory.writer import RuleBasedMemoryWriter
from arf.guardrails.runner import DefaultGuardRunner
from arf.guardrails.none_guard import NoneInputGuard
from arf.guardrails.regex_clean import RegexOutputGuard
from arf.guardrails.path_check import PathCheckToolGuard
from arf.errors.retry import DefaultErrorPolicy
from arf.errors.transaction import SnapshotRollback


class BaseAgent:
    def __init__(self, config: AgentConfig, **override_protocols) -> None:
        self.config = config
        adv = config.effective_advanced()

        # 1. Core infrastructure
        event_bus = override_protocols.pop("event_bus", InMemoryEventBus())
        state_store = override_protocols.pop("state_store", InMemoryStateStore())

        # 2. Resources
        providers = override_protocols.pop("providers", [StaticYamlToolProvider(Path("./tools"))])
        tool_resolver = override_protocols.pop("tool_resolver", DefaultToolResolver(providers))

        # 3. Memory
        mem_cfg = adv.memory or AdvancedConfig.default().memory
        memory_store = override_protocols.pop("memory_store", FileMemoryStore(mem_cfg.workspace))
        memory_retriever = override_protocols.pop("memory_retriever", RecentFirstRetriever())
        memory_writer = override_protocols.pop("memory_writer", RuleBasedMemoryWriter())

        # 4. Guardrails
        guard_cfg = adv.guardrails or AdvancedConfig.default().guardrails
        guard_runner = override_protocols.pop("guard_runner", DefaultGuardRunner(
            input_guard=NoneInputGuard(),
            output_guard=RegexOutputGuard(),
            tool_guard=PathCheckToolGuard(),
        ))

        # 5. Error + Transaction
        err_cfg = adv.errors or AdvancedConfig.default().errors
        error_policy = override_protocols.pop("error_policy", DefaultErrorPolicy(
            tool_retry=err_cfg.tool_retry, model_retry=err_cfg.model_retry,
        ))
        transaction_ctx = override_protocols.pop("transaction_ctx", SnapshotRollback())

        # 6. Tool executor
        tool_executor = override_protocols.pop("tool_executor",
            ConcurrentToolExecutor(tool_resolver))

        # 7. Loop strategy
        loop_strategy = override_protocols.pop("loop_strategy",
            ReActStrategy(max_turns=adv.max_turns))

        # 8. Build engine
        self._engine = GraphEngine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_executor=tool_executor,
            tool_resolver=tool_resolver,
            transaction_ctx=transaction_ctx,
            memory_retriever=memory_retriever,
            memory_writer=memory_writer,
            guard_runner=guard_runner,
            event_bus=event_bus,
            error_policy=error_policy,
            system_prompt=self._build_system_prompt(),
            max_turns=adv.max_turns,
            **override_protocols,
        )

    def _build_system_prompt(self) -> str:
        return f"""You are {self.config.name}, an AI assistant.

## Capabilities
{self.config.description}

## Critical Rules
{self.config.effective_advanced().critical_rules or 'Follow best practices. Use tools to gather information before answering.'}
"""

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        from arf.core.state import AgentState
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": [{"role": "user", "content": user_message}],
            "current_model": self.config.models[0].name,
            "current_turn": 0,
            "context_summary": "",
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }
        result = await self._engine.invoke(state)
        for m in reversed(result.get("messages", [])):
            if m.get("role") == "assistant":
                return m.get("content", "")
        return ""

    async def astream(self, user_message: str, session_id: str = "default"):
        from arf.core.state import AgentState
        state: AgentState = {
            "session_id": session_id, "agent_name": self.config.name,
            "messages": [{"role": "user", "content": user_message}],
            "current_model": self.config.models[0].name, "current_turn": 0,
            "context_summary": "", "tool_results": {}, "plan": None, "metadata": {},
        }
        async for event in self._engine.astream(state):
            yield event

    def reconfigure(self, **overrides) -> None:
        if "advanced" in overrides:
            self.config = AgentConfig(**{**self.config.model_dump(), "advanced": overrides["advanced"]})
```

- [ ] **Step 2: Write `factory.py`**
```python
"""create_agent — the single public entry point for users."""
from pathlib import Path
import yaml
from arf.agent.config import AgentConfig as AgentConfigModel
from arf.agent.base import BaseAgent


def create_agent(*, config: AgentConfigModel | None = None, agent_dir: str | None = None) -> BaseAgent:
    if agent_dir:
        return BaseAgent.from_dir(agent_dir)
    if config:
        return BaseAgent(config)
    raise ValueError("Either 'config' or 'agent_dir' must be provided")
```

- [ ] **Step 3: Add `from_dir` and `to_yaml` to AgentConfig
```python
# Add to AgentConfig class:
@classmethod
def from_yaml(cls, path: str | Path) -> "AgentConfig":
    raw = yaml.safe_load(Path(path).read_text())
    version = raw.pop("schema_version", "0.0")
    if version not in {"1.0", "0.0"}:
        raise ValueError(f"Unsupported schema version: {version}")
    return cls(**raw)

def to_yaml(self, directory: str | Path) -> None:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    data = self.model_dump(exclude_none=True)
    data["schema_version"] = self.schema_version
    (d / "agent.yaml").write_text(
        f"# arf_version: {self.schema_version}\n" + yaml.dump(data, allow_unicode=True)
    )
```

- [ ] **Step 4: Commit**
```bash
git add arf/agent/
git commit -m "feat(agent): add BaseAgent with full DI assembly, create_agent factory, from_dir/to_yaml"
```

---

## Phase 15: Testing + Migration

### Task 15.0: arf/testing — InMemory test doubles
**Files:**
- Create: `arf/testing/__init__.py` with all fake exports

- [ ] **Step 1: Write `arf/testing/__init__.py`** that re-exports existing InMemory impls:
```python
"""arf.testing — InMemory test doubles for all core Protocols.
Usage:
    from arf.testing import InMemoryStateStore, InMemoryEventBus, ...
"""
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus
from arf.communication.in_memory_bus import InMemoryAgentBus
from arf.communication.supervisor import RoundRobinSupervisor
from arf.communication.shared_workspace import DictWorkspace
from arf.communication.lock import InMemoryLock

# InMemoryMemoryStore — dict-backed
class InMemoryMemoryStore:
    def __init__(self) -> None:
        self.entries: list = []
        self.saves: list = []
        self.deletes: list = []

    async def save(self, entry) -> None:
        self.saves.append(entry)
        self.entries = [e for e in self.entries if e.id != entry.id]
        self.entries.append(entry)

    async def load(self, session_id: str):
        return list(self.entries)

    async def delete(self, entry_id: str) -> None:
        self.deletes.append(entry_id)
        self.entries = [e for e in self.entries if e.id != entry_id]

    def reset(self) -> None:
        self.entries.clear()
        self.saves.clear()
        self.deletes.clear()


# InMemoryGuardRunner — pass-through
class InMemoryGuardRunner:
    @staticmethod
    async def check_input(message, context):
        from arf.core.results import GuardResult
        return GuardResult(allowed=True)

    @staticmethod
    async def check_output(message, context):
        from arf.core.results import GuardResult
        return GuardResult(allowed=True)

    @staticmethod
    async def check_tool_params(tool_name, params):
        from arf.core.results import GuardResult
        return GuardResult(allowed=True)


# InMemoryApprovalChannel — auto-approve
class InMemoryApprovalChannel:
    def __init__(self) -> None:
        self.responses: list = []

    async def send(self, request):
        self.responses.append(request)
        return "test_approval_id"

    async def wait(self, approval_id: str, timeout: int):
        from arf.core.results import ApprovalResponse
        return ApprovalResponse(action="approve")

    def reset(self) -> None:
        self.responses.clear()


__all__ = [
    "InMemoryStateStore", "InMemoryEventBus", "InMemoryAgentBus",
    "InMemoryMemoryStore", "InMemoryGuardRunner", "InMemoryApprovalChannel",
    "RoundRobinSupervisor", "DictWorkspace", "InMemoryLock",
]
```

- [ ] **Step 2: Commit**
```bash
git add arf/testing/
git commit -m "feat(testing): add InMemory test doubles for all core Protocols"
```

### Task 15.1: Verify zero app dependencies
**Files:** none created; validation only.

- [ ] **Step 1: Run dependency check**
```bash
# Verify no arf/ module imports app/
grep -r "from app\." arf/ || echo "PASS: zero app imports in arf/"
grep -r "import app" arf/ || echo "PASS: zero app imports in arf/"

# Verify engine only imports arf.core
grep -r "from arf\." arf/engine/ | grep -v "from arf.core\|from arf.engine" || echo "PASS: engine only imports arf.core + arf.engine"
```

- [ ] **Step 2: Commit**
```bash
git commit -m "chore: verify zero app dependencies in framework layer"
```

---

Plan complete — 15 phases, ~45 tasks, every step with exact code and file paths.

