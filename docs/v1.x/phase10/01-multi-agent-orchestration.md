# Phase 10 / 01 — Multi-Agent Orchestration: ARFV1 vs DeepAgents

> Atomic-level comparison covering cross-engine federation, team abstraction,
> barrier coordination, node failure recovery, and sub-agent pools. Every claim
> cites `path:line`.

---

## 1. Cross-Engine Bus / Federation

### ARFV1
- File(s): `crates/arf-bus/src/lib.rs:104-121` (Bus struct), `crates/arf-bus/src/lib.rs:202-213` (`Bus::send`), `crates/arf-bus/src/lib.rs:394-495` (`run_message_loop`).
- Implementation: One `Bus` multiplexes `Send`, `Connect`, `Disconnect`, `HeartbeatAck`, `Shutdown` into a single tokio task. `Bus::send` stamps `from_bus = self.id` for multi-Bus federation, then broadcasts via `tokio::sync::broadcast`. Directed routing uses `msg.to: Vec<NodeId>`; broadcast uses empty `to` plus per-node `MessageFilter`.
- Strengths: CAN-bus "never block sender" via dummy `drain_rx`; `Lagged(n)` backpressure on slow consumers.
- Weaknesses: Process-local; cross-host federation needs a transport adapter.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:353-1025` (`create_deep_agent`), `libs/deepagents/deepagents/middleware/subagents.py:737`, `libs/deepagents/libs/ARCHITECTURE.md`.
- Implementation: Three-layer model — a `create_agent` graph per subagent, a `task` tool for dispatch, a top-level compiled main graph. No broadcast bus; subagents talk only via `ToolMessage` in the parent's `messages` list.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add a high-level `Team.broadcast(msg_type, payload)` helper so users don't see `NodeHandle`.

---

## 2. Heartbeat / Liveness Detection

### ARFV1
- File(s): `crates/arf-bus/src/heartbeat.rs:20-65` (`handle_heartbeat_tick`), `crates/arf-bus/src/lib.rs:489-492`.
- Implementation: A `tokio::time::interval` in `run_message_loop` broadcasts `heartbeat_request`, scans nodes whose `last_ack` is older than `heartbeat_timeout`, removes them, and broadcasts `node_offline`. `NodeHandle`'s forwarding task filters the heartbeat from `recv()` and auto-acks.
- Strengths: App code is invisible to liveness; drop a `NodeHandle` to retire a node.
- Weaknesses: Interval and timeout are bus-wide.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`.
- Implementation: No in-process heartbeat. Async subagent status (`'running' | 'success' | 'error' | 'cancelled'`) comes from the Agent Protocol server.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Surface `task_id`/`status` on `TaskResult` so sync pool callers can observe long-running tasks.

---

## 3. Inbound Dedup (LRU)

