# Phase 10 / 10 — Subagent Patterns: ARFV1 vs DeepAgents

> Atomic-level comparison of subagent typing, pooling, lifecycle, state
> isolation, and metadata propagation. Every claim cites `path:line`.

---

## 1. Subagent Type Taxonomy

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:71-93` (`SubagentPool` struct), `crates/arf-engine/src/builder.rs:78-86` (`EngineBuilder::ephemeral`), `crates/arf-core/src/msg_type.rs:29-32` (`SUBAGENT_DELEGATE` / `SUBAGENT_RESULT`).
- Implementation: One runtime type — `SubagentPool` manages a bounded set of `Engine` instances built with `EngineBuilder::ephemeral(true)`. There is no separate "SubAgent" / "CompiledSubAgent" / "AsyncSubAgent" distinction; the pool serves any one-shot task via the bus. Subagent identity is a `TaskInput { user_message: String }` (`crates/arf-engine/src/engine.rs:1483-1487`).
- Strengths: One abstraction covers local, async, and remote cases (any caller that can post `subagent_delegate`); uniform lifecycle / metrics; no second compile path.
- Weaknesses: No declarative authoring surface (everything is `AgentConfig`-driven); no separate "pre-built runnable" escape hatch; remote subagents ride the same in-process bus unless an external transport adapter is bolted on.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_models.py` (3 TypedDicts), `libs/deepagents/deepagents/middleware/subagents.py:36-164` (`SubAgent`), `libs/deepagents/deepagents/middleware/subagents.py:167-244` (`CompiledSubAgent`), `libs/deepagents/deepagents/middleware/async_subagents.py:34-70` (`AsyncSubAgent`).
- Implementation: Three sibling TypedDicts cover three authoring modes. `SubAgent` is purely declarative (name/description/system_prompt/tools/model/middleware); `CompiledSubAgent` carries an already-built `Runnable` (typically a compiled LangGraph `StateGraph`); `AsyncSubAgent` carries `graph_id`, `url`, `headers` and points at a remote Agent Protocol server. The main graph's middleware routes to the right one per task.
- Strengths: Each shape matches a real authoring intent (declarative / pre-built / cross-host); typed; middleware-driven so users compose without touching the pool.
- Weaknesses: Three shapes means three lifecycle paths to test; `AsyncSubAgent` depends on a separately deployed Agent Protocol service; declarative and compiled are not interchangeable.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add a thin `SubagentSpec` TypedDict-style facade over `SubagentPool` so apps can declare pools of declarative subagents without hand-rolling `AgentConfig`. Keep one runtime path.

---

## 2. Declarative Subagent Config

### ARFV1
- File(s): `crates/arf-engine/src/config.rs` (`AgentConfig`), `crates/arf-subagent-pool/src/lib.rs:113-128` (`new_with_strategy`).
- Implementation: There is no declarative "SubAgent" record. A pool is constructed with an `Arc<AgentConfig>` (model/resources/prompt template/initial memory/tools) and a `size`. The same config is reused across every slot — there is no per-slot prompt override path inside `build_slot` (`crates/arf-subagent-pool/src/lib.rs:416-438`).
- Strengths: One config flow; the framework owns system-prompt templating and tool wiring.
- Weaknesses: Cannot express "two subagents with different system prompts sharing one pool" without standing up two pools; no equivalent of the `SubAgent.name`/`description` slot that DeepAgents exposes to the parent's tool schema.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:36-164`.
- Implementation: `SubAgent = TypedDict("SubAgent", {name, description, system_prompt, tools, model, middleware})` is the unit of authoring. Each declarative subagent gets its own system prompt, tool set, optional model, and its own middleware stack — all expressed in one TypedDict passed to `task()`.
- Strengths: Self-documenting; one record per subagent; per-subagent model and middleware overrides.
- Weaknesses: All per-subagent overrides re-invoke `create_agent`, which is heavier than slot-recycling.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Support per-slot `system_prompt_template` overrides via a `Vec<SubagentSpec>` constructor on `SubagentPool`, sharing the model/resources config.

