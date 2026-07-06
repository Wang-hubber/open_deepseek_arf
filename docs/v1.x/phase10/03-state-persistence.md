# State & Persistence — Atomic Comparison

> Comparison of ARFV1's session/round/turn persistence stack against DeepAgents' LangGraph-anchored state model. Each capability point is split into ARFV1 / DeepAgents / Gap Analysis.

---

## 1. State Root Schema

### ARFV1
- File(s): `crates/arf-core/src/state.rs:41-49`; `crates/arf-state/src/lib.rs:60-66`
- Implementation: Two parallel `State` aggregates. `arf_core::State` carries `messages: Vec<ModelMessage>`, `over_view: OverView` (round/turn counters, context tokens, runtime), and engine-private `wait_events: Vec<WaitEvent>`. `arf_state::State` carries `messages` + `tasks: Vec<Task>` (A2A task DAG). Engine owns the former; App owns the latter. Both `#[derive(Serialize, Deserialize)]`.
- Strengths: Explicit `OverView` metrics (round_count, turn_count, context_utilization) are O(1) to read; no need to re-scan messages. `wait_events` is segregated so App doesn't touch engine-private fields.
- Weaknesses: Two `State` types in two crates is conceptual overhead — app developers must know which crate owns what.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:65-68`
- Implementation: `DeepAgentState(TypedDict)` aggregates a single `messages` channel plus per-middleware channels (`memory`, `todos`, `files`, `skills`, `virtual_files`). Backed by LangGraph's channel/reducer machinery.
- Strengths: One unified TypedDict surface; LangGraph gives typed reducers per channel.
- Weaknesses: No precomputed aggregate metrics — `context_tokens` and `turn_count` must be derived by middleware on every read.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: ARFV1's precomputed `OverView` is a real win for checkpoint-side decisions (`when_context_over` uses `context_utilization()` directly). Document the split between `arf_core::State` (engine-owned) and `arf_state::State` (app-owned tasks) more explicitly in onboarding docs.

---

## 2. Messages Reducer — Standard vs DeltaChannel

### ARFV1
- File(s): `crates/arf-core/src/state.rs:57-62` (`push_message`); no reducer abstraction
- Implementation: Plain `Vec<ModelMessage>` with a single mutator `push_message` that appends and updates `over_view.last_user_message` for user role. No deduplication, no removal.
- Strengths: Trivial to reason about; serialization is one-shot per checkpoint.
- Weaknesses: Linear write growth — every checkpoint rewrites the entire message list. No id-based dedup, so identical messages from a tool retry produce duplicate rows.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:65-68`; `libs/deepagents/deepagents/_messages_reducer.py:31-90`
- Implementation: `messages: Annotated[list[AnyMessage], _messages_delta_reducer]` registers a custom reducer that batches deltas. The reducer applies `RemoveMessage` patches (by id) and uses an `id`-based dedup so retried tool calls merge into the same message id.
- Strengths: Delta semantics enable checkpoint diffs without rewriting the whole list; id-based dedup is replay-safe.
- Weaknesses: Reducer semantics leak into message identity — every message must carry a stable `id`.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add a `MessageId` field to `ModelMessage` and a delta-based checkpoint writer (append-only deltas instead of full `state_json` rewrites). Current SQLite `state_json` column gets fully replaced on every `snapshot()` call (`crates/arf-session/src/lib.rs:780-782`), which is O(N) per checkpoint.

---

## 3. Snapshot Frequency

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:280-285` (trait `snapshot`); invoked at the 5 Checkpoint positions in `crates/arf-core/src/checkpoint.rs:9-19`
- Implementation: Engine calls `snapshot()` at every Checkpoint (BeforeModelCall, AfterModelCall, BeforeToolExec, AfterToolExec, RoundEnd). No frequency knob — always 5+ per round.
- Strengths: Maximum granularity; replay is deterministic per turn.
- Weaknesses: On a long ReAct loop (20 turns × 5 checkpoints) this is 100 SQLite writes per round; for chatty workloads this is the bottleneck.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:65-68`; LangGraph `snapshot_frequency` default `50`
- Implementation: LangGraph configures the checkpointer with `snapshot_frequency=50` by default — every 50 supersteps the graph state is snapshotted, otherwise only the latest pointer is persisted.
- Strengths: Trades replay fidelity for write throughput; tunable per deployment.
- Weaknesses: Coarser granularity means up to 49 supersteps of work is lost on crash.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Add a `SnapshotPolicy` knob on `SessionStore::snapshot` (or `AgentConfig`) so deployments with long-running ReAct loops can opt into coarser frequency. Document the trade-off in a "Persistence tuning" section.