### ARFV1
- File(s): `crates/arf-engine/src/dedup.rs:14-99` (`InboundDedupCache`), `crates/arf-engine/src/engine.rs:62-66`, `crates/arf-engine/src/config.rs:30-33`.
- Implementation: `LruCache<Uuid, ()>` of inbound reply `correlation_id`s, default capacity 1024, configurable via `EngineConfig::inbound_dedup_capacity`. `check_and_record(cid)` returns `true` on the second sighting.
- Strengths: `Arc<Mutex<…>>` is `Clone`; tests at `dedup.rs:69-80` prove LRU eviction.
- Weaknesses: Process-local; cross-restart dedup is application responsibility (`engine.rs:65-66`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:658-669`, `libs/deepagents/deepagents/middleware/subagents.py:603-641`.
- Implementation: Implicit via LangGraph's reducer on `messages` and unique `tool_call_id`. No LRU — relies on the user's checkpointer.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: None; explicit cache is a feature, not a gap.

---

## 4. Persistent Outbound + Resend on Restart

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1376-1422` (`resend_pending_outbound`).
- Implementation: On restart the engine reads `pending_outbound` from the session store, reconstructs each `Message`, re-sends via `handle.send_message`, and persists `Event::OutboundSent { attempt + 1 }`. Receivers dedup via the LRU above.
- Strengths: Bounded retry counter; failure logs and continues (best-effort).
- Weaknesses: No exponential backoff, no DLQ. Linear `attempt` only.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:73-87`.
- Implementation: For async subagents, `task_id == thread_id` is the durable handle; the Agent Protocol server owns the retry policy.

### Gap Analysis
- Parity: ✅ (ARFV1 ahead on sync durability) · Severity: 🟠 Important · Recommendation: Add exponential backoff + DLQ to `pending_outbound`.

---

## 5. Barrier Protocol (Cross-Node Coordination)

### ARFV1
- File(s): `crates/arf-bus/src/lib.rs:275-361` (`Bus::barrier`).
- Implementation: Best-effort two-phase. Initiator generates `correlation_id`, broadcasts `barrier_request` with `participants`, collects `barrier_ack` carrying the same `cid` until all expected ack or `timeout` elapses. Returns `BarrierReceipt { acked, missing, timed_out }`.
- Strengths: Subscribe-before-broadcast (`lib.rs:296-298`) closes the ack-before-listener race; `cid_match` ignores stray acks.
- Weaknesses: No built-in retry; no quorum semantics (`N of M`).

### DeepAgents
- File(s): `libs/deepagents/libs/ARCHITECTURE.md`.
- Implementation: No barrier primitive. Cross-node sync is graph-based — a parent node awaits `Command`s from each subagent via reducer.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add `BarrierOptions { quorum: Option<NonZeroUsize> }` for "N of M".

---

## 6. Node Failure Handler (Fail / Retry / SwitchTo)

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:52-74`.
- Implementation: `MemberFailedAction` is a sum type: `FailSession`, `Retry { delay_ms }`, `SwitchTo { alternative: NodeId }`. Trait `OnMemberFailedHandler::handle(agent, member, reason)` returns one. Closures auto-implement.
- Strengths: Pure data, easy to test; `SwitchTo` enables hot failover.
- Weaknesses: `Retry` has no `max_attempts` — apps must layer a counter.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`.
- Implementation: Recovery is operator-driven on the Agent Protocol server; no declarative policy.

### Gap Analysis
- Parity: ✅ · Severity: 🟠 Important · Recommendation: Add `max_attempts: u32` to `Retry`.

---

## 7. Team Abstraction (Team / TeamBuilder / TeamConfig)

### ARFV1
- File(s): `py-arf/src/lib.rs:1926-1936`, `py-arf/src/team/team_config.rs`, `py-arf/src/team/team_builder.rs`.
- Implementation: Three-tier Python surface — `PyEngineSpec`/`PyPoolSpec` declare members, `PyTeamConfig` aggregates them, `PyTeamBuilder` materializes YAML+Bus and returns `PyTeam`. `Team.engine(id)` and `Team.subagent_pool(id)` return handles.
- Strengths: YAML-driven; mirrors `EngineBuilder` ergonomics.
- Weaknesses: No Rust-native equivalent in `crates/` — Rust apps assemble by hand.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:36-244`.
- Implementation: Flat `subagents=[…]` list on `create_deep_agent`. No builder, no YAML, no resource spec.

### Gap Analysis
- Parity: ✅ (ARFV1 ahead) · Severity: 🟡 Useful · Recommendation: Expose `TeamBuilder` in `crates/arf-team` for non-Python apps.

---

## 8. Subagent Pool as Bus Actor

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:76-128`, `crates/arf-subagent-pool/src/lib.rs:284-347` (`connect_to_bus`), `crates/arf-subagent-pool/src/lib.rs:360-378` (`handle_delegate_msg`).
- Implementation: Pool registers as `subagent-pool/<pool_id>`, advertises `pool_id`/`size`/`msg_types` in capabilities, and spawns a listener that consumes `subagent_delegate`, invokes `self.delegate(TaskInput)`, and replies with `subagent_result` keyed by `correlation_id`. Accepts both `{task}` and `{user_message}` payload shapes.
- Strengths: Bus-actor pattern is `Send`-safe (avoids the PyO3 `spawn_local` panic, see comment `lib.rs:271-283`). Per-slot UUID-suffixed `agent_id` prevents `engine/<provider>` collisions (`lib.rs:425-427`).
- Weaknesses: `OutboxStrategy::HandoffOutbox` is a logging placeholder (`lib.rs:464-465`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`, `libs/deepagents/examples/async-subagent-server/`.
- Implementation: Async subagent *is* the bus actor (remote Agent Protocol server). No pool concept — each `AsyncSubAgent` is one named agent on one server.

### Gap Analysis
- Parity: ✅ (ARFV1 ahead) · Severity: 🟡 Useful · Recommendation: Implement `HandoffOutbox` end-to-end.

---

## 9. Subagent Invocation Patterns

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:133-211` (`delegate`), `crates/arf-engine/src/builder.rs`, `crates/arf-subagent-pool/src/lib.rs:284-347`.
- Implementation: Three surfaces — (a) `pool.delegate(TaskInput)` direct call, (b) `pool.connect_to_bus(pool_id)` then send `subagent_delegate` on the bus, (c) `EngineBuilder::ephemeral(true).build()` for one-shots. Semaphore bounds concurrency; `populate()` warms idle queue.
- Strengths: Pattern (b) is the recommended production path (Send-safe). State preserved across recycle (`lib.rs:150-157`); `reset_state` only on error.
- Weaknesses: Pattern (a) breaks under PyO3 `local_future_into_py` (see comment `lib.rs:14-23`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:36-244`, `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`, `libs/deepagents/examples/deploy-gtm-agent/`.
- Implementation: Four patterns — `SubAgent` (declarative), `CompiledSubAgent` (caller-supplied Runnable), `AsyncSubAgent` (remote server), `AsyncSubAgentMiddleware` (in-process polling). Single `task` tool dispatches all four.
- Strengths: Uniform dispatch via one tool.
- Weaknesses: No concurrency cap; 50 `task` calls → 50 parallel subagents.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Add `delegate_many(inputs: Vec<TaskInput>)` respecting the semaphore.

---

## 10. Subagent State Isolation

### ARFV1
- File(s): `crates/arf-subagent-pool/src/lib.rs:65-69` (`PoolSlot`), `crates/arf-subagent-pool/src/lib.rs:417-438` (`build_slot`).
- Implementation: Each slot is `(Engine, State)` that recycles as a unit. `build_slot` constructs a fresh `Engine::new(..., ephemeral=true)` with a unique `agent_id`. Conversation history persists in the slot.
- Strengths: Hard isolation — subagents can't see parent's session store unless explicitly granted. UUID suffix avoids NodeId collisions.
- Weaknesses: No declarative "share X, hide Y" — apps hand-craft which fields inherit.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:658-669`, `libs/deepagents/deepagents/middleware/subagents.py:252-269`.
- Implementation: `_validate_and_prepare_state` strips both `_EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response"}` and `private_state_keys` (declared by middleware). `_return_command_with_state_update` strips them again on the way back.
- Strengths: Two-sided filter; declarative via `private_state_keys`.
- Weaknesses: Only top-level dict keys; nested sensitive data is forwarded verbatim.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add `engine.private_state_keys: Vec<String>` to `AgentConfig`.

---

## 11. Subagent Result Extraction

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:383-388`, `crates/arf-subagent-pool/src/lib.rs:160`.
- Implementation: `run_once` returns the final `String` from `Engine::run`. Pool wraps as `TaskResult { output, turns_consumed, pending_peer_messages }`. The engine terminates on `tool_calls.is_empty()` with the terminal assistant text.
- Strengths: Single-source — the terminal `AIMessage` *is* the result.
- Weaknesses: No `response_format` schema.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:603-641`, `libs/deepagents/deepagents/middleware/subagents.py:127-164`.
- Implementation: When `structured_response` is set, JSON-serialize it as `ToolMessage`. Otherwise walk `result["messages"]` in reverse to find the last `AIMessage` with non-empty text — explicit comment notes "Anthropic occasionally emits a trailing empty `end_turn`" (`subagents.py:624-627`).
- Strengths: Handles trailing-empty correctly. `response_format` supports `ToolStrategy`/`ProviderStrategy`/`AutoStrategy`/bare type/JSON schema.
- Weaknesses: Walk-back only inspects top-level `AIMessage`s.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Add `response_format: Option<JsonSchema>` to `TaskInput`.

---

## 12. Route System (Strict / Discovery)

### ARFV1
- File(s): `crates/arf-core/src/route.rs:14-49`, `crates/arf-engine/src/engine.rs:520-523`.
- Implementation: `Route::Strict(Vec<NodeId>)` is point-to-point; `Route::Discovery(Capability)` matches nodes whose `capabilities` JSON contains all required key/value pairs (AND). Top-level strings only (`route.rs:11-12`).
- Strengths: Discovery resolved via `DiscoveryCache` (`engine.rs:49`); matching is O(requirements).
- Weaknesses: No wildcard/NOT/OR — AND on string equality only.

### DeepAgents
- File(s): `libs/deepagents/libs/ARCHITECTURE.md`, `libs/deepagents/deepagents/middleware/subagents.py:36-244`.
- Implementation: No route primitive. `task` tool dispatches to the named subagent.

### Gap Analysis
- Parity: ✅ (ARFV1 ahead) · Severity: 🟡 Useful · Recommendation: Document Strict vs Discovery trade-off in `docs/api/teams.md`.

---

## 13. Resource Resolution (ResourceSpec → NodeIds)

### ARFV1
- File(s): `crates/arf-agent/src/config.rs:95-113`, `crates/arf-engine/src/registry.rs`, `crates/arf-engine/src/engine.rs:54-56`.
- Implementation: Build-time snapshot. `ResourceRegistry` resolves `ResourceSpec { node_type, selector }` into concrete `NodeId`s at engine build; runtime reads the snapshot. `node_type="mcp"` extracts tools/skills; `node_type="mcp/pool"` becomes a `NodePool` (sub-bus).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:36-244`.
- Implementation: Resources compiled into subagent graph at `create_deep_agent` time.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: None — both share the build-time tradeoff.

---

## 14. Multi-Team Membership (TeamMembership)

### ARFV1
- File(s): `py-arf/src/lib.rs:1920`, `py-arf/src/relay/team_membership.rs:1-60`.
- Implementation: `PyTeamMembership` reads `persistent_engines[].id` and `subagent_pools[].id` from `team.yaml` and exposes the union to `PySseRelay`. The `bus` parameter is reserved (`team_membership.rs:9-17`) — the dynamic `node_online` merge is a documented follow-up.
- Strengths: YAML-driven; honest about what's static vs dynamic.
- Weaknesses: `members()` returns static set today; apps must subscribe to `node_online` themselves.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:73-87`.
- Implementation: Multiple `AsyncSubAgent` entries with distinct `name`s. No aggregate "membership" view.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Wire `PyTeamMembership::members()` to call `bus.connected_node_ids()`.

---

## 15. Checkpoint Rules as Team-Level Policy

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:504-545`, `crates/arf-engine/src/config.rs:18-19`.
- Implementation: `Vec<CheckpointRule>` fires on `BeforeModelCall`/`AfterModelCall`/`BeforeToolExec`/`AfterToolExec`/`RoundEnd`. Each rule's `MessageIntent` (Query vs Command) drives `publish_and_await_query` vs `publish_only_command`. `WaitStrategy::{All, Any, Count(n)}` controls the await.
- Strengths: Declarative per-agent; rules can target Discovery routes so they fire across the team.
- Weaknesses: Per-agent — N engines means N copies of the same rule.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:737-810`, `libs/deepagents/deepagents/middleware/rubric.py`.
- Implementation: Middleware is the cross-cutting surface. `SubAgentMiddleware` injects `task` globally; `RubricMiddleware` runs at compile time. No per-step checkpoint rule.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Introduce `TeamConfig::shared_checkpoint_rules` for team-wide rules with per-engine overrides.

---

## Summary Table

| # | Capability | ARFV1 | DeepAgents | Parity |
|---|-----------|-------|------------|--------|
| 1 | Cross-engine bus | Bus + broadcast + filter | Three-layer graph + `task` | ⚠️ |
| 2 | Heartbeat | Bus timer + auto-ack | Agent Protocol status | ✅ |
| 3 | Inbound dedup (LRU) | Explicit `InboundDedupCache` | LangGraph reducer | ✅ |
| 4 | Persistent outbound + resend | `resend_pending_outbound` | Agent Protocol retry | ✅ |
| 5 | Barrier protocol | `Bus::barrier` (cid, best-effort) | None | ⚠️ |
| 6 | Node failure handler | `MemberFailedAction` enum | Server-owned | ✅ |
| 7 | Team abstraction | `PyTeam` + YAML | Flat `subagents=[…]` | ✅ |
| 8 | Subagent pool as bus actor | `connect_to_bus` | Agent Protocol server | ✅ |
| 9 | Invocation patterns | Direct / Bus / Ephemeral | Sub / Compiled / Async / AsyncSub | ✅ |
| 10 | State isolation | `(Engine, State)` pair | `private_state_keys` filter | ⚠️ |
| 11 | Result extraction | `run_once` terminal text | Walk-back + `response_format` | ⚠️ |
| 12 | Route system | Strict / Discovery | Name-based dispatch | ✅ |
| 13 | Resource resolution | Build-time `ResourceRegistry` | Compile-time graph build | ✅ |
| 14 | Multi-team membership | `PyTeamMembership` (YAML + bus) | AsyncSubAgent status | ⚠️ |
| 15 | Checkpoint rules | `CheckpointRule` + WaitStrategy | Middleware | ✅ |

## Top Five Fixes (by ROI)

1. `Bus::barrier` quorum — add `BarrierOptions { quorum }` for "N of M".
2. `MemberFailedAction::Retry { max_attempts }` — close the infinite-retry hole.
3. `engine.private_state_keys` — two-sided filter for ephemeral / subagent engines.
4. `TeamMembership` live merge — call `bus.connected_node_ids()` to drop the static/dynamic split.
5. `pending_outbound` backoff + DLQ — move from best-effort linear retry to bounded exponential.