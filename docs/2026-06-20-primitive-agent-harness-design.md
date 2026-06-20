# Primitive Agent + Harness — Framework Redesign

**Date**: 2026-06-20
**Status**: approved

## 1. Motivation

Current `BaseAgent` (~980 lines) is a monolithic DI assembler. `ControlPlane` (~1338 lines) mixes
execution loop, hook scheduling, tool execution, and lifecycle management into one class. The
framework's abstractions have accreted organically — `PluginProvider`, `PluginRuntime`,
`PrimitiveHandler`, `PrimitiveContext` — each solving a specific problem but lacking a unified model.

This redesign starts from zero: **framework = agent + harness**. Agent provides six primitives.
Harness provides the execution skeleton. Everything else is a plugin.

## 2. Core Design

### 2.1 Agent — Passive Primitive State Machine

Agent is a pure message state machine with model calling capability. It knows nothing about tools,
hooks, session/turn lifecycle, sandbox, events, or any harness concept.

#### AgentState

```python
@dataclass
class AgentState:
    agent_id: str
    session_id: str
    messages: list[Message]
    waiting: dict[str, list[WaitItem]]   # hook_name -> [...]
    model_config: dict                    # enough to rebuild model connection
```

- `Message` = `{message_id, role, content}` — role is `system` | `user` | `assistant` | `tool`; content is `Any` (str for text, dict for structured tool results/images)
- `WaitItem` = `{wait_id, hook_name, reason, created_at}`
- `model_config` = `{api_base, api_key_env, model_name, context_window}` — minimal for resume
- `ModelResult` = `{content: str, tool_calls: list[{id, name, params}], usage: dict, finish_reason: str}` — tool_calls use internal format `{id, name, params}`, conversion to OpenAI format happens inside agent

AgentState contains only what is needed to restore the agent from zero. No turn counts,
tool_results, plans, interaction_round — those belong to harness.

#### Six Primitives

```
input(role, content, position="end") → Message
    Inject a message into state.messages.
    position: "end" | "begin" | int index.

model_call() → ModelResult
    Single LLM API call consuming state.messages.
    Returns {content, tool_calls, usage, finish_reason}.

wait(hook_name, reason) → WaitItem
    Append WaitItem to state.waiting[hook_name].
    Synchronous — only modifies state, does not block.

finish_wait(wait_id, reason="") → dict[str, list[WaitItem]]
    Remove WaitItem by id. Returns updated state.waiting.

stop() → AgentState
    Return current full state for persistence. Tears down model connection.

resume(state: AgentState) → PrimitiveAgent
    classmethod. Reconstruct agent from state, including model connection.
```

Key decisions:
- `wait`/`finish_wait` are synchronous state mutations — blocking logic lives in harness.
- `model_call` always consumes `state.messages` — no external message override. To influence
  the model, call `input()` first.
- `stop()` is only called at session termination — park uses `agent.state` directly without stopping.

### 2.2 Harness — Execution Skeleton + Plugin Scheduler

Harness has exactly three responsibilities:
1. Execute the run loop (call `agent.model_call()`, execute tools, inject results via `agent.input()`)
2. At each checkpoint: run plugins → check `agent.state.waiting[hook_name]` → park if non-empty
3. Park/resume mechanism

Harness does NOT do validation, guardrails, compaction, tracing — those are plugins.

#### Seven Checkpoints

```
before_model   — before agent.model_call()
after_model    — after agent.model_call() returns
before_tools   — before tool execution (only when model returns tool_calls)
after_tools    — after tool results injected
before_round   — before processing user input
after_round    — after turn loop completes
on_error       — when an exception occurs
```

Any exception at any point triggers the `on_error` checkpoint (not shown in the loop below
for brevity — it wraps every step). Error handler plugins run; if none resolve, the session aborts.

#### Execution Loop