---

## 4. RemoveMessage Support (by-id tombstones)

### ARFV1
- File(s): None — no equivalent in `crates/arf-core/src/state.rs` or `crates/arf-session/src/lib.rs`
- Implementation: No `RemoveMessage` or tombstone mechanism. Messages are append-only in the SQLite `state_json` blob.
- Strengths: Simpler mental model — state is the literal message list.
- Weaknesses: Compaction (`docs/v1.x/phase9/`) must rewrite the entire message list and bump `state_json`. No surgical removal possible.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_messages_reducer.py:31-90`
- Implementation: `_messages_delta_reducer` matches `RemoveMessage(id=...)` entries and deletes the corresponding message by id. Old messages can be pruned without rewriting the full list.
- Strengths: Surgical deletion; replay-safe via id matching; supports summarization middleware that replaces old messages with a `RemoveMessage(all=True)` cascade.
- Weaknesses: Tombstone accumulation if not pruned.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Introduce `MessagePatch { kind: Remove, id: Uuid }` in `ModelMessage` and a delta writer so the snapshot layer can persist surgical removals. This unlocks real compaction without full message-list rewrites.

---

## 5. REMOVE_ALL_MESSAGES Sentinel

### ARFV1
- File(s): None — no equivalent
- Implementation: ARFV1's State holds `messages: Vec<ModelMessage>` directly. There is no wildcard "clear all" sentinel because messages are the source of truth.
- Strengths: No ambiguous state — every message is concrete.
- Weaknesses: Compaction has to construct a replacement list and rewrite `state_json` wholesale.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_messages_reducer.py:31-90` (REMOVE_ALL_MESSAGES sentinel)
- Implementation: Reducer recognizes a sentinel that clears the channel in one reducer step. Used by summarization middleware to drop all prior messages and replace with a summary.
- Strengths: O(1) logical "clear" operation; preserves the reducer contract.
- Weaknesses: Adds a non-message value to the channel type.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Once delta-based snapshots land (see #2 / #4), an explicit `Clear` event in the unified event log (`crates/arf-session/src/event.rs`) would give App a way to compact without a full-state rewrite.

---

## 6. Session > Round > Turn Layering

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:69-79` (SessionMeta has round_count/turn_count); `crates/arf-core/src/state.rs:13-27` (OverView)
- Implementation: Three-tier layering is first-class: `session_id` (string, lives in `SessionMeta`), `_interaction_round` (counted in `OverView.round_count`), `current_turn` (counted in `OverView.turn_count`). Each Event carries explicit `round` and `turn` fields (`crates/arf-session/src/event.rs:39-63`). Round boundary is a Checkpoint position (`RoundEnd` in `crates/arf-core/src/checkpoint.rs:18`).
- Strengths: Eval pipelines can filter at any tier by reading `data["round"]` / `data["turn"]`. Persistence layer stores all three counts.
- Weaknesses: Two `round_count` counters exist (`SessionMeta.round_count` and `OverView.round_count`) that must be kept in sync; not currently asserted in tests.

### DeepAgents
- File(s): LangGraph `thread_id` = session; `state["messages"]` carries `langgraph_trim` markers; no first-class round/turn counters
- Implementation: DeepAgents inherits LangGraph's concept of a `thread` (≈ session). Within a thread, supersteps are implicit and not enumerated in state.
- Strengths: No double bookkeeping.
- Weaknesses: Eval scripts must scan message roles/length to reconstruct round boundaries — slow and lossy.

### Gap Analysis
- Parity: ✅ (structural), ⚠️ Partial (sync invariants)
- Severity: 🟡 Useful
- Recommendation: Add a `assert!` that `SessionMeta.round_count == State.over_view.round_count` on every `save()`. Currently they could drift silently.

---

## 7. SessionStatus Preservation (Cancelling survives snapshot, R7-L2)

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:27-65`; `crates/arf-session/src/lib.rs:745-766` (R7-L2 fix in `snapshot()`)
- Implementation: Four-state enum (`Active` / `Cancelling` / `Completed` / `Interrupted`). The `snapshot()` SQL reads current `status` before forcing; if it is `'cancelling'`, the snapshot writes `'cancelling'` instead of `'interrupted'`. Test coverage at `crates/arf-session/src/lib.rs:1176-1198`.
- Strengths: Resume semantics distinguish "user cancelled mid-round" (`Cancelling`) from "process killed" (`Interrupted`). Cancellation preserves state for resume.
- Weaknesses: Two-step preservation is subtle — App must call `save()` with `Completed` to break out of `Cancelling`, otherwise the next `snapshot()` re-freezes it.

### DeepAgents
- File(s): No equivalent. LangGraph thread state has no lifecycle field; resume is by `thread_id`.
- Implementation: Thread state is alive or not — there's no "user cancelled, preserve for review" state.
- Strengths: Simpler model.
- Weaknesses: No way to distinguish "round finished cleanly" from "user aborted" in the persisted record.

### Gap Analysis
- Parity: ✅
- Severity: 🟠 Important
- Recommendation: Surface `SessionStatus` to the public CLI (`sessions list`) and the eval harness so dashboards can separate Cancelling from Interrupted sessions.

---

## 8. Checkpoint Enum Variants

### ARFV1
- File(s): `crates/arf-core/src/checkpoint.rs:9-19`
- Implementation: Five variants: `BeforeModelCall`, `AfterModelCall`, `BeforeToolExec`, `AfterToolExec`, `RoundEnd`. Engine fires `evaluate()` at each (`crates/arf-engine/src/checkpoint.rs:120-152`).
- Strengths: Fixed, exhaustive set; `CheckpointRule` declares which trigger it cares about (`trigger: Checkpoint`). Adding a sixth position is a typed change.
- Weaknesses: Closed set — domain-specific checkpoints (e.g., before tool approval) can't be inserted without engine changes.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:65-68`; LangGraph node-level granularity
- Implementation: LangGraph checkpoints at every node boundary; middleware can attach pre/post hooks but the engine itself doesn't expose a typed enum.
- Strengths: Open-ended — middleware decides where to observe.
- Weaknesses: No compile-time guarantee that every "before model call" hook has fired consistently.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Keep the typed enum; add a "domain checkpoint" hook (`BeforeHumanApproval`, `BeforePeerSend`) if the use case arises. Typed triggers > stringly-typed hooks.

---

## 9. SnapshotEffects Documentation (F-014)

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:214-231` (struct); `crates/arf-session/src/lib.rs:783-788` (return); `crates/arf-session/src/lib.rs:1154-1172` (test)
- Implementation: `snapshot()` returns `SnapshotEffects { checkpoint_written, state_updated, updated_at, status_forced }` documenting the four required side effects (write checkpoint, push state to latest, bump updated_at, force `Interrupted`). Doc comment at `crates/arf-session/src/lib.rs:213-220` explains the F-014 origin (Phase 9 issue: impl was silently doing all 4 while trait said only "append").
- Strengths: Custom impl authors get a checklist. Return value is observable.
- Weaknesses: The trait default impl (`Async-trait` style) can't enforce the 4 side effects — buggy custom impls still compile.

### DeepAgents
- File(s): Not applicable — LangGraph checkpointer is a third-party class; no ARF-style contract.
- Implementation: LangGraph's checkpointer has its own contract (`put`, `get`, `list`) that doesn't include "force status".

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Add a `SnapshotEffects` invariant test that a custom in-memory impl must pass. Consider a `#[doc(hidden)]` "conformance suite" generator.

---

## 10. SessionStore Implementations

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:487-938` (SQLite); `crates/arf-session/src/jsonl_store.rs` (JSONL)
- Implementation: Two production-ready impls sharing the `SessionStore` trait. SQLite has a 4-table schema (`sessions`, `checkpoints`, `events`, indexes); JSONL uses one append-only file per session (`events.{sid}.jsonl`). Both implement `record_event` and `pending_outbound` (Task 19).
- Strengths: SQLite for production (indexes, transactions); JSONL for debugging and small deployments. Same `SessionStore` trait — App code is storage-agnostic.
- Weaknesses: JSONL `pending_outbound` does a full file scan (`crates/arf-session/src/jsonl_store.rs:198-238`) — O(N) per restart. Acceptable for dev, not for production.

### DeepAgents
- File(s): `libs/deepagents/libs/ARCHITECTURE.md` — "Graph state and checkpoints come from LangGraph"
- Implementation: DeepAgents delegates entirely to LangGraph's checkpointer (MemorySaver, SqliteSaver, PostgresSaver). The middleware layer is checkpointer-agnostic.
- Strengths: Production-grade persistence options for free.
- Weaknesses: Third-party dependency; can't customize write semantics (e.g., R7-L2 Cancelling preservation) without forking.

### Gap Analysis
- Parity: ✅ (parity on impl count) + ⚠️ Partial (no Postgres)
- Severity: 🟠 Important
- Recommendation: Add `PostgresSessionStore` for multi-node deployments. The trait is already there; only the SQL dialect differs.

---

## 11. Event Model — 7 Variants vs LangGraph Stream Events

### ARFV1
- File(s): `crates/arf-session/src/event.rs:20-64`
- Implementation: Closed enum with 7 variants: `OutboundSent`, `InboundReply`, `RoundStart`, `RoundEnd`, `ModelCallEnd`, `ToolCallEnd`. (Note: docstring says 7 in the prompt but actual enum has 6 — `PendingOutbound` is a return-type struct, not a variant.) Each has `captured_at` (`crates/arf-session/src/event.rs:80-89`).
- Strengths: Tagged enum (`#[serde(tag = "kind", rename_all = "snake_case")]`) — consumers dispatch on string. All events go through `record_event` so the outbox is a single append-only stream.
- Weaknesses: Adding a new event requires a Rust enum variant change. Schema evolution is centralized.

### DeepAgents
- File(s): LangGraph `astream_events`; per-node stream events
- Implementation: LangGraph emits a stream of typed events (`on_chain_start`, `on_tool_end`, `on_llm_end`, ...). DeepAgents middleware subscribes via callbacks.
- Strengths: Open-ended — new event types from new middleware.
- Weaknesses: No unified tag; consumers must filter by type prefix.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Keep the typed enum; expose a Python-side `EventKind` enum mirroring the 6 variants so `py-arf` consumers can pattern-match without string fragility.

---

## 12. pending_outbound Query

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:881-937` (SQLite); `crates/arf-session/src/jsonl_store.rs:180-254` (JSONL)
- Implementation: SQLite uses a `NOT EXISTS` subquery (`correlation_id NOT IN (SELECT ... FROM events WHERE kind = 'inbound_reply')`) plus `MAX(attempt)` to handle resends. JSONL replays the whole file into HashMaps and filters in memory.
- Strengths: Single SQL query; correct semantics across resends (`MAX(attempt)` increments per resend).
- Weaknesses: SQLite `NOT IN` with NULLs can return unexpected results — handled by the `AND correlation_id IS NOT NULL` guard at `crates/arf-session/src/lib.rs:893`.

### DeepAgents
- File(s): Not applicable — DeepAgents has no outbound-message concept; messages are intra-thread.
- Implementation: N/A.

### Gap Analysis
- Parity: ✅ (for ARF-specific concern)
- Severity: 🟠 Important
- Recommendation: Add an integration test that verifies `pending_outbound` correctly handles a `peer_message` → crash → resend (attempt=2) → inbound_reply sequence.

---

## 13. resend_pending_outbound on Startup

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1376-1422`
- Implementation: Called from `Engine::run()` startup. Queries `store.pending_outbound(session_id)`, reconstructs the original message via `message_reconstruct::reconstruct_message`, increments `attempt`, calls `bus.send`, and writes a new `Event::OutboundSent` so the next restart doesn't re-send.
- Strengths: Self-healing on restart. `attempt` counter prevents infinite re-send loops. Receiver-side LRU absorbs duplicates if a send succeeded but the inbound_reply event write failed.
- Weaknesses: Best-effort (`log::error!` and continue) — a bus.send failure is logged but doesn't retry. Acceptable per spec.

### DeepAgents
- File(s): N/A — DeepAgents has no outbound outbox concept.

### Gap Analysis
- Parity: ✅ (N/A in DeepAgents)
- Severity: 🟠 Important
- Recommendation: Add metrics on resend counts so operators can detect flapping peers.

---

## 14. InboundDedupCache LRU

### ARFV1
- File(s): `crates/arf-engine/src/dedup.rs:14-42`
- Implementation: Process-level LRU cache keyed by `correlation_id`. `check_and_record` returns `true` if the cid was already seen (duplicate). Capacity clamped to ≥1 (`crates/arf-engine/src/dedup.rs:21`). Clone-shared state via `Arc<Mutex<LruCache>>`.
- Strengths: Bounded memory. Cross-process dedup is out-of-scope per the docstring (`crates/arf-engine/src/dedup.rs:1-5`) — but that's the app's responsibility.
- Weaknesses: Process-local only — a restart loses the cache, so a duplicate inbound message after restart triggers double-processing. Spec acknowledges this.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:1003-1025`; tool call id-based dedup via LangGraph
- Implementation: DeepAgents uses tool-call-id deduplication within a single thread, not an LRU.
- Strengths: LangGraph handles dedup within a thread.
- Weaknesses: No cross-restart or cross-thread dedup for inbound replies.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Persist a "seen inbound_reply cids" watermark to the events table so a restart doesn't reprocess. Currently the LRU is throwaway.

---

## 15. ModelCallRecord / ToolCallRecord Telemetry

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:458-481`
- Implementation: Two structs (`ModelCallRecord` for model usage; `ToolCallRecord` for tool success/failure + duration). Routed through `record_model_call_end` / `record_tool_call_end` which delegate to `record_event` with `Event::ModelCallEnd` / `Event::ToolCallEnd`.
- Strengths: Telemetry is first-class — round/turn + tokens/duration captured automatically.
- Weaknesses: `duration_ms` is on `ToolCallRecord` but not propagated into `Event::ToolCallEnd` (`crates/arf-session/src/event.rs:56-63` has no duration field). Lossy schema.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_state.py:11`; LangGraph stream events
- Implementation: Token counts and latencies appear in LangGraph's `on_llm_end` stream events. Not persisted by DeepAgents itself.
- Strengths: Stream events are rich.
- Weaknesses: Not durable by default — eval scripts must capture the live stream.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Add `duration_ms` to `Event::ToolCallEnd` so the JSONL store records it (SQLite already has it via `payload_json`).

---

## 16. last_model_response_meta

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:110-153` (`ModelResponseMeta`; `CheckpointSnapshot.last_model_response_meta`)
- Implementation: Optional struct on `CheckpointSnapshot` carrying `input_tokens`, `output_tokens`, `response_latency_ms`, `finish_reason`, `provider`, `model`. R5-L2 audit metadata. `None` for `BeforeModelCall` checkpoints (no response yet).
- Strengths: Six fields cover the full billing/observability surface. Optional keeps the schema lean.
- Weaknesses: Only persists in the last checkpoint — full history requires the `events` table join.

### DeepAgents
- File(s): LangGraph `RunMetadata`; per-call latency tracked in stream events
- Implementation: LangGraph tracks per-node metadata; DeepAgents middleware can read it but doesn't persist by default.
- Strengths: Live observability.
- Weaknesses: No durable audit trail without custom middleware.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Surface `ModelResponseMeta` as a JSON column on the `model_call_end` events row so dashboards don't need a checkpoint → event join.

---

## 17. Model Params Persistence (R5-L1)

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:180-194` (`SessionData.model_params: CoreModelParams`); `crates/arf-session/src/lib.rs:538` (schema column `model_params_json`); `crates/arf-session/src/lib.rs:610-625` (COALESCE for backward compat)
- Implementation: `CoreModelParams` (thinking_enabled, temperature, max_tokens, extra) is serialized into `model_params_json` column on save. `load()` uses `unwrap_or_default()` for legacy rows missing the column. Test coverage at `crates/arf-session/src/lib.rs:1240-1279`.
- Strengths: Resume restores exact runtime params — temperature, thinking, max_tokens survive a reload.
- Weaknesses: App must populate `model_params` from the active `ModelDecl` on `save()` — not automatic.

### DeepAgents
- File(s): No equivalent — model params live in the LLM call kwargs per turn, not in thread state.
- Implementation: DeepAgents passes model config to the chat model each turn; thread state doesn't persist it.

### Gap Analysis
- Parity: ✅
- Severity: 🟠 Important
- Recommendation: Provide a `EngineBuilder::with_model_decl` helper that auto-fills `SessionData.model_params` on every `save()`.

---

## 18. State Extension — `state_schema` Parameter

### ARFV1
- File(s): None in current Rust code
- Implementation: ARFV1's `State` is a fixed struct (`crates/arf-core/src/state.rs:41-49`). No per-agent extension point.
- Strengths: Predictable schema.
- Weaknesses: App-side custom state (e.g., user preferences) must be stored in `config_snapshot` (a `serde_json::Value`) — not type-safe.

### DeepAgents
- File(s): DeepAgents 0.6.6 `state_schema` parameter
- Implementation: `create_deep_agent(..., state_schema=MyState)` accepts a TypedDict subclass and merges it into the graph state.
- Strengths: Typed per-agent state extension.
- Weaknesses: Reducer conflicts possible when middleware adds overlapping fields.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Add `AgentConfig.state_extension: serde_json::Value` (or a typed extension trait) so apps can attach domain fields without touching the core `State` struct.

---

## 19. Private State Isolation

### ARFV1
- File(s): None
- Implementation: ARFV1 has no `private_state_field_names` concept. Engine-private data lives in `wait_events: Vec<WaitEvent>` on `State`, which App can technically read but docstring says "App should not touch" (`crates/arf-core/src/state.rs:47-48`).
- Strengths: Simple.
- Weaknesses: No compile-time guarantee that App doesn't peek at engine internals.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_state.py:11`; `libs/deepagents/deepagents/middleware/subagents.py:658-669`
- Implementation: `PrivateStateAttr` marker on TypedDict fields; `_validate_and_prepare_state` strips private keys before passing to subagents. Subagent invocations see only public state.
- Strengths: Compile-time / type-time guarantee that private state never leaks to subagents.
- Weaknesses: Marker is convention-based (string attribute), not enforced by TypedDict.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Mirror the pattern in `arf-subagent-pool`: when constructing a `subagent_delegate` message, strip `wait_events` and any future `#[private]` fields. Add a `is_private()` predicate on State fields.

---

## 20. Concurrent Tool Call Dedup

### ARFV1
- File(s): `crates/arf-engine/src/dedup.rs` (InboundDedupCache, LRU)
- Implementation: LRU by correlation_id — single-axis dedup. If two tool calls happen to share a correlation_id, the second is dropped.
- Strengths: Bounded memory.
- Weaknesses: No per-tool-call-id dedup; relies on the assumption that correlation_ids are globally unique.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_messages_reducer.py:31-90`
- Implementation: Id-based dedup on message ids — if a tool returns a message with an id already in the channel, it's merged (not duplicated).
- Strengths: Stable per-message identity across retries.
- Weaknesses: Requires every Message to carry an id.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Add tool-call-id as a second dedup axis in `InboundDedupCache` so a tool retry is deduplicated even if the correlation_id changes.

---

## 21. Resume Semantics

### ARFV1
- File(s): `crates/arf-core/src/state.rs:1-5` (docstring); `crates/arf-session/src/lib.rs:135-154` (`CheckpointSnapshot`)
- Implementation: Explicit `snapshot()` (Engine-side) and `restore()` (App reads `load()` and rebuilds Engine). `CheckpointSnapshot` carries `pending_messages` and `wait_events` for replay of in-flight turns.
- Strengths: Reproducible replay — `pending_messages` are re-pushed to State on restore, in-flight WaitEvents are reconstructed.
- Weaknesses: Two-phase API (App reads SessionData → App constructs Engine). App code is verbose.

### DeepAgents
- File(s): LangGraph `thread_id` resume via `invoke(config={"configurable": {"thread_id": "..."}})`
- Implementation: Resume is implicit — pass the same `thread_id` and LangGraph loads the latest checkpoint.
- Strengths: One-call API.
- Weaknesses: Hidden complexity in checkpointer lookup; no per-checkpoint inspection API like ARF's `SnapshotEffects`.

### Gap Analysis
- Parity: ✅
- Severity: 🟠 Important
- Recommendation: Provide an `Engine::resume(session_id)` shortcut that does `load() → build → run()` in one call, mirroring DeepAgents' ergonomics.

---

## 22. sqlite state_json / config_json / model_params_json Columns

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:536-539` (schema)
- Implementation: `sessions` table has three JSON columns: `state_json` (full State), `config_snapshot` (AgentConfig snapshot), `model_params_json` (CoreModelParams). All three are TEXT NOT NULL except `model_params_json` which defaults to `'{}'`.
- Strengths: Single-row read on `load()` — no JOIN needed. Schema versioning via column additions + COALESCE.
- Weaknesses: `state_json` is fully rewritten on every `snapshot()` (`crates/arf-session/src/lib.rs:780-782`) — no delta.

### DeepAgents
- File(s): LangGraph SqliteSaver
- Implementation: LangGraph stores each checkpoint as a separate row with serialized state blob.
- Strengths: Per-checkpoint row enables time-travel.
- Weaknesses: Row-per-checkpoint bloats the DB quickly.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Add a `checkpoints` row count cap (or compaction) so old checkpoints are pruned after N. Currently checkpoints table grows unbounded.

---

## 23. JSONL Append-Only Store

### ARFV1
- File(s): `crates/arf-session/src/jsonl_store.rs:93-178`
- Implementation: Each session is one file `events.{sid}.jsonl`. Lines are tagged (`{"kind": "save"|"snapshot"|"event", ...}`). `save()` writes a `save` line; `snapshot()` writes a `snapshot` line; `record_event()` writes an `event` line. `fsync` after every write (`crates/arf-session/src/jsonl_store.rs:106`).
- Strengths: Append-only — no partial writes. Easy to `tail -f` for live debugging. fsync guarantees crash-safe recovery.
- Weaknesses: `load()` scans the whole file for the latest `save` line (`crates/arf-session/src/jsonl_store.rs:53-89`). No random access.

### DeepAgents
- File(s): Not applicable — DeepAgents has no JSONL format.

### Gap Analysis
- Parity: ✅ (unique to ARFV1)
- Severity: 🟡 Useful
- Recommendation: Add a `tail -f`-style debug CLI command that streams the JSONL file for live observability.

---

## 24. recursion_limit

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs` — `max_turns` is configurable per round (no global cap)
- Implementation: Round-scoped `max_turns`; no graph-level recursion limit since Engine is not LangGraph.
- Strengths: Predictable per-round budget.
- Weaknesses: A misconfigured `max_turns=0` could deadlock — no safety net.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:1003-1025`
- Implementation: `recursion_limit=9999` (very high) — DeepAgents inherits LangGraph's default. Configurable via `config`.
- Strengths: Generous ceiling prevents accidental starvation.
- Weaknesses: 9999 is arbitrary; a runaway loop can still exhaust memory before hitting it.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Adopt a per-round `max_turns` default of 50 (DeepAgents-style generous but bounded) and document the trade-off vs the current "config required" model.

---

## Summary Scorecard

| # | Capability | Parity | Severity |
|---|------------|--------|----------|
| 1 | State root schema | ⚠️ Partial | 🟡 |
| 2 | Messages reducer | ⚠️ Partial | 🟠 |
| 3 | Snapshot frequency | ❌ Missing | 🟠 |
| 4 | RemoveMessage support | ❌ Missing | 🟠 |
| 5 | REMOVE_ALL_MESSAGES sentinel | ❌ Missing | 🟡 |
| 6 | Session > Round > Turn layering | ✅ | 🟡 |
| 7 | SessionStatus preservation | ✅ | 🟠 |
| 8 | Checkpoint enum variants | ⚠️ Partial | 🟡 |
| 9 | SnapshotEffects documentation | ✅ | 🟡 |
| 10 | SessionStore implementations | ✅ | 🟠 |
| 11 | Event model | ⚠️ Partial | 🟡 |
| 12 | pending_outbound query | ✅ | 🟠 |
| 13 | resend_pending_outbound | ✅ | 🟠 |
| 14 | InboundDedupCache LRU | ⚠️ Partial | 🟡 |
| 15 | ModelCallRecord / ToolCallRecord | ⚠️ Partial | 🟡 |
| 16 | last_model_response_meta | ✅ | 🟡 |
| 17 | Model params persistence | ✅ | 🟠 |
| 18 | State extension | ❌ Missing | 🟠 |
| 19 | Private state isolation | ⚠️ Partial | 🟡 |
| 20 | Concurrent tool call dedup | ⚠️ Partial | 🟡 |
| 21 | Resume semantics | ✅ | 🟠 |
| 22 | state_json / config_json / model_params_json | ⚠️ Partial | 🟡 |
| 23 | JSONL append-only store | ✅ | 🟡 |
| 24 | recursion_limit | ⚠️ Partial | 🟡 |

**Top 5 highest-impact gaps** (🔴 / 🟠): #2 (delta reducer), #3 (snapshot frequency), #4 (RemoveMessage), #10 (PostgresSessionStore), #17 (model_params auto-fill), #18 (state_schema), #21 (resume shortcut).