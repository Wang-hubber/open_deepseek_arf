# Agent Execution -- Control Plane Architecture

> A pure-skeleton execution loop. All behavior is plugin-injected.

## 1. Process Management Analogy

This chapter describes how an OS manages process lifecycle and scheduling, providing the conceptual foundation for ARF's execution architecture. The comparisons are illustrative -- conceptual inspiration, not implementation parity.

### 1.1 Process Lifecycle

A Unix process follows a well-defined lifecycle: `fork()` creates a copy of the current process (duplicating address space, file descriptors, signal handlers), `exec()` replaces the address space with a new program image. Between fork and exec lies a critical window where the child can modify its environment (redirect file descriptors, set resource limits) before the new program starts.

The process terminates via `exit()` (voluntary) or `signal` (involuntary). The parent collects the exit status via `waitpid()`. Zombie processes are those that have exited but not yet been waited on.

**ARF mapping**: A Session is ARF's process. `BaseAgent.__init__` is the fork-exec gap -- dependency injection assembles all Protocol implementations before execution begins. `chat()/astream()` is the exec -- the session becomes active. `session_end` is exit -- hooks clean up resources.

### 1.2 Scheduling and Time Quantums

The Completely Fair Scheduler (CFS, Linux 2.6.23) uses a red-black tree keyed by `vruntime`. Each scheduling tick selects the process with the smallest vruntime. When the current process exceeds its time quantum, the scheduler sets `TIF_NEED_RESCHED` and context-switches at the next opportunity (return to userspace).

Cgroups add hierarchical CPU weight distribution -- a configuration abstraction over the scheduler.

**ARF mapping**: The LoopStrategy is ARF's scheduler policy. `should_continue()` is the entry gate (equivalent to checking `TIF_NEED_RESCHED`). `should_break()` is the exit gate (equivalent to timeslice expiration). A Turn is a time quantum -- one model call plus its tool executions.

### 1.3 Signals and Interrupts

Unix signals (SIGINT, SIGTERM, SIGKILL) provide asynchronous notification. `SIGKILL` is uncatchable; `SIGINT` can be caught for graceful shutdown. Signal delivery to a running system call may cause `EINTR`.

**ARF mapping**: `cancel_event` (an `asyncio.Event`) is ARF's signal mechanism. It is checked at loop boundaries, not in the middle of dispatch -- analogous to checking `TIF_NEED_RESCHED` on return to userspace rather than in the middle of a syscall.

### 1.4 System Calls

The kernel's syscall interface is the boundary between user mode and kernel mode. Syscalls are synchronous, privileged operations (open, read, write, ioctl) that transition through a gate (software interrupt or `sysenter`).

**ARF mapping**: Tool calls are ARF's syscalls. The engine is kernel mode, tool call dispatch is the syscall gate. Guards (PathCheck, Permission, Approval) are the security checks performed before entering the kernel. Hook points are the tracepoints/kprobes that allow observability without modifying the syscall path.

### 1.5 Mapping Table

| OS Concept | ARF Equivalent | Location |
|-----------|----------------|----------|
| Process creation (fork+exec) | `BaseAgent.__init__` DI assembly | `arf/agent/base.py` |
| Process (address space) | Session (message list + state) | `ControlPlane._execute()` |
| Thread (lightweight sub-process) | Round (user interaction) | `BaseAgent.chat()` |
| Scheduler timeslice | Turn (model call + tool exec) | `ReActStrategy` |
| Context switch | Hook dispatch | `_fire_blocking()`, `_fire_side()` |
| Signal (SIGINT) | `cancel_event` (asyncio.Event) | `_cancelled()` |
| System call | Tool call | `_action_execute_tools()` |
| Security enforcement (SELinux) | Tool guards + approval | Plugins (ToolGuard, Approval) |
| Tracepoints/kprobes | Hook points (9 lifecycle events) | `InProcessHookRunner` |
| Process group | Round (transaction boundary) | `RoundManager` (see 2.9) |
| /proc filesystem | AgentState (inspectable state) | `arf/core/state.py` |
| Checkpoint/restart | StateStore + RoundManager | `arf/engine/checkpoint.py` |

---

## 2. Current Implementation

### 2.1 Architecture Overview

The agent execution system has four layers:

| Layer | Files | Responsibility |
|-------|-------|---------------|
| **Assembly** | `arf/agent/base.py`, `arf/agent/config.py` | DI assembly of all Protocol implementations, `agent.yaml` to running instance |
| **Engine** | `arf/engine/control_plane.py`, `arf/engine/loop_strategies/react.py`, `arf/engine/checkpoint.py`, `arf/engine/round_manager.py`, `arf/engine/tool_executor.py` | Invoke/astream main loop, state machine transitions, dispatch, state persistence |
| **Plugin (cross-cutting)** | `arf/hooks/`, `arf/plugins/` | 9 lifecycle hook points, blocking and side plugin runners |
| **Transport** | `arf/event_bus.py`, `arf/observability/`, `arf/core/events.py` | EventBus, tracing, AgentEvent definitions |