---

## 3. Compiled Subagent (Pre-built Runnable)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:416-438` (`build_slot`).
- Implementation: No analogue. Slots are always built via `EngineBuilder::build(cfg)`; there is no path that accepts an externally-compiled state machine or graph and threads it into a slot. The pool owns the entire engine construction.
- Strengths: Predictable; one construction path = one validation surface.
- Weaknesses: Users wanting custom pre-built graphs must rebuild a pool from scratch; no composability with non-Engine runtimes.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:167-244` (`CompiledSubAgent`), `libs/deepagents/deepagents/middleware/subagents.py:552` (`_compile_spec`).
- Implementation: `CompiledSubAgent = TypedDict({name, description, runnable})` — caller hands in a pre-built LangGraph `CompiledStateGraph`. `_compile_spec` skips `create_agent` for compiled entries and invokes the runnable directly, keeping the main graph's tool surface stable.
- Strengths: Accepts arbitrary LangGraph state machines; preserves the parent's tool schema while delegating to a custom subgraph.
- Weaknesses: Caller must own the runnable's lifecycle (checkpointer, store, etc.).

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Add a `SubagentPool::with_compiled(slot_id, runnable)` slot type. Not blocking for v1.x.

---

## 4. Async / Remote Subagent (Agent Protocol)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:284-347` (`connect_to_bus`), `crates/arf-core/src/msg_type.rs:29-32`.
- Implementation: No dedicated remote-subagent type. A pool is reachable across processes only if the bus is bridged by an external transport (out of scope for the current crates). Pool advertises `subagent-pool/<pool_id>` with `capabilities.kind = "subagent-pool"` and `msg_types = [SUBAGENT_DELEGATE]` (`crates/arf-subagent-pool/src/lib.rs:289-303`).
- Strengths: Uniform wire format; if a transport adapter exists, the same pool runs unchanged.
- Weaknesses: No reference transport implementation; no Agent Protocol server analog; remote = same wire + custom transport, not first-class.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`, `libs/deepagents/examples/async-subagent-server/`.
- Implementation: `AsyncSubAgent = TypedDict({name, description, graph_id, url, headers})` — main graph's middleware calls the remote Agent Protocol `POST /assistants/{graph_id}/runs` endpoint and returns a run id.
- Strengths: First-class remote; existing `examples/async-subagent-server/` is a self-hosted reference; cross-host federation is a TypedDict field away.
- Weaknesses: Locked to Agent Protocol schema; long-running tasks need explicit polling.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Ship a `remote-agent-protocol-bridge` adapter that maps `subagent_delegate` to Agent Protocol HTTP — wire-compatible, opt-in.

---

## 5. Async Subagent Operations (start/check/update/cancel/list)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:133-211` (`delegate`), `crates/arf-subagent-pool/src/lib.rs:360-378` (`handle_delegate_msg`).
- Implementation: ARFV1 has no long-running async task concept. Each delegation is a single one-shot RPC: `delegate → run_once → TaskResult`, with the result returned synchronously over the bus via `subagent_result`. There is no separate start/check/update/cancel/list surface — `pending_peer_messages` on `TaskResult` (`crates/arf-engine/src/engine.rs:1493-1498`) is a placeholder.
- Strengths: One request/response shape; trivial error semantics.
- Weaknesses: Cannot express "kick off, check later" workflows; no cancellation token between bus hops (the local `CancellationToken` at `crates/arf-subagent-pool/src/lib.rs:159` is dropped on `recycle_slot`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:956` (`AsyncSubAgentMiddleware`).
- Implementation: Five tools — `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks`. State tracked per task id, pollable.
- Strengths: Full lifecycle; cancellation built-in; list view for orchestration.
- Weaknesses: Adds 5 tools to the parent's tool surface; remote server must implement all five.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: When Task 5 lands outbox tracking (`crates/arf-engine/src/engine.rs:1355-1360` is currently a `[]` stub), promote `delegate` to a `start_async_task` shape returning a run id.

---

## 6. Pool as Bus Actor

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:263-347` (`connect_to_bus`).
- Implementation: `connect_to_bus(self: Arc<Self>, pool_id)` registers the pool as `NodeId("subagent-pool/<pool_id>")`, with `node_type = "subagent-pool"` and a `MessageFilter` for `SUBAGENT_DELEGATE` only. A long-running `tokio::spawn`'d task loops on `handle.recv()`, dispatches each message to `handle_delegate_msg`, and replies via `handle.send(SUBAGENT_RESULT, ...)`. The `correlation_id` round-trips through both request and reply (`crates/arf-subagent-pool/src/lib.rs:317-333`).
- Strengths: Send-safe (TokioMutex + spawn); apps do `Bus → Pool node → reply`; correlation_id matches Engine's `WaitEvent` via `MessageIntent::Query` (`crates/arf-engine/src/engine.rs:504-545`).
- Weaknesses: The listener is unbounded — every `subagent_delegate` is processed sequentially per pool node (concurrency is bounded by `Semaphore::new(size)` inside `delegate`, not by the listener loop).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:737` (`SubAgentMiddleware`), `libs/deepagents/deepagents/graph.py:716-887` (pre-processing loop).
- Implementation: No bus actor — the middleware runs inline inside the parent LangGraph node. There is no separate node, no `correlation_id`, no two-hop RPC.
- Strengths: Zero wire overhead; single trace.
- Weaknesses: No cross-process / cross-language callers; tied to LangGraph's single-process execution model.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: None — this is a deliberate ARFV1 architectural choice (Phase 6 bus-actor pattern).

---

## 7. Semaphore-Bounded Pool (Backpressure)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:124` (`Semaphore::new(size)`), `crates/arf-subagent-pool/src/lib.rs:140-145` (`acquire_owned`).
- Implementation: A `tokio::sync::Semaphore` of `size` permits bounds concurrent `delegate()` calls. The 51st caller awaits `acquire_owned().await`. The semaphore is `Arc<Semaphore>`, so it is shared between `delegate` and the listener (`crates/arf-subagent-pool/src/lib.rs:90`). The active count is derived from `size - available_permits()` at `crates/arf-subagent-pool/src/lib.rs:192-204`.
- Strengths: Natural backpressure; `OwnedSemaphorePermit` self-releases on drop; works correctly across `.await`.
- Weaknesses: No timeout — caller blocks forever if all slots hang; no priority lane; no queue overflow strategy distinct from "block" (cf. `Overflow::Queue(n)` / `Reject` / `Block` in `crates/arf-pool/src/overflow.rs:7-15`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:430-435` (general-purpose), `libs/deepagents/deepagents/graph.py:822-887`.
- Implementation: No bounded pool. Each `task()` call spins up a fresh `create_agent` invocation. Concurrency is gated by LangGraph's own parallelism (typically 1 — the parent graph is single-threaded).
- Strengths: Simpler mental model; no cap means no head-of-line blocking.
- Weaknesses: Unbounded fan-out; no backpressure; resource exhaustion is the caller's problem.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: Wire `Overflow::Block(timeout)` semantics into `delegate()` so callers can fail-fast instead of hanging.

---

## 8. Slot Lifecycle (Idle → Busy → Idle)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:133-211` (`delegate`), `crates/arf-subagent-pool/src/lib.rs:382-401` (`take_slot`), `crates/arf-subagent-pool/src/lib.rs:440-450` (`recycle_slot`).
- Implementation: `delegate` acquires permit → `take_slot` pops an idle `PoolSlot` (engine + state) or builds a fresh one → `run_once` → `recycle_slot` pushes it back into the `idle` `VecDeque` if `idle.len() < size`. Idle state is implicit (presence in the queue); there is no explicit `ResourceState::Idle/Busy/Draining` enum on `PoolSlot` (the enum exists in `crates/arf-pool/src/manager.rs:5-14` but is documented as "type-level" — the in-memory state is inline in the queue).
- Strengths: Pair-engine-and-state cycle is the unit of recycling, avoiding orphaned state; multi-turn reuse preserves history on Ok, resets on Err (`crates/arf-subagent-pool/src/lib.rs:165-182`).
- Weaknesses: No `Draining` state — `shutdown` (`crates/arf-subagent-pool/src/lib.rs:257-261`) drops idle engines immediately; in-flight tasks finish on their own with no cancel signal.

### DeepAgents
- File(s): N/A (no pool).
- Implementation: Lifecycle is the LangGraph runnable's — no recycling across calls.
- Strengths: None specific.
- Weaknesses: Cold start every task.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: Consider implementing `ResourceState::Draining` so in-flight tasks can be cancelled gracefully.

---

## 9. Ephemeral Engine

### ARFV1
- File(s): `crates/arf-engine/src/builder.rs:78-86` (`ephemeral`), `crates/arf-subagent-pool/src/lib.rs:428-431` (slot build), `crates/arf-engine/src/engine.rs:1338-1353` (`reset_state`), `crates/arf-engine/src/engine.rs:1464-1480` (`run_once`).
- Implementation: `EngineBuilder::ephemeral(true)` flips a flag consulted by `reset_state` and `run_once`. Each slot gets a unique `agent_id` of the form `subagent-pool/<pool_id>/<uuid>` (`crates/arf-subagent-pool/src/lib.rs:427`) so N slots can coexist on one bus without `AlreadyConnected` collisions (Phase 9 F-018). `run_once` performs a single round (`crates/arf-engine/src/engine.rs:1470-1479`) — no multi-turn loop, no checkpoints.
- Strengths: Predictable cost model; reset-on-error path (`crates/arf-subagent-pool/src/lib.rs:178-180`); no state bleed between unrelated tasks.
- Weaknesses: `reset_state` refuses to clear messages if `collect_outbox_pending` returns non-empty (`crates/arf-engine/src/engine.rs:1339-1342`), but `collect_outbox_pending` is itself a stub returning `[]` (`crates/arf-engine/src/engine.rs:1355-1360`) — the safety check is currently unreachable.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:603-641` (`_return_command_with_state_update`), `libs/deepagents/deepagents/middleware/subagents.py:658-669` (`_validate_and_prepare_state`).
- Implementation: Each `task()` invocation calls `ainvoke(...)` on a per-subagent graph with a clean state (private keys stripped). The graph is reconstructed every call (no caching).
- Strengths: Total isolation by construction.
- Weaknesses: Reconstruction cost per call; no slot recycling.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: None.

---

## 10. OutboxStrategy (TimeoutAbort default 5000ms)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:33-49` (enum + default), `crates/arf-subagent-pool/src/lib.rs:452-470` (`apply_outbox_strategy`).
- Implementation: `enum OutboxStrategy { TimeoutAbort { timeout_ms: u64 } | HandoffOutbox { path: PathBuf } | SyncWait }`. `Default::default()` returns `TimeoutAbort { timeout_ms: 5_000 }`. Applied on `Err(RunError::Internal(reason))` when `reason.contains("OutboxNotEmpty")` (`crates/arf-subagent-pool/src/lib.rs:169-174`). The current implementation is a tracing log + `tokio::time::sleep` placeholder — outbox tracking is not yet wired (`crates/arf-engine/src/engine.rs:1355-1360`).
- Strengths: Three strategies documented; default chosen for safety; integrates with `EngineError::OutboxNotEmpty`.
- Weaknesses: Placeholder body — does not actually abort / handoff / sync-wait today; gated on an error branch that is currently unreachable.

### DeepAgents
- File(s): N/A (no outbox concept).
- Implementation: No outbox; messages are streamed directly to the parent graph's state.
- Strengths: Simpler.
- Weaknesses: Cannot replay / handoff pending messages.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Land outbox tracking (Task 5) so `apply_outbox_strategy` actually fires.

---

## 11. Metrics

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:51-62` (`PoolMetrics`), `crates/arf-subagent-pool/src/lib.rs:200-208` (update site).
- Implementation: `PoolMetrics { total_delegations: u64, active_count: usize, idle_count: usize, last_error: Option<String> }`. Snapshot under `std::sync::Mutex`. Updated post-`delegate` with queue length + `size - available_permits()`. Cheap to clone (`#[derive(Clone)]`).
- Strengths: 4 fields cover operational needs; cheap to scrape; `last_error` surfaces recent failures.
- Weaknesses: No latency histograms; no per-slot breakdown; not Prometheus-shaped.

### DeepAgents
- File(s): N/A.
- Implementation: Observability is the host graph's (LangSmith).
- Strengths: Reuses existing tooling.
- Weaknesses: Pool-level metrics are the user's problem.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: Expose `PoolMetrics` over the bus (publish on a `pool_metrics` tick) so dashboards scrape without polling.

---

## 12. Eager Warm-up (populate)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:223-235`.
- Implementation: `populate()` locks `idle`, then loops while `idle.len() < size` building slots via `build_slot` and pushing them. Safe to call repeatedly (no-op once full). Constructor cannot build because `EngineBuilder::build` is async (`crates/arf-subagent-pool/src/lib.rs:107-112`).
- Strengths: Separates cheap sync construction from async warm-up; explicit lifecycle.
- Weaknesses: Only ever used by tests in-tree — no app-level wiring.

### DeepAgents
- File(s): N/A.
- Implementation: No warm-up; first call pays full cost.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: Document `populate()` for production use; expose as a CLI flag.

---

## 13. Subagent State Isolation

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1338-1353` (`reset_state`), `crates/arf-subagent-pool/src/lib.rs:165-182` (delegate post-processing).
- Implementation: Each slot owns its own `State` (`crates/arf-subagent-pool/src/lib.rs:66-69`). On `Ok`, state is preserved across calls (multi-turn reuse, Task 5 review fix); on `Err`, `reset_state` clears messages, ReAct counters, last_user_message, wait_events. Engine-level system prefix is rebuilt each turn from template + memory + skills.
- Strengths: Hard isolation between slots; per-slot `agent_id` prevents bus collisions.
- Weaknesses: Aggressive reset on any non-Outbox error discards usable history.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:252-269` (`_EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response"}`).
- Implementation: Parent state is filtered through `_validate_and_prepare_state` (`libs/deepagents/deepagents/middleware/subagents.py:658-669`) before each `ainvoke`. Excluded keys are dropped so the child never sees the parent's tool messages / todos / structured response.
- Strengths: Explicit allow/deny list; documented; per-key customizable.
- Weaknesses: Subagent cannot see parent's tool messages even when that would be useful.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add an ARFV1-side `private_state_keys` field on `EngineBuilder::ephemeral` mirroring DeepAgents' deny list.

---

## 14. Private State Keys (strip before invoke)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:165-182`, `crates/arf-engine/src/engine.rs:1338-1353`.
- Implementation: No `private_state_keys` concept. Isolation is achieved by per-slot `State` ownership, not by stripping keys from a shared state.
- Strengths: Structurally impossible to leak.
- Weaknesses: Cannot selectively forward a subset of parent state to the child; no composable handoff.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:252-269` (`_EXCLUDED_STATE_KEYS`), `libs/deepagents/deepagents/middleware/subagents.py:658-669` (`_validate_and_prepare_state`).
- Implementation: A `PrivateStateAttr` declared on the child graph propagates to `private_state_keys`; the middleware strips those keys before invoke and re-injects the result on return.
- Strengths: Composable; round-trip; per-subagent declaration.
- Weaknesses: List is hard-coded for the three top-level keys; custom keys require subclassing.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Optional `private_keys: &[&str]` field on the future `SubagentSpec` facade.

---

## 15. Auto-inserted General-Purpose Subagent

### ARFV1
- File(s): N/A.
- Implementation: No auto-insertion. The pool serves whatever tasks the caller routes; no equivalent of a "default fallback subagent" exists.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:430-435` (`GENERAL_PURPOSE_SUBAGENT`).
- Implementation: A `general-purpose` subagent is auto-inserted at position 0 of the subagent list unless the user explicitly disables it via profile.
- Strengths: Out-of-the-box capability; users can delegate without setup.
- Weaknesses: Wastes a slot if the user only wants specialized agents.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Ship a `general_purpose` profile that spins up one slot per pool for untyped tasks.

---

## 16. Subagent Response Format (configurable in 0.6.9)

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1493-1498` (`TaskResult.output: serde_json::Value`).
- Implementation: Result payload is a JSON `Value`; the producer (engine run loop) controls shape. No per-subagent `response_format` knob; the model adapter's structured-output config governs shape.
- Strengths: Loose typing lets callers accept any structured output.
- Weaknesses: No subagent-level override; producers must coordinate via model adapter config.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.9` (`configurable subagent response_format`).
- Implementation: `response_format` field added per subagent; injected into the inner `create_agent` invocation.
- Strengths: Per-subagent output control; aligns with OpenAI's structured-output API.
- Weaknesses: Vendor-specific; subagents that mix providers lose the feature.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Add `TaskResult.schema: Option<JsonSchema>` so the engine can validate / coerce on return.

---

## 17. Subagent Metadata Propagation (0.6.8)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:317-333` (bus reply), `crates/arf-engine/src/engine.rs:1493-1498` (`TaskResult`).
- Implementation: Reply carries `correlation_id`, `ok`, `output`, `turns_consumed`, `pending_peer_messages`. No subagent-name / parent-context metadata on the wire — the pool is untyped with respect to which subagent handled the call.
- Strengths: correlation_id is preserved cleanly; light payload.
- Weaknesses: Caller cannot tell which slot / subagent answered; debugging requires correlation with `agent_id` (which is a UUID).

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.8` (`subagent metadata propagation`).
- Implementation: Each subagent invocation tags results with the subagent's name and config so the parent can route on identity.
- Strengths: Identity preserved end-to-end; required for multi-subagent orchestration.
- Weaknesses: None specific.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important · Recommendation: Add a `subagent_id` field to `SUBAGENT_DELEGATE` and echo it in `SUBAGENT_RESULT`.

---

## 18. Subagent Result Extraction (skip trailing empty end_turn)

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1464-1480` (`run_once`).
- Implementation: `run_once` returns the entire `content` string verbatim as `TaskResult.output`. There is no trailing-message stripping — the caller sees the last model output as-is, including any empty finish reason.
- Strengths: Lossless.
- Weaknesses: Caller must post-process; no canonical "user-facing output" extraction.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:603-641` (`_return_command_with_state_update`).
- Implementation: Pops the final AI message; if it is an empty `end_turn` (no content / no tool calls) it is dropped and the prior AI message is surfaced as the subagent's "response".
- Strengths: Sensible default; matches OpenAI's "last non-empty message" convention.
- Weaknesses: Heuristic — may surprise users who actually want the empty marker.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Add `Engine::extract_response(&State) -> String` helper that drops trailing empty markers; have `run_once` call it.

---

## 19. Subagent's Own Middleware Stack

### ARFV1
- File(s): `crates/arf-engine/src/builder.rs:78-86` (`ephemeral`), `crates/arf-subagent-pool/src/lib.rs:416-438` (`build_slot`).
- Implementation: Each slot inherits the parent's full engine configuration (model, resources, routes, checkpoint rules). There is no per-subagent "what middleware does the child run" field — the engine's processors / CheckpointRules apply to the slot the same way they apply to the parent. Subagent-specific behavior comes from per-slot `agent_id` and shared config.
- Strengths: Predictable — child = parent - long-lived state.
- Weaknesses: Cannot strip the parent's CheckpointRules from the child; subagents snapshot to the parent's session_store by default.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:36-164` (`SubAgent.middleware`).
- Implementation: Declarative subagents get the full default stack (TodoList / Filesystem / Summarization / Skills / PatchToolCalls) via `create_agent`; the `middleware` field lets callers add / override.
- Strengths: Per-subagent capability tuning; documented default stack.
- Weaknesses: Default stack is opinionated and not trivially disableable.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add a `subagent_overrides: AgentConfig` field on the future `SubagentSpec` so per-slot checkpoint_rules / routes can be narrowed.

---

## 20. Subagent Delegate Wire Format

### ARFV1
- File(s): `crates/arf-core/src/msg_type.rs:29-32` (`SUBAGENT_DELEGATE` / `SUBAGENT_RESULT`), `crates/arf-subagent-pool/src/lib.rs:360-378` (`handle_delegate_msg`), `crates/arf-subagent-pool/src/lib.rs:320-333` (reply payload).
- Implementation: Two `ActionMessage` types — `subagent_delegate` (parent → pool) and `subagent_result` (pool → parent). Request payload accepts `{task}` or `{user_message}` plus optional `parent_session_id`, `subagent_node_id`, `context`. Reply payload is `{correlation_id, ok, output, turns_consumed, pending_peer_messages}` on success or `{correlation_id, ok=false, error}` on failure. The `correlation_id` matches Engine `WaitEvent`s created via `MessageIntent::Query` (`crates/arf-engine/src/engine.rs:525-543`).
- Strengths: Bidirectional wire contract; correlation_id round-trip; accepts two payload shapes for convenience; Engine `CheckpointRule` (`crates/arf-engine/src/engine.rs:504-545`) can synthesize the delegate message from state.
- Weaknesses: Two payload shapes (`task` vs `user_message`) is leaky — pick one.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:737` (`SubAgentMiddleware`).
- Implementation: No wire format — the tool call is in-graph.
- Strengths: Simpler.
- Weaknesses: No cross-process.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: Deprecate `user_message` shape; standardize on `task`.

---

## 21. Eager Warm-up vs Lazy Provisioning

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:107-112` (constructor comment), `crates/arf-subagent-pool/src/lib.rs:223-235` (`populate`), `crates/arf-subagent-pool/src/lib.rs:382-401` (`take_slot`).
- Implementation: Lazy by default — `new()` cannot build async engines, so the pool starts empty. `populate()` is the explicit eager path. The lazy fallback (`take_slot`) builds on demand. Both paths share `build_slot`, so behavior is identical.
- Strengths: Caller chooses; cheap construction; warm-up when needed.
- Weaknesses: First `delegate()` after construction pays the full build cost if `populate()` was skipped.

### DeepAgents
- File(s): N/A.
- Implementation: Per-call construction; no provisioning decision.
- Strengths: None.
- Weaknesses: Always pays cold-start cost.

### Gap Analysis
- Parity: ✅ · Severity: 🔵 Informational · Recommendation: None.

---

## 22. Async Subagent via Agent Protocol (cross-host federation)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:284-347`.
- Implementation: Federation only if an external transport bridges two Bus instances. There is no first-class Agent Protocol adapter; cross-host means re-implementing the wire.
- Strengths: Wire format is transport-agnostic.
- Weaknesses: No reference implementation; manual bridge code required.

### DeepAgents
- File(s): `libs/deepagents/examples/async-subagent-server/`, `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`.
- Implementation: First-class. `AsyncSubAgent.url` + `graph_id` is the entire API; example server ships in-tree.
- Strengths: Reference; deployable; spec-defined.
- Weaknesses: Tied to Agent Protocol.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important · Recommendation: Ship `crates/arf-remote-agent-protocol/` adapter mapping `subagent_delegate` → Agent Protocol HTTP.

---

## 23. Compiled Subagent Bypass (skip inner create_agent)

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:416-438`.
- Implementation: No compiled-subagent path; every slot goes through `EngineBuilder::build(cfg).await` (`crates/arf-subagent-pool/src/lib.rs:431`).
- Strengths: Single code path.
- Weaknesses: Cannot accept pre-built runnables.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:552` (`_compile_spec`).
- Implementation: `_compile_spec` checks the type — `CompiledSubAgent` skips `create_agent` and uses the caller-provided `Runnable` directly. Pre-built graphs keep their own checkpointers / stores.
- Strengths: Performance; preserves user-supplied graph features.
- Weaknesses: Bypass path is a separate test surface.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Add a slot-kind enum so `build_slot` can pick between Engine-built and externally-built slots.

---

## Summary Scorecard

| Capability                                | ARFV1                  | DeepAgents               | Parity |
|-------------------------------------------|------------------------|--------------------------|--------|
| 1. Type taxonomy                          | 1 (SubagentPool)       | 3 (Sub/Compiled/Async)   | ⚠️     |
| 2. Declarative config                     | ❌                     | ✅                       | ❌     |
| 3. Compiled subagent                      | ❌                     | ✅                       | ❌     |
| 4. Async / remote subagent                | ⚠️ wire only           | ✅                       | ⚠️     |
| 5. Async subagent ops (5 tools)           | ❌                     | ✅                       | ❌     |
| 6. Pool as bus actor                      | ✅                     | ❌                       | ✅     |
| 7. Semaphore-bounded pool                 | ✅                     | ❌                       | ✅     |
| 8. Slot lifecycle (Idle→Busy→Idle)        | ✅                     | ❌                       | ✅     |
| 9. Ephemeral engine                       | ✅                     | ✅ (per-call)            | ✅     |
| 10. OutboxStrategy (TimeoutAbort 5000ms)  | ✅ placeholder         | ❌                       | ⚠️     |
| 11. Metrics                               | ✅ (4 fields)          | ❌                       | ✅     |
| 12. Eager warm-up (populate)              | ✅                     | ❌                       | ✅     |
| 13. State isolation                       | ✅ (per-slot State)    | ✅ (EXCLUDED_STATE_KEYS) | ✅     |
| 14. Private state keys                    | ❌                     | ✅                       | ❌     |
| 15. Auto-inserted general-purpose         | ❌                     | ✅                       | ❌     |
| 16. response_format (0.6.9)               | ⚠️                     | ✅                       | ⚠️     |
| 17. Metadata propagation (0.6.8)          | ⚠️ correlation_id only | ✅                       | ❌     |
| 18. Result extraction (skip empty end_turn)| ❌                     | ✅                       | ❌     |
| 19. Per-subagent middleware stack         | ⚠️ inherits parent     | ✅                       | ⚠️     |
| 20. Wire format                           | ✅ (ActionMessage)     | ❌ in-graph              | ✅     |
| 21. Warm-up vs lazy                       | ✅ both                | ❌                       | ✅     |
| 22. Cross-host federation                 | ⚠️ wire-only           | ✅                       | ❌     |
| 23. Compiled subagent bypass              | ❌                     | ✅                       | ❌     |

**Tally**: 9 ✅ · 8 ⚠️ · 6 ❌ across 23 capabilities.

**Top recommendations** (ordered by ROI):
1. Land outbox tracking so `OutboxStrategy::TimeoutAbort` actually fires (`crates/arf-engine/src/engine.rs:1355-1360`).
2. Add `subagent_id` field on `SUBAGENT_DELEGATE` / `SUBAGENT_RESULT` for identity propagation.
3. Ship a `SubagentSpec` declarative facade over `SubagentPool` so apps don't hand-roll `AgentConfig` per subagent.
4. Add `private_state_keys` field on ephemeral engine builder for selective handoff.
5. Add `Engine::extract_response` to drop trailing empty markers.
6. Reference implementation of Agent Protocol transport adapter for cross-host federation.