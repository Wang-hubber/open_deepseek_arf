# Trace & Observability — ARFV1 vs DeepAgents

> Atomic-level comparison of event model, streaming protocols, telemetry, and replay surfaces. Every claim cites `file:line`.

---

## 1. Event Variant Coverage

### ARFV1
- File(s): `crates/arf-session/src/event.rs:20-64`
- Implementation: Closed `Event` enum with 7 variants — `OutboundSent`, `InboundReply`, `RoundStart`, `RoundEnd`, `ModelCallEnd`, `ToolCallEnd`, and a derived `PendingOutbound` row. Each variant carries a `captured_at: DateTime<Utc>` for sortable timeline reconstruction.
- Strengths: Exhaustive — covers message boundaries, round boundaries, and per-call telemetry; sum type forces exhaustiveness in consumers.
- Weaknesses: No `StreamChunk` variant; token deltas are not events (they ride `MODEL_RESPONSE_CHUNK` separately).

### DeepAgents
- File(s): `libs/deepagents/libs/ARCHITECTURE.md` (stream APIs), `CHANGELOG.md` 0.6.0
- Implementation: LangGraph exposes `astream_events(v2/v3)` returning `AIMessageChunk`, `ToolStartEvent`, `ToolEndEvent`, `OnChainStart/End`, `OnRetrieverStart/End`. Variants are namespaced strings, not a Rust-style enum.
- Strengths: Open-ended; pluggable nodes surface their own events.
- Weaknesses: No first-class `RoundStart` / `RoundEnd` concept — "round" is implicit in node edges.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add a `StreamChunk` variant to ARFV1 `Event` when token-level replay is needed; current lack limits LLM-output diffing.

---

## 2. Streaming Event Protocol

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs` (`MODEL_RESPONSE_CHUNK`)
- Implementation: Engine emits `MODEL_RESPONSE_CHUNK {content, round, turn, finish_reason}` on the actor-mailbox channel; consumers subscribe via bus router. Token-level visibility without a v3-style spec.
- Strengths: Latency-to-first-byte observable; integrates with `bus` counter (§14).
- Weaknesses: No `stream_events` mode-toggle; clients must subscribe to actor queue directly.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.0`
- Implementation: `astream_events` v3 protocol — supports `messages`, `values`, `updates`, `events` modes. Stream objects carry `event`, `name`, `run_id`, `tags`, `metadata`.
- Strengths: Mode-aware SDK; tooling (LangSmith) natively consumes v3.
- Weaknesses: Coupled to LangGraph version; churn risk (v2 → v3 breaking).

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Expose `stream_mode="events"` parity in `py-arf/src/lib.rs` `SseRelay`; map ARFV1 `MODEL_RESPONSE_CHUNK` → v3-shaped payload.

---

## 3. Round Boundary Events

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs` (`record_round_start`, `record_round_end`)
- Implementation: Emits `RoundStart { round, captured_at }` at the head of each `chat()` invocation and `RoundEnd { round, captured_at }` after final output or interrupt. Persisted via `record_event` into both JSONL and SQLite.
- Strengths: First-class round semaphore enables `case.session_id`-style eval slicing (`CLAUDE.md` boundary table).
- Weaknesses: No nested round (multi-agent round-inside-round) — flat integer only.

### DeepAgents
- File(s): N/A in source
- Implementation: Round is implied by `thread_id` resume boundary; no explicit round sentinel event.
- Strengths: Stateless model — simpler.
- Weaknesses: Eval systems must derive round from message indices; brittle.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Document round semantics as stable API contract (already in `CLAUDE.md`).

---

## 4. ModelCallEnd Telemetry

### ARFV1
- File(s): `crates/arf-session/src/event.rs:47-55`, `crates/arf-model-adapter/src/types.rs` (`ModelResponsePayload`)
- Implementation: `ModelCallEnd { round, turn, model, input_tokens, output_tokens, total_tokens, captured_at }`. Source `ModelResponsePayload` carries `latency` and `finish_reason` upstream.
- Strengths: Three token counters always present; aggregable per round.
- Weaknesses: `latency` is in the adapter payload but not propagated into `ModelCallEnd` struct — gap.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_models.py:60-118`, `middleware/summarization.py`
- Implementation: `get_model_identifier(model)` + `model_matches_spec` produce usage metadata via LangSmith callbacks. Per-call counters aggregated by middleware.
- Strengths: Provider-agnostic via callback hook.
- Weaknesses: Latency not part of standard callback; tracked externally.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add `latency_ms: u32` and `finish_reason: String` fields to `ModelCallEnd` in `crates/arf-session/src/event.rs:47`.

---

## 5. ToolCallEnd Telemetry

### ARFV1
- File(s): `crates/arf-session/src/event.rs:56-63`
- Implementation: `ToolCallEnd { round, turn, tool: String, success: bool, error: Option<String>, captured_at }`. Duration not stored; computed from `captured_at` deltas.
- Strengths: Error captured alongside success flag; backward-compatible with `None` errors.
- Weaknesses: No `args` / `result` payload — only tool name; replay needs separate event channel.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py` (CHANGELOG 0.6.8)
- Implementation: Subagent emits full ToolStart/ToolEnd with args+result into `DeltaChannel` reducer.
- Strengths: Carries args and result inline — replayable.
- Weaknesses: Volume can flood trace; no rate-limit.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Extend `ToolCallEnd` with `args: Value` and `result_summary: Option<String>` (full results are externalized to `tool_outputs` per 2026-06-15 refactor).

---

## 6. OutboundSent / InboundReply Pair

### ARFV1
- File(s): `crates/arf-session/src/event.rs:22-37`, `crates/arf-engine/src/engine.rs:504-545` (`maybe_record_outbound`)
- Implementation: `OutboundSent { msg_type, correlation_id, attempt, target, payload }` is persisted BEFORE `bus.send` (at-least-once). `InboundReply` pairs via `correlation_id`.
- Strengths: Crash-safe — pending send has a record even if engine dies mid-send.
- Weaknesses: Payload is `Value` (untyped); no schema validation.

### DeepAgents
- File(s): N/A
- Implementation: No native message-bus semantics; messages are tool args/results in graph state.
- Strengths: Uniform type system.
- Weaknesses: No at-least-once semantics; multi-agent orchestration is in-memory only.

### Gap Analysis
- Parity: ✅ (structural advantage)
- Severity: 🟡 Useful

---

## 7. PendingOutbound Record

### ARFV1
- File(s): `crates/arf-session/src/event.rs:66-80`, `crates/arf-session/src/lib.rs:487-938` (SQLite schema)
- Implementation: Separate `pending_outbound` table; `SessionStore::pending_outbound(session_id)` returns rows on boot for crash recovery before event log replay.
- Strengths: Decouples recovery from event tail; deterministic on startup.
- Weaknesses: Requires SQLite alongside JSONL — two-file consistency window.

### DeepAgents
- File(s): N/A
- Implementation: Relies on `thread_id` resume; if state is in-memory and process crashed, conversation is lost unless using a checkpointer.
- Strengths: No outbox dual-write.
- Weaknesses: No delivery guarantee for outbound peer messages.

### Gap Analysis
- Parity: ✅
- Severity: 🟠 Important

---

## 8. SSE Relay Surface

### ARFV1
- File(s): `py-arf/src/lib.rs` (`JsonlTailer`, `SseFormatter`, `EventFilter`, `SseRelay`, `SseRelayStream`)
- Implementation: Five Python-callable SSE primitives — `JsonlTailer` polls JSONL, `SseFormatter` maps ARFV1 events → SSE, `EventFilter` selects by `round`/`turn`/`msg_type`, `SseRelay` multiplexes, `SseRelayStream` exposes an async-iterable surface for `httpx`/FastAPI clients.
- Strengths: Composable; filter stage pushes selection to gateway.
- Weaknesses: No backpressure document; relay assumes fast clients.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md`
- Implementation: LangGraph's `RemoteGraph.stream_events()` returns async iterators; SSE shaping is server-side.
- Strengths: Standard protocol.
- Weaknesses: Filter logic lives downstream; cannot subscribe mid-stream with new rule.

### Gap Analysis
- Parity: ✅ (feature width)
- Severity: 🟡 Useful

---

## 9. JSONL Append-Only Log

### ARFV1
- File(s): `crates/arf-session/src/jsonl_store.rs`
- Implementation: `events.<sid>.jsonl` — newline-delimited `Event` JSON; append-only, fsync on write. Compatible with `jq` and `tail -f`.
- Strengths: Crash-safe, language-neutral replay, Unix-toolable.
- Weaknesses: No rotation/size cap — long sessions grow unbounded.

### DeepAgents
- File(s): N/A
- Implementation: Stream into memory or Postgres via `PostgresSaver`; not file-based by default.
- Strengths: Queryable via SQL.
- Weaknesses: Requires DB driver; no `tail`-style debug surface.

### Gap Analysis
- Parity: ✅ (orthogonal surface, not missing)
- Severity: 🟡 Useful
- Recommendation: Document JSONL max-size / rotation policy.

---

## 10. SQLite Event Table

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:487-938`
- Implementation: `events` table with columns `(session_id, round, turn, variant, payload_json, captured_at)` plus indexed `correlation_id`. Mirrors JSONL.
- Strengths: SQL queries: "all ToolCallEnd where success=false" possible.
- Weaknesses: Dual-write costs; reconciliation needed on schema migration.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_messages_reducer.py`
- Implementation: `DeltaChannel`-style state reducers; observability via LangGraph state inspection + `messages` reducer.
- Strengths: Strong typing in reducers.
- Weaknesses: Ad-hoc query requires custom code; no SQL surface.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful

---

## 11. Trace Replay

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:504-545`, `crates/arf-session/src/lib.rs` (InboundDedupCache)
- Implementation: Replay by `pending_outbound` table recovery + `InboundDedupCache` LRU idempotency — same `correlation_id` arriving twice is dropped.
- Strengths: Delivery guarantees across restart.
- Weaknesses: Replay of past LLM completions requires separate artifact (not in event log alone).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py`
- Implementation: Replay via `thread_id` resume — entire checkpoint state rehydrated.
- Strengths: Single-call resume.
- Weaknesses: No at-least-once outbound; no mid-graph resume.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Keep `InboundDedupCache` + add `correlation_id` index to JSONL for fast dedup lookup.

---

## 12. Per-Model Response Metadata

### ARFV1
- File(s): `crates/arf-session/src/lib.rs` (`CheckpointSnapshot.last_model_response_meta`), `crates/arf-model-adapter/src/types.rs`
- Implementation: `last_model_response_meta` fields: `provider`, `model`, `input_tokens`, `output_tokens`, `finish_reason`, `latency`.
- Strengths: One field per checkpoint — cheap introspection.
- Weaknesses: Only last call; history-by-call queries require event log.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_models.py:60-118`
- Implementation: `get_model_provider()`, `get_model_identifier()` resolve on the fly; no per-call pinned struct.
- Strengths: Reflects live model swaps.
- Weaknesses: Historical lookup requires LangSmith.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful

---

## 13. Streaming Chunk Delivery

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs` (`MODEL_RESPONSE_CHUNK`)
- Implementation: Per-token payload published to actor mailbox; consumers (CLI, SSE) tail and forward.
- Strengths: Token-level TTFB observable.
- Weaknesses: Needs custom consumer code; no JSON-RPC streaming wrapper.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.0` (`stream_events` v3)
- Implementation: `event: "on_chat_model_stream"` tokens with `data["chunk"].content`.
- Strengths: Standard SDK shape.
- Weaknesses: Schema churn (v2 → v3 broke consumers).

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Mirror v3 `data.chunk` envelope in `SseFormatter` to ease cross-framework client reuse.

---

## 14. Counter / Timing Metrics

### ARFV1
- File(s): `crates/arf-bus/src/lib.rs` (`message_count`, `start_time`)
- Implementation: Atomic `message_count: AtomicU64` (increment on every bus send) and `start_time: Instant` for uptime.
- Strengths: Lock-free hot-path accounting; powers health endpoints.
- Weaknesses: No per-round/per-channel counters; only global.

### DeepAgents
- File(s): N/A in source
- Implementation: Metrics via LangSmith/OTel; no native counter.
- Strengths: External systems handle it.
- Weaknesses: Cannot introspect without external infra.

### Gap Analysis
- Parity: ✅ (structural advantage)
- Severity: 🟡 Useful

---

## 15. Stream-Mode-Aware Client APIs

### ARFV1
- File(s): `py-arf/src/lib.rs` (`SseRelayStream`)
- Implementation: Async-iterable wrapper; consumers await via `async for` in `httpx`/FastAPI.
- Strengths: Pythonic surface over Rust actor.
- Weaknesses: No `mode="events" | "messages" | "values"` toggle — always events.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.0`
- Implementation: `RemoteGraph.astream(messages|values|updates|events)` — explicit modes.
- Strengths: Client chooses payload shape.
- Weaknesses: API surface churn between modes.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add `mode` kwarg to `SseRelayStream.__call__` mapping to ARFV1 `EventFilter`.