```
session start
  │
  ▼
before_round ──▶ run plugins → check waiting["before_round"]
  │
  ▼
┌──────────────────────────────┐
│  before_model               │ run plugins → check waiting["before_model"]
│  agent.model_call()         │
│  after_model                │ run plugins → check waiting["after_model"]
│                              │
│  if tool_calls:             │
│    before_tools             │ run plugins → check waiting["before_tools"]
│    tool_executor.execute()  │
│    agent.input("tool", ...) │ for each result
│    after_tools              │ run plugins → check waiting["after_tools"]
│    ──▶ loop back            │
│  else:                      │
│    break                    │
└──────────────────────────────┘
  │
  ▼
after_round ──▶ run plugins → check waiting["after_round"]
  │
  ├─ waiting["after_round"] empty → loop ends normally
  └─ waiting["after_round"] non-empty → park
```

#### Park / Resume

```
after_round → waiting["after_round"] non-empty
  │
  ├─ harness reads agent.state → persists (backup, agent is still alive)
  ├─ emit parked event (carrying wait_items for downstream display)
  └─ wait for external signal...
       │
       ▼ (external: agentB finishes / toolC produces result)
  harness calls agent.input(role, content)         # inject result message
  harness calls agent.finish_wait(hook_name, wait_id)
       │
       ├─ waiting[hook_name] still has items → keep waiting
       └─ waiting[hook_name] empty → continue loop from next checkpoint
```

`agent.stop()` is called only at session termination (cleanup + destroy). Park does not go through
stop — the agent stays alive so `input()` and `finish_wait()` work.

### 2.3 Plugin Model

A plugin is a unit that executes at one or more harness checkpoints. It describes itself via
`plugin.yaml` and is enabled by listing its name in `harness.yaml`.

#### Plugin Structure

```python
class Plugin:
    name: str
    events: list[dict]  # [{hook_name, event_name, mode}]

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        """Called by harness at the checkpoint. event_name identifies which
        of this plugin's registered actions to execute."""
```

- `hook_name` — harness checkpoint: `"before_model"`, `"before_tools"`, etc.
- `event_name` — plugin-internal action name, also emitted on event_bus for downstream consumption
- `mode` — `"blocking"` (harness awaits, plugin failure can abort the step) or `"side"` (harness fires via `asyncio.create_task()`, failure is logged but does not block)

#### plugin.yaml (per-plugin, self-describing)

```yaml
# plugins/compaction/plugin.yaml
name: "compaction"
events:
  - {hook_name: "before_model", event_name: "compact", mode: "blocking"}
config:
  threshold: 500
  keep_recent: 10
```

```yaml
# plugins/trace/plugin.yaml
name: "trace"
events:
  - {hook_name: "after_model",  event_name: "trace_model",  mode: "side"}
  - {hook_name: "after_tools",  event_name: "trace_tools",  mode: "side"}
  - {hook_name: "after_round",  event_name: "trace_round",  mode: "side"}
  - {hook_name: "on_error",     event_name: "trace_error",  mode: "side"}
```

#### PluginContext

```python
class PluginContext:
    agent: PrimitiveAgent       # can read state, call agent.wait(), agent.input()
    hook_data: dict             # ephemeral data for this checkpoint
    session_id: str
    emit(event_type, data)      # push to event_bus (SSE + trace)
```

#### All Functionality is Plugin

| Function | Checkpoint | Mode |
|----------|-----------|------|
| Tool Guard | `before_tools` | blocking |
| Sandbox | `before_tools` | blocking |
| Approval | `before_tools` | blocking |
| Compaction | `before_model` | blocking |
| Trace | all checkpoints | side |
| Error Handler | `on_error` | blocking |
| Memory | `before_model` + `after_round` | mixed |

Framework provides reference implementations. Users can replace any plugin.

### 2.4 Plugin Registration

**harness.yaml** — only lists enabled plugin names:

```yaml
plugins:
  - compaction
  - trace
  - tool_guard
  - approval
```

Harness loads: read enabled names → find each `plugins/<name>/plugin.yaml` → register events to
checkpoints. Plugin config lives in its own `plugin.yaml`, not in harness config.

## 3. Configuration Separation