The `ControlPlane` class (`arf/engine/control_plane.py`) is a **pure-skeleton execution loop**. It provides the structural framework (session, round, turn boundaries) and dispatch (model calling, tool execution), but all domain-specific behavior -- permission checking, memory extraction, compaction, approval, error recovery -- is injected via plugins. This is a deliberate architectural shift -- the previous engine embedded those concerns inline; the ControlPlane delegates them to plugins.

The loop is driven by a `LoopStrategy` protocol that tells the engine which step to execute next. The default implementation is `ReActStrategy` (think -> act -> observe), but the protocol is designed for alternative strategies such as plan-execute.

### 2.2 Three-Tier Lifecycle: Session, Round, Turn

Agent execution is organized into three nested tiers, each with its own counter, hook events, and boundary semantics:

| Tier | Definition | Hook Events | Counter | Circuit Breaker |
|------|-----------|-------------|---------|-----------------|
| **Session** | One continuous user connection, spanning multiple rounds | `session_start`, `session_end` | `session_id: str` | -- |
| **Round** | One `chat()` / `astream()` call boundary | `round_start`, `round_end` | `interaction_round: int` (monotonic across the agent's lifetime) | -- |
| **Turn** | One model call + tool execution iteration | `turn_start`, `pre_action`, `post_action`, `turn_end` | `current_turn: int` (reset per round) | `max_turns` (per-round circuit breaker) |

**Sequence diagram**:

```
Session  --------------------------------------------------------->
         |                                                    |
         +-- Round 0 ------------------------------------->   |
         |    |  (turn resets to 0)                         |   |
         |    +-- Turn 0 --->                                |   |
         |    +-- Turn 1 --->                                |   |
         |    +-- Turn 2 ---> [end]                          |   |
         |                                                   |   |
         +-- Round 1 ------------------------------------->   |
         |    |  (turn resets to 0)                         |   |
         |    +-- Turn 0 --->                                |   |
         |    +-- Turn 1 ---> [end]                          |   |
         |                                                   |   |
         +-- Round 2 ...            stop() ----------------->   |
                                                              v
                                                           CLOSED
```

**Crash recovery** (handled by `BaseAgent.chat()` / `astream()`):

1. `session_id` already in `_active_sessions` -> reuse existing session, no session-level hooks fired
2. `session_id` not in `_active_sessions` but stored state has `session_active == True` -> process was killed, fire `session_end(reason="recovery")`, then proceed as new session
3. No stored state or `session_active` absent -> brand new session, fire `session_start`

### 2.3 ControlPlane Loop Flow

The engine's core loop is `_execute()`, an async generator that yields `AgentEvent` instances. It contains the nested session -> round -> turn structure with hook points at each boundary.

**Top-level flow**:

```
_execute(state)
  |
  +-- session_id = state.get("session_id")
  +-- interaction_round = state.get("interaction_round", 0) + 1
  +-- yield session_start event
  |
  +-- [Hook] session_start (blocking + side)
  |
  +-- while loop_strategy.should_continue(state):
  |     |
  |     +-- check _cancelled() -> yield session_end and break
  |     +-- interaction_round++
  |     +-- [Hook] round_start (blocking + side)
  |     +-- check strategy_override from round_start hook
  |     |
  |     +-- while loop_strategy.should_continue(state):
  |     |     |
  |     |     +-- turn = current_turn + 1
  |     |     +-- step = loop_strategy.next_step(state)
  |     |     +-- [Hook] turn_start (blocking + side)
  |     |     +-- [Hook] pre_action (fire_and_drain -- hooks emit events)
  |     |     +-- _execute_action(step, state, ctx)  -- inline execution
  |     |     +-- [Hook] post_action (blocking + side)
  |     |     +-- [Hook] turn_end (blocking + side)
  |     |     +-- loop_strategy.on_transition("turn_end", ctx)
  |     |     +-- state_store.put(session_id, state)
  |     |     +-- should_break? -> break inner loop
  |     |     +-- text-only response (step=="call_model", no pending tools) -> break inner loop
  |     |
  |     +-- [Hook] round_end (blocking + side)
  |     +-- should_break? -> break outer loop
  |     +-- last message is not user -> break outer loop
  |
  +-- [Hook] session_end (blocking catches exception; side always fires)
  +-- state_store.put(session_id, state)
  +-- yield session_end event
```

**Loop termination conditions**:

1. `should_continue()` returns False (entry gate blocked)
2. `_cancelled()` is True (user interrupt)
3. An error handler returns `action: "abort"`
4. Model returns pure text with no tool_calls (round complete)
5. `should_break()` returns True (exit gate triggered)
6. No new user input after round completion
7. `max_turns` reached (per-round circuit breaker)

**Error handler actions within the loop**:

| Context | Possible Actions | Effect |
|---------|-----------------|--------|
| `round_start` | `abort` | Break outer loop |
| | `continue` (default) | Skip to next round |
| `turn_start` | `abort` | Break inner loop |
| | `continue` (default) | Skip to next turn |
| `pre_action` | `abort` | Break inner loop |
| | `skip` | Continue to next iteration |
| `action` | `abort` | Break inner loop |
| | `retry` | Decrement turn, re-enter same turn |
| | `skip` | Continue to next iteration |
| | `fallback` | Save state, continue |
| | `rollback` | Break inner loop |
| `post_action` | `abort` | Break inner loop |
| | `continue` (default) | Continue to turn_end |
| `turn_end` | `abort` | Break inner loop |
| | `continue` (default) | Continue normally |
| `round_end` | `abort` | Break outer loop |
| | `continue` (default) | Continue to next round |

### 2.4 Action

The `_execute_action()` method routes execution based on the step name returned by `LoopStrategy.next_step()`. There are two action targets:

**`call_model`** -- model invocation (`_action_call_model`):

1. Resolve tool definitions via MCP tool resolver (if configured)
2. Convert internal message format to OpenAI API format (`_to_api_messages`): wraps system prompt, converts internal `{id, name, params}` tool_calls to API `{id, type, function: {name, arguments}}`
3. Validate message contract (`_validate_messages`): checks all messages are dicts, roles are valid ("user", "assistant", "tool"), sequence starts with user role
4. Emit `model_call_start` event
5. Streaming path (preferred):
   - Iterate over `_stream_model` async generator
   - Emit `thinking_delta` events for text/reasoning chunks
   - Accumulate streamed tool_calls and usage
   - On stream error: emit error event, fall through to non-streaming
6. Non-streaming path (fallback):
   - Call `_call_model` and await the complete response
7. Both paths failed -> emit error event with `"Both streaming and non-streaming model calls failed"`
8. Emit `model_call_end` event with content and usage
9. Append assistant message to state (with tool_calls if any)
10. Store pending tool_calls in `state["_pending_tool_calls"]`

**`execute_tools`** -- tool execution (`_action_execute_tools`):

1. Pop `state["_pending_tool_calls"]`
2. If none, return immediately
3. Emit `tool_call_start` events (one per tool call)
4. Execute via `ConcurrentToolExecutor.execute()` (parallel or sequential)
5. Emit `tool_call_end` events (one per tool call, with success/duration/result/error)
6. Append tool result messages to state
7. Store `state["tool_results"]` for hook inspection

### 2.5 The 9 Hook Points

The ControlPlane fires hooks at 9 lifecycle points. Each point has two hook runners: **blocking** (in-process, sequential, error-propagating) and **side** (in-process plugins awaited to completion; external subprocess hooks fire-and-forget, error-tolerant).

| # | Hook Point | When | Fire Mechanism | Context Payload | Design Purpose |
|---|-----------|------|---------------|-----------------|---------------|
| 1 | `session_start` | Beginning of `_execute()` | blocking + side | session_id, full AgentState | Session initialization, resource allocation |
| 2 | `round_start` | Each round iteration start | blocking + side | full PluginContext | Round initialization; can inject `strategy_override` via `ctx.hook_data["strategy"]` |
| 3 | `turn_start` | Each turn iteration start | blocking + side | full PluginContext with current step | Turn-level initialization |
| 4 | `pre_action` | Before `_execute_action()` call | fire_and_drain (blocking hooks emit events into stream) + side | full PluginContext | Intercept/modify action; emit approval events |
| 5 | `post_action` | After `_execute_action()` returns | blocking + side | full PluginContext with updated state | Post-execution processing |
| 6 | `turn_end` | End of each turn (after action + state_store.put) | blocking + side | full PluginContext | Turn-level teardown, state checks |
| 7 | `round_end` | After inner loop exits | blocking + side | full PluginContext | Round-level teardown, memory extraction, compaction |
| 8 | `session_end` | Before final state save | blocking (errors caught) + side (always fires) | full PluginContext | Session teardown, resource cleanup |
| 9 | `error` | When any hook or action throws | blocking only | PluginContext with `ctx.hook_data["exception"]` | Error recovery; sets `ctx.hook_data["_recovery_decision"]` |

**Special characteristics**:

- `pre_action` uses `_fire_and_drain` instead of `_fire_blocking`. This runs the blocking plugin in a background task while the engine drains events emitted by the hook via `ctx.emit()`. This allows hooks (e.g., ApprovalPlugin) to emit approval_required events into the stream without blocking the entire loop.
- `session_end` blocking hook errors are silently caught (`pass` on exception) so that session teardown cannot be prevented by a failing hook.
- `error` fires only on the blocking runner. The error hook sets `ctx.hook_data["_recovery_decision"]` to a dict with an `"action"` key. If no decision is set, the engine falls back to default actions based on exception type.

### 2.6 Plugin System

The engine uses two parallel hook runners:

**InProcessHookRunner** (`arf/hooks/in_process_runner.py`) -- for blocking plugins:

- Registers plugins that declare `hook_mode == "blocking"` for specific hook types
- Runs plugins sequentially in registration order
- On first plugin exception: subsequent plugins in the same `fire()` call are skipped, exception propagates to the engine's error handler
- Used for plugins that must execute before the engine can continue (ToolGuardPlugin, ApprovalPlugin)
- ApprovalPlugin has two resolution paths: inline callback (`chat()` path — calls `_chat_handler` directly) and event-wait (`astream()` path — emits `approval_required`, waits for `approve()` via `asyncio.Event`)
- Provides `get_plugin(name)` for direct plugin access (e.g., `BaseAgent.approve()` delegates to ApprovalPlugin)

**SubprocessHookRunner** (`arf/hooks/runner.py`) -- for side plugins:

- Registers plugins that declare `hook_mode == "side"` and external `HookDefinition` scripts
- In-process side plugins: awaited via `asyncio.gather` — all must complete before `fire()` returns, so side effects (e.g., trace writes) are guaranteed visible
- External subprocess hooks: fire-and-forget via `asyncio.ensure_future`, never block
- Never throws (exceptions are logged) for both in-process and subprocess hooks
- External hook scripts receive runtime environment variables: `ARF_RUNTIME` (JSON), `ARF_SESSION_ID`, `ARF_ROUND`, `ARF_MEMORY_DIR`, `ARF_WORKSPACE`, `ARF_TRACE_DIR`, `ARF_SYSTEM_MODEL`
- Hook commands support `$ARF_{KEY}` placeholder substitution from runtime + context merged dict

**PluginContext** (`arf/core/plugin_context.py`):

The context object passed to every hook invocation. It provides:

- `session_id`, `interaction_round`, `turn`, `current_step` -- execution identifiers
- `state` -- the full mutable AgentState (blocking plugins may mutate it; side plugins treat as read-only by convention)
- `messages` -- shortcut to `state["messages"]`
- `tool_definitions`, `system_prompt`, `model` -- execution context
- `workspace_dir`, `memory_dir`, `state_dir`, `trace_dir` -- runtime directories
- `event_bus` -- for emitting events
- `hook_data: dict` -- arbitrary key-value store for cross-hook data passing
- `plugin_config: dict` -- per-plugin configuration from plugin.yaml
- `emit(event_type, data)` -- push an AgentEvent into the engine's output stream (used by blocking plugins in `pre_action` to inject approval events)

### 2.7 Loop Strategy

**LoopStrategy protocol** (`arf/core/protocols/engine.py`):

```python
class LoopStrategy(Protocol):
    def should_continue(self, state: AgentState) -> bool: ...
    def should_break(self, state: AgentState) -> bool: ...
    def next_step(self, state: AgentState) -> str: ...

    @property
    def current_phase(self) -> str: ...

    def on_transition(self, event: str, ctx) -> None: ...
```

Three gates control the loop:

- `should_continue(state) -> bool`: Entry gate. False means the loop body (the entire round) is skipped.
- `should_break(state) -> bool`: Exit gate. True means exit the loop after this turn completes.
- `next_step(state) -> str`: Action gate. Returns the step name for the current iteration. The engine dispatches to `call_model` or `execute_tools`.

Additional protocol methods:

- `current_phase`: Read-only property exposing the internal phase for monitoring/UI
- `on_transition(event, ctx)`: Called at turn_end so the strategy can update internal state

The `max_turns` value is set by the engine from `_max_turns` (default 50) and synchronized to the strategy before the loop starts.

**ReActStrategy** (`arf/engine/loop_strategies/react.py`) -- the only current implementation:

Implements the think -> act -> observe cycle with two phases tracked by `_phase`:

```
Phase flow:
  user message   -> next_step() -> "call_model"   (_phase = "think")
  model returns tool_calls -> next_step() -> "execute_tools" (_phase = "act")
  tool results   -> next_step() -> "call_model"   (_phase = "think")
  model returns text -> next_step() -> "call_model" (next round)
                        should_continue checks -> break if no new input
```

The `next_step` logic reads the last message role:

- Empty messages or last role is "user"/"system" -> `"call_model"`
- Last role is "assistant" with tool_calls -> `"execute_tools"`
- Last role is "tool" -> `"call_model"` (observe -> think)

Both `should_continue` and `should_break` currently only monitor `current_turn` against `max_turns`. The strategy can be swapped at runtime via `strategy_override` from the `round_start` hook (see section 2.5, hook point 2).

### 2.8 State Management

**StateStore protocol** (`arf/core/protocols/engine.py`):

```python
class StateStore(Protocol):
    async def put(self, session_id: str, state: AgentState) -> None: ...
    async def get(self, session_id: str) -> AgentState | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def list_sessions(self) -> list[str]: ...
```

Two implementations exist:

**FileStateStore** (`arf/engine/checkpoint.py`):

- Persists state to `<state_dir>/<session_id>.json`
- Atomic writes: writes to a `.tmp` file first, then `rename()` to the target path
- **`tool_results` is stripped before persistence** (`data.pop("tool_results", None)`) -- tool results are ephemeral and should not survive restarts
- Recovery from corruption: returns None on JSON decode error, logs a warning
- Survives process restarts

**InMemoryStateStore** (`arf/engine/checkpoint.py`):

- Dict-backed, fast but lost on process restart
- Maintains a `snapshots: list[dict]` recording every `put()` call for testing
- `reset()` clears all data
- **Does NOT strip `tool_results`** -- as a test double, it preserves full state

**Where StateStore.put() is called in the engine**:

1. After action (during error recovery for `retry` / `fallback` actions)
2. At the end of every turn (`turn_end` hook fires first, then `put()`)
3. At session end (before yielding `session_end` event)

**AgentState structure** (`arf/core/state.py`, `AgentState` is a `TypedDict` with `total=False`):

Public fields:

| Field | Type | Set By | Description |
|-------|------|--------|-------------|
| `session_id` | `str` | BaseAgent.chat() | Session unique identifier |
| `agent_name` | `str` | BaseAgent.chat() | Currently active agent name |
| `messages` | `list[dict]` | Engine (each turn) | OpenAI-format message list |
| `current_model` | `str` | Init + ModelDegrader | Current model name |
| `current_turn` | `int` | Engine loop | Turn counter (model + tool exec) |
| `interaction_round` | `int` | Engine loop | Monotonic round counter |
| `context_summary` | `str` | Compaction | Compacted history summary |
| `tool_results` | `dict[str, dict]` | Post-execution | tool_call_id -> ToolResult (ephemeral, not persisted) |
| `plan` | `dict \| None` | Planner (reserved) | Execution plan |
| `metadata` | `dict` | App/user | Free-form extension metadata |
| `session_active` | `bool` | BaseAgent | Session liveness flag |

Engine-internal fields (not publicly documented, subject to change):

| Field | Type | Description |
|-------|------|-------------|
| `last_token_usage` | `int` | Total tokens from last API call, used by compaction |
| `_pending_tool_calls` | `list[dict]` | Cross-step: tool_calls awaiting execution |
| `_compaction_cooldown` | `int` | Decreasing counter preventing consecutive compactions |
| `session_title` | `str` | Optional session title |

### 2.9 RoundManager -- Checkpoint and Undo

`RoundManager` (`arf/engine/round_manager.py`) provides round-level snapshot and undo capability. It is **defined but not yet wired** into BaseAgent or ControlPlane -- the class exists with full implementation, but no code currently calls `begin_round()` or `undo()`.

**`RoundTransaction`** -- one user-interaction round's snapshot:

```python
@dataclass
class RoundTransaction:
    round_id: str                       # "session_id/round_num"
    round_num: int                      # monotonic
    state_snapshot: dict                # deepcopy(AgentState) at round start
    workspace_snapshot_dir: str | None  # data/checkpoints/{round_num}/
    created_at: float
    agent_trace: list[str]              # agent names visited in this round
    closed: bool
```

**Key behaviors**:

- `begin_round(state, workspace_dir)` -> deepcopies AgentState + snapshots workspace files to `data/checkpoints/{round_num}/`. Also writes `state.json` for crash-safe undo recovery.
- `undo(steps)` -> pops N transactions, restores workspace files from the oldest popped snapshot, returns deepcopy of that state. Cleans up checkpoint directories >= the target round number.
- `close_round()` -> marks active transaction as closed.
- `max_undo_depth` -> default 3, uses `deque(maxlen=N)` for automatic oldest-eviction.
- Persistence: `rounds.json` (metadata only, not full state) is written to `data/checkpoints/`. `_restore_from_disk()` on construction reads this file and re-attaches state snapshots from checkpoint dirs, enabling undo across process restarts.

| Component | StateStore | RoundManager (not yet wired) |
|-----------|-----------|------------------------------|
| Trigger | Engine loop (turn_end, session_end) | Each chat()/astream() entry |
| Granularity | Multiple times per turn | Once per round |
| Contents | AgentState (excluding tool_results) | Deepcopied AgentState + workspace files |
| Purpose | Crash recovery | Undo/rollback |
| Restoration path | `StateStore.get()` | `RoundManager.undo(N)` |

### 2.10 Tool Execution

**ConcurrentToolExecutor** (`arf/engine/tool_executor.py`) executes tool calls with configurable concurrency.

**Execution strategies**:

| Strategy | Implementation |
|----------|---------------|
| `"parallel"` (default) | `asyncio.gather` with `Semaphore(max_concurrency=5)` |
| `"sequential"` | `for` loop awaiting each tool |

**Automatic parameter injection**: Each tool call's params dict receives:
- `_agent_mode` -- current active agent name (when `agent_mode != ""`)
- `_engine` -- ControlPlane reference (for tools that need to interact with the engine)
- `_state_store` -- StateStore reference
- `_workspace` -- workspace directory path
- `session_id` -- current session ID

**Path parameter resolution** (`_resolve_path_params`): Parameter names ending in `_path`, `_file`, `_dir`, or matching the set of known path names (`path`, `file_path`, `file`, `output_dir`, `input_dir`, `cwd`) are resolved relative to the workspace directory. Framework directory params (`memory_dir`, `state_dir`, `trace_dir`, `files_dir`) are NOT resolved. Absolute paths are left as-is (security validation is handled by PathCheckToolGuard upstream).

**Guard check** (`_check_params`, before execution):

1. Resolve directory boundary: tool-specific boundary (from `tool_boundaries` dict) -> sandbox boundary (from `SandboxManager`) -> default boundary (workspace root)
2. Run `PathCheckToolGuard.check()` if both `tool_guard` and `boundary` are configured
3. If guard blocks: return `ToolResult(blocked=True, error="[PathCheck] reason")`
4. If guard passes: execute the tool via `ToolResolver.execute()`

**ToolResult structure** (from `arf/core/results.py`):

```
success: bool        -- execution succeeded
data: Any            -- result data (if success)
error: str           -- error message (if not success)
duration_ms: float   -- execution wall-clock time
blocked: bool        -- true if blocked by guard (not executed)
rolled_back: bool    -- true if rollback was attempted
rollback_error: str  -- rollback failure message
```

### 2.11 Error Handling

The `_handle_error()` method (`control_plane.py:484`) is called when any hook or action operation throws an exception.

**Flow**:

1. Sets `ctx.hook_data["exception"]` to the exception
2. Fires the `error` blocking hook
3. If the error hook itself fails: emits an error event, returns `_default_error_action(exc)`
4. If the error hook succeeded: reads `ctx.hook_data["_recovery_decision"]`
5. If no recovery decision was set: emits an error event, returns default action
6. Returns the decision dict to the caller, which acts on it (see the table in 2.3)

**Default error actions by exception type**:

| Exception Type | Default Action |
|---------------|----------------|
| `PermissionDenied`, `ApprovalDenied`, `SandboxViolation`, `ApprovalTimeout` | `{"action": "skip"}` -- guard/approval blocked the tool, let the model see the tool_result |
| All others | `{"action": "abort", "params": {"user_message": str(exc)}}` |

**Error events**: The engine emits an `AgentEvent(type="error")` via the event bus when:
- A hook fails during error handling (the error hook itself crashed)
- No recovery decision was returned by the error hook
- Streaming model calls fail (caught by `_action_call_model`)

### 2.12 BaseAgent -- Assembly and Public API

`BaseAgent.__init__()` (`arf/agent/base.py`) assembles all Protocol implementations in a fixed order. Each can be overridden via `**override_protocols`:

| Step | Component | Default Implementation |
|------|-----------|----------------------|
| 1 | EventBus | `InMemoryEventBus` |
| 2 | StateStore | `FileStateStore(state_dir)` |
| 3 | MCP Manager | `McpClientManager(tools/skills/models/plugins dirs)` |
| 4 | Plugin Provider | `PluginProvider` (from plugins directory) |
| 5 | FileWatcher | `FileWatcher` (optional, disabled if config says so) |
| 6 | Memory | `FileMemoryStore(memory_dir)` |
| 7 | PluginRuntime | `PluginRuntime(memory/workspace/state/trace dirs)` |
| 8 | Guardrails | `DefaultGuardRunner(NoneInputGuard, RegexOutputGuard, PathCheckToolGuard)` |
| 9 | Permission System | `SessionModeManager + PermissionRegistry` |
| 10 | ErrorPolicy | `DefaultErrorPolicy(tool_retry=2, model_5xx_action="fallback")` |
| 11 | Hooks | `SubprocessHookRunner(config hooks + plugin hooks)` |
| 12 | ToolExecutor | `ConcurrentToolExecutor(strategy, max_concurrency, tool_guard, boundaries)` |
| 13 | LoopStrategy | `ReActStrategy(max_turns=50)` |
| 14 | System Prompt | `DefaultSystemPromptProvider.build()` with `$INVENTORY` (MCP) and `$MEMORY` (resident) filled |
| 15 | Model Calls | `_inject_model_calls()`: ModelDegrader + optional ModelCallProtector |
| 16 | Plugins | `ToolGuardPlugin` (blocking, deny/allow/deny_patterns) and `ApprovalPlugin` (blocking, ask list) |
| 17 | ControlPlane | Constructed with all assembled dependencies |

**Model call injection** (`_inject_model_calls`):

1. Build `ModelDegrader` from `model_defs` (new format) or `config.models` (legacy): ordered list of `ModelAdapter` instances for fallback
2. Optionally wrap with `ModelCallProtector` (rate limiting + circuit breaker, TODO #10)
3. Inject via `engine.set_call_model()` and `engine.set_stream_model()`
4. Internal `_call_model` and `_stream_model` closures handle message formatting, tool schema conversion, response parsing

**Blocking plugins constructed from permission config**:

- `ToolGuardPlugin`: Handles `deny` (immediate rejection), `allow` (bypass), and `deny_patterns` (dangerous content check)
- `ApprovalPlugin`: Handles `ask` (human-in-the-loop wait), registered on `pre_action` hook to emit approval_required events
- Auto-discovered plugins from `PluginProvider` are appended after these two (with special plugins `tool_guard` and `approval` skipped to avoid duplication)

**Public API**:

- `chat(user_message, session_id, on_approval=None)` -> `str`: Sync invocation. Manages session lifecycle (recovery, hooks), builds state, calls `engine.invoke(state)`, returns the last assistant message text. Since `invoke()` blocks without yielding events (no consumer for `approval_required` events), `chat()` supports an inline `on_approval(tool_name, params) -> bool` callback for approval decisions. Without `on_approval`, any ask-list tool triggers a `RuntimeError` immediately (not a silent 60s timeout).
- `astream(user_message, session_id)` -> `AsyncGenerator[AgentEvent]`: Streaming variant. Same lifecycle management, delegates to `engine.astream(state)`. Approval uses event-based flow (`approval_required` event → consumer calls `approve(decision_id, approved)`).
- `start()`: Start FileWatcher and MCP manager (must be called after the event loop is running).
- `stop()`: Stop FileWatcher, MCP manager, close all active sessions (fires `session_end(reason="shutdown")` hooks).
- `approve(decision_id, approved)`: Delegate approval to the ApprovalPlugin (if registered).
- `reconfigure(**overrides)`: Update config at runtime.
- `evaluate(benchmark)`: Run an EvalBenchmark against this agent.

**Approval flow — chat() vs astream()**:

`chat()` uses `engine.invoke()` which runs the ControlPlane loop to completion without yielding intermediate events. The normal approval path (`pre_action` → `approval_required` event → consumer calls `approve()`) depends on an event consumer, which doesn't exist in the `invoke()` path. Instead, `chat()`:

1. Sets `ApprovalPlugin._chat_handler = on_approval` and `_chat_mode = True` before `invoke()`
2. When ApprovalPlugin encounters an ask-list tool, it checks `_chat_handler` first — if present, calls it directly (supporting both sync and async callables via `iscoroutine` check) to get the approval decision inline
3. If `_chat_mode` is True but no `_chat_handler` is set, raises `RuntimeError` immediately (fail-fast, no silent timeout)
4. Clears both attributes in `finally` after `invoke()` returns

`astream()` uses `engine.astream()` which yields events to a consumer — the event-based `approval_required` → `approve()` flow works normally.

**External hook system**: BaseAgent also maintains its own `SubprocessHookRunner` (separate from ControlPlane's side runner) which fires:
- `session_start` (if new session, before engine invoke)
- `round_start` (before engine invoke)
- `session_end` (on stop/shutdown)

These are subprocess hooks defined in `agent.yaml`, used for logging, metrics, and external integrations. They receive a simpler context dict (not a PluginContext) and fire before the engine-internal hooks.

### 2.13 Event Types

**AgentEvent** (`arf/core/events.py`):

```python
@dataclass
class AgentEvent:
    type: EventType       # literal string from the union
    data: dict            # event-specific payload
    timestamp: float      # auto-set to time.time()
    trace_id: str         # distributed tracing
    span_id: str
    parent_span_id: str | None
    session_id: str
    agent_name: str
    turn: int
```

**EventType union** (31 literal values):

Events emitted by the engine (ControlPlane):

| Event | When | Data Payload |
|-------|------|-------------|
| `session_start` | _execute begins | `{session_id}` |
| `session_end` | _execute exits | `{session_id, reason?}` |
| `model_call_start` | Before model API call | `{model, turn}` |
| `thinking_delta` | Each streamed chunk | `{content, reasoning}` |
| `model_call_end` | After model API call | `{model, turn, content, usage}` |
| `tool_call_start` | Tool execution starts | `{tool_name, turn, id, arguments}` |
| `tool_call_end` | Tool execution finishes | `{tool_name, turn, id, success, duration_ms, result, error}` |
| `error` | Error during action | `{phase, exception, detail, message}` |

Events available for plugins to emit via `ctx.emit()` or the EventBus (defined in the type union but not emitted by the engine itself):

| Event | Purpose |
|-------|---------|
| `user_input` | User message received |
| `compaction_start` / `compaction_end` | Context compression |
| `approval_required` / `approval_resolved` | Human-in-the-loop approval |
| `guard_block` / `guard_pass` | Guard check results |
| `hook_start` / `hook_end` | Hook execution trace |
| `undo_executed` | Undo boundary marker |
| `rollback_executed` | Tool rollback completed |
| `rate_limited` | Rate limit hit (protection) |
| `circuit_opened` / `circuit_half_open` / `circuit_closed` | Circuit breaker transitions |
| `breaker_blocked` | Breaker blocked a request |
| `pre_model_call` | Before model call (plugin mount point) |
| `post_permission` | After permission check (plugin mount point) |
| `sandbox_persist` | Before sandbox persistence (plugin mount point) |
| `tool_call_result` | Tool result (used by ReplayController) |

Note: `hook_start`, `hook_end`, and `undo_executed` are defined in the type union but are **not emitted** by the current engine. They exist for future use or for plugin-emitting.

### 2.14 Dual Hook System

The ControlPlane and BaseAgent maintain separate hook systems that fire at overlapping points:

| Hook Point | BaseAgent (SubprocessHookRunner) | ControlPlane Blocking (InProcessHookRunner) | ControlPlane Side (SubprocessHookRunner) |
|-----------|--------------------------------|---------------------------------------------|------------------------------------------|
| `session_start` | Yes (if new session, before invoke) | Yes | Yes |
| `round_start` | Yes (before invoke) | Yes | Yes |
| `turn_start` | -- | Yes | Yes |
| `pre_action` | -- | Yes (fire_and_drain, can emit events) | Yes (after blocking) |
| `post_action` | -- | Yes | Yes |
| `turn_end` | -- | Yes | Yes |
| `round_end` | -- | Yes | Yes |
| `session_end` | Yes (on stop/shutdown) | Yes (errors silently caught) | Yes |
| `error` | -- | Yes | -- |

This dual system is a deliberate separation of concerns:

- **BaseAgent hooks** (`self._hook_runner`): External subprocess scripts defined in `agent.yaml`. Session lifecycle notifications for external systems (logging, metrics, monitoring).
- **ControlPlane blocking hooks** (`self._blocking`): In-process Python plugins that can block execution, mutate state, and interact with the event stream. Permission checking, approval, memory extraction, compaction.
- **ControlPlane side hooks** (`self._side`): Fire-and-forget subprocess and in-process hooks for observability, tracing, and non-blocking side effects.

---

## 3. Evolution Directions

The following directions are identified but **not yet implemented**. They are ordered by estimated priority.

### 3.1 Plan-Execute Loop Strategy

**Status**: The `Planner` protocol is defined in `arf/core/protocols/engine.py` (with `generate_plan`, `update_progress`, `detect_divergence`, `revise` methods). No `PlanExecuteStrategy` implementation exists.

The plan-execute pattern would add a new LoopStrategy implementation:

- **Plan phase**: The planner generates a step-by-step execution plan from the user's task
- **Execute phase**: The engine executes plan steps, with divergence detection between expected and observed outcomes
- **Replan threshold**: When divergence exceeds a threshold, the planner revises the plan
- The `next_step()` method would dispatch to plan, execute_step, or revise based on the current phase

### 3.2 Multi-Agent DAG Orchestration

**Status**: Not implemented. The previous handoff infrastructure has been removed. No replacement exists.

The vision is DAG-style orchestration where a supervisor agent decomposes a task and dispatches sub-tasks to multiple child agents concurrently (fork), then collects results (waitpid/join). The current architecture would implement this through:

- A plugin attached to `round_start` that inspects state and creates sub-agents
- The `strategy_override` mechanism (already present in `round_start` hook) to swap execution strategy
- State isolation via separate session IDs or namespaced state keys

### 3.3 Preemptive Cancellation

**Status**: Current cancellation is cooperative -- the `cancel_event` is checked only at loop boundaries (before each turn begins). If a model call is in progress, the user must wait for it to complete.

Future: Cancel in-flight HTTP requests to the model API. This requires API client support (`httpx`/`openai` client cancellation). The `_cancelled()` check could be augmented with a background task that forcefully cancels the current action when the event is set.

### 3.4 Turn-Level Checkpointing

**Status**: The `RoundManager` class exists (full implementation with state snapshot, workspace snapshot, disk persistence, and restore) but is **not wired** into BaseAgent. Beginning a round's checkpoint and restoring on undo are not connected.

Short-term: Wire `RoundManager.begin_round()` / `close_round()` into `BaseAgent.chat()` / `astream()`. This enables undo functionality.

Medium-term: Add `undo` event emission (the `undo_executed` event type is already defined). Expose undo through the agent's public API.

Long-term: Consider turn-level checkpointing for finer-grained recovery, balancing storage cost against recovery granularity.

### 3.5 Extended Loop Controls

**Status**: Both `should_continue()` and `should_break()` currently only monitor `current_turn` against `max_turns`.

Future extensions to the LoopStrategy protocol:

- **Token budget**: Stop when accumulated token usage exceeds a threshold
- **Time budget**: Stop when wall-clock time exceeds a limit
- **Tool call count**: Stop after N tool calls in a round
- **Error count**: Stop after consecutive model or tool failures

These would be additive -- the strategy combines all active constraints with logical OR (any gate triggers termination).

### 3.6 Dynamic Strategy Switching

**Status**: The `strategy_override` mechanism in the `round_start` hook is minimal -- a hook can replace the strategy instance, but there is no framework support for phased execution (e.g., plan -> react -> reflect).

Future: A meta-strategy that delegates to sub-strategies based on phase, allowing complex multi-phase agent behaviors without changing the engine.