---

## 16. Tool Call Dedup by ID

### ARFV1
- File(s): `crates/arf-session/src/lib.rs` (InboundDedupCache)
- Implementation: LRU keyed on `correlation_id` + `source`; rejects repeat inbound.
- Strengths: O(1); survives restart via JSONL.
- Weaknesses: Only inbound — outbound dedup is attempt-counter, not LRU.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md`
- Implementation: `tool_call_id` re-key on each `ToolMessage`; reducer drops repeats.
- Strengths: Framework-level invariant.
- Weaknesses: Lost on thread_id change.

### Gap Analysis
- Parity: ✅ (more general surface)
- Severity: 🟡 Useful

---

## 17. Latency Tracking

### ARFV1
- File(s): `crates/arf-session/src/lib.rs` (`CheckpointSnapshot.response_latency_ms`)
- Implementation: Last-call latency persisted at checkpoint; read back into `last_model_response_meta`.
- Strengths: One field; cheap to compute.
- Weaknesses: Overwritten each checkpoint; no per-call ledger in state.

### DeepAgents
- File(s): N/A
- Implementation: LangSmith carries latency as a callback span attribute; not in state.
- Strengths: Per-call span.
- Weaknesses: Outside of agent state; agent code cannot introspect.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Keep `response_latency_ms` as "last" only; expose p50/p95 via metrics exporter.

---

## 18. Granularity

### ARFV1
- File(s): `crates/arf-session/src/event.rs:20-64`
- Implementation: Three layers — `Event` (per-message / per-tool-call), `CheckpointSnapshot` (per-state-change), `Session` (cross-run). Rollup controlled by `record_event` frequency.
- Strengths: Explicit; CLAUDE.md-aligned.
- Weaknesses: Granularity fixed at emit sites; cannot downsample retroactively.

### DeepAgents
- File(s): `libs/deepagents/deepagents/_messages_reducer.py`
- Implementation: Per-node, per-message; reducer aggregates into `messages`.
- Strengths: Reducer composability.
- Weaknesses: No checkpoint-layer abstraction; consumers re-implement.

### Gap Analysis
- Parity: ✅

---

## 19. LLM-Call Idempotency

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs` (in-memory `InboundDedupCache`)
- Implementation: Repeated inbound with same `correlation_id` is dropped at engine layer; LLM calls not idempotent — each `model_call` triggers a new request.
- Strengths: Stable correlation semantics for tool replies.
- Weaknesses: Two distinct `model_call` invocations for the same prompt send new requests; cost charged twice.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.9` (`count_tokens_once_per_model_call`)
- Implementation: Token counting cached by `(model, prompt_fingerprint)` for the duration of one call; not cross-call.
- Strengths: Reduces redundant counting overhead.
- Weaknesses: Not full idempotency; recomputes after process restart.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Note in docs that ARFV1 LLM-call idempotency is NOT guaranteed; cost engineers must dedup at request layer if needed.

---

## Summary

| Area | ARFV1 Wins | Gaps |
|------|-----------|------|
| Crash-safe delivery (OutboundSent+pending_outbound) | ✅ structural | — |
| SSE primitives (5 components) | ✅ | mode-toggle missing |
| SQLite + JSONL dual surface | ✅ | — |
| Round boundary events | ✅ | nested round lacking |
| ModelCallEnd tokens | ✅ | `latency`/`finish_reason` missing |
| ToolCallEnd args+result | ❌ Gap | add fields |
| Streaming chunk delivery | partial | v3-shaped payload |
| Counter / timing | ✅ | per-channel missing |
| LLM-call idempotency | ❌ Gap | document; do NOT claim |

Top three fixes (rank-ordered by severity):
1. 🟠 Extend `ModelCallEnd` and `ToolCallEnd` with metadata + args/result (sections 4, 5).
2. 🟠 Add v3-shaped `data.chunk` envelope to `SseFormatter`; add `mode` kwarg to `SseRelayStream` (sections 2, 15).
3. 🟡 Document that LLM-call idempotency is not guaranteed; rely on `InboundDedupCache` only for tool/bus replies (section 19).