| File | Owned by | Content | Enters AgentState? |
|------|---------|---------|-------------------|
| `agent.yaml` | Agent | name, system_prompt, models | Yes (model_config) |
| `harness.yaml` | Harness | plugin list, tool sources | No |
| `plugins/<name>/plugin.yaml` | Plugin | events, config | No |

### agent.yaml

```yaml
name: "default"
system_prompt: "You are a helpful assistant."

models:
  - type: "quick"
    api_base: "https://api.deepseek.com/v1"
    api_key_env: "DEEPSEEK_API_KEY"
    model: "deepseek-chat"
    context_window: 131072
```

### harness.yaml

```yaml
plugins:
  - compaction
  - trace
  - tool_guard
  - approval
  - sandbox
  - error_handler

tools:
  sources:
    - type: "directory"
      path: "./tools"
    - type: "kernel"
      names: ["use_skill", "ask_user", "task_complete"]
```

## 4. Framework Boundaries

### Framework Built-ins (not plugin)

| Component | Responsibility |
|-----------|---------------|
| **PrimitiveAgent** | 6 primitives + state |
| **AgentHarness** | execution loop + plugin scheduling + park/resume |
| **ToolExecutor** | execute tool calls, return results (no validation) |
| **EventBus** | event channel shared by harness and plugins |

### Everything Else is Plugin

Validation, guardrails, compaction, tracing, approval, error handling, memory — all plugins.
The framework is the skeleton; plugins are the flesh.

## 5. Complete Data Flow Example

```
1. harness receives user message "write a file"
2. harness calls agent.input("user", "write a file")
3. before_round → plugins run
4. before_model → CompactionPlugin compacts old messages
                → MemoryPlugin injects memory context via agent.input("system", ...)
                → check waiting["before_model"] — empty, continue
5. agent.model_call() → {content: "", tool_calls: [{id: "1", name: "write_file", params: {...}}]}
6. after_model → TracePlugin writes to JSONL (side)
7. before_tools → ToolGuardPlugin validates tool
               → SandboxPlugin validates path
               → ApprovalPlugin checks ask list
               → check waiting["before_tools"] — agent had called wait("before_tools", "approval")
                 → park: persist state, emit parked, wait for user
8. user approves → harness calls agent.finish_wait("before_tools", wait_id)
                 → waiting["before_tools"] empty → continue
9. tool_executor.execute([write_file]) → result
10. agent.input("tool", {tool_call_id: "1", name: "write_file", result: "ok"})
11. after_tools → TracePlugin writes to JSONL (side)
12. loop → before_model → agent.model_call()
          → {content: "File written successfully.", tool_calls: []}
13. after_model → TracePlugin writes to JSONL
14. after_round → plugins run
                → waiting["after_round"] empty → loop ends
```

## 6. What Goes Away

| Removed | Reason |
|---------|--------|
| `ControlPlane` | Replaced by `AgentHarness` |
| `BaseAgent.__init__` DI assembly | Split: Agent constructor (minimal) + Harness constructor |
| `PluginProvider` | Plugins loaded from plugin.yaml by harness |
| `PluginRuntime` | Replaced by PluginContext injected at each checkpoint |
| `PrimitiveHandler` Protocol | Plugins directly implement `handle(event_name, ctx)` |
| `PrimitiveContext` | Replaced by `PluginContext` |
| `McpClientManager` | Tool sources declared in harness.yaml; MCP is one source type |
| `SessionModeManager` | Session mode becomes ApprovalPlugin config |
| `ParkCoordinator` | Park/resume is harness built-in |
| `GateChecker` | Gate logic is a plugin or harness built-in at after_round |
| `FileWatcher` | Can be a plugin or removed — hot reload is orthogonal |
| Current `agent.yaml` fields for plugins config | Each plugin owns its config |

## 7. Non-Goals

- Changing the model adapter or streaming protocol
- Changing SSE/event format
- Adding new primitives beyond the six
- Hot reload of agent config (restart is fine)
- Undo/rollback (revisit later)
