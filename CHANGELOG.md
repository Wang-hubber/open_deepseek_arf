# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Unified async outbox** — `SessionStore::record_event` + `pending_outbound` unify `peer_message` and `HumanHandoff` (and future async collaboration modes) under one event log. `Engine::resend_pending_outbound` replaces `resend_pending_peer_messages` as the single recovery path.
  - `Event` enum in `arf_session::event` with 7 variants (`OutboundSent`, `InboundReply`, `RoundStart`, `RoundEnd`, `ModelCallEnd`, `ToolCallEnd`).
  - `PendingOutbound` struct in `arf_session::event`.
  - `SqliteSessionStore`: `peer_events` table renamed to `events` with new `msg_type` and `source_node` columns; `record_event` and `pending_outbound` implemented with MAX(attempt) + NOT EXISTS derivation.
  - `JsonlSessionStore`: snapshot line now includes `data: State` payload (was previously missing — known gap fixed).
- **`Engine::handoff_to_human`** — public API for sending a `human_handoff` message to the UI node and awaiting its reply. Records the outbound event via the unified outbox before sending.
- **`InboundDedupCache`** — process-level LRU dedup cache for inbound reply correlation IDs. Absorbs self-resends (sender == receiver) without a SQL query. Capacity configurable via `EngineConfig::inbound_dedup_capacity` (default 1024). **Process-level only — cross-restart dedup is application responsibility** (documented in the type's rustdoc and in `docs/superpowers/specs/2026-07-06-unified-async-outbox-design.md` §4.4).
- **`EngineConfig::inbound_dedup_capacity`** — LRU capacity for `InboundDedupCache` (default 1024).
- **`Message::dispatch_incoming`** retains its sync signature; new `Engine::maybe_record_inbound_reply` (Task 19) is wired into `wait_for_strategy` to perform dedup + record `InboundReply` event before delegating to registered handlers.

### Fixed
- JSONL `snapshot` line gap (was missing `data` payload — `load()` would fall back to `save` line with a `tracing::warn!`); now includes the State at snapshot time.
- **Phase 9 lesion fixes** (see `docs/v1.x/phase9/fix-design.md`):
  - C8/F-003: `ModelAdapterPoolNode` dispatches each `model_call` on its own task (spawn-per-task + correlation_id demux) so concurrent calls use the whole pool in parallel; pool acquire failure now returns `model_response{error}` instead of dropping the request.
  - C9/F-015: `Summarizer::summarize` now takes `CompactionRequest { instruction, messages }` — the raw conversation and the instruction are passed separately (was a single pre-baked `[system, user]` slice with a misleading param name).
  - C5/F-012,F-013,F-014: `SessionStore::save()` now persists `last_checkpoint`; `snapshot()` returns `SnapshotEffects` and documents its 4 side effects; Engine `run()` fails fast (`SessionNotPreSaved`) when a store is configured but the session was never saved, and aborts the round (`SnapshotFailed`) on a failed snapshot instead of silently continuing. Added `SessionStore::exists()`.
  - C7/F-010,F-011: added `McpNode::with_discovery()` to inject a custom `DiscoveryBackend` (+ `discovery()` accessor); `HttpProxyTool` now honours the MCP `isError` flag and surfaces failing tools as errors instead of success.
  - C3/A3-001,A4-001,F-020: new `arf_core::msg_type` constants module (single source of truth for wire message types); `Message::correlation_id()` typed accessor centralises Uuid↔string conversion; `Message::new_broadcast()` constructor for safe broadcast sends (skip online-check + silent NodeOffline) — implementation: `Message` keeps a single `to: Vec<NodeId>` field; broadcast is detected when `to.is_empty()` (no separate `routing` enum).
  - C6/F-005,F-006: `ModelCall` now carries `model_params` (`thinking_enabled` etc.); Engine copies `ModelDecl.thinking_enabled` into the wire payload so it actually reaches the adapter. Capability-matrix spec uses `thinking_enabled` (the framework's actual field name).
  - C4/F-007,F-008: `Bus::graph()` now sorts nodes by `node_id` for deterministic routing across processes; `resolve_model` additionally checks `capabilities.models` so an unsupported `model_name` no longer silently routes to a wrong node.
  - C1/F-002,F-009,F-021: `PoolConfig` gained `min_size`; `Pool::with_provisioner` pre-populates and `acquire()` now auto-provisions up to `max_size` (CRITICAL — was the framework's biggest design deviation). `Overflow::Queue(N)` now respects N: pending acquirers are counted; the (N+1)-th returns `PoolError::Full` immediately (was: blocked forever, N was dead code). `MCPPoolNode` benefits automatically by passing a pool built with `with_provisioner`.
  - C2/F-016,F-018,F-019: `EngineBuilder::with_agent_id` overrides the hardcoded `engine/{provider}` default (multi-engine collisions); the `OnMemberFailedHandler` registered in AgentConfig is now actually invoked on `node_offline` (was silent dead code); `auto_subscribe_message_types(&["peer_message", …])` extends the Engine's primary subscription filter without Strict routes.
  - C2-followup/F-017: `ToolPermission` (Allow/Ask/Deny) is now wired end-to-end — `arf_core::ToolSpec.permission` defaults to Allow (legacy compat), `arf_engine::AgentConfig.tools` carries the declarations, and `Engine::do_tool_turn` gates `tool_exec` on permission: Deny short-circuits with an error `tool_result`; Ask broadcasts `permission_request` and waits for `permission_response` before proceeding.

### Removed
- `docs/architecture/` directory — was V0.x-only material (10-checkpoint Hook lifecycle, plugin system, PrimitiveAgent/AgentHarness, two-mode A2A, Eval benchmark). V1.x redesigned all of these; architecture overview now lives in `README.md` + `docs/dev/`.

## [1.0.0-alpha.0] - 2026-07-01

### Added
- Rust workspace: arf-core, arf-bus, arf-state, arf-model-adapter, arf-mcp, arf-engine, arf-agent, arf-pool, arf-e2e
- Python binding `py-arf` (PyO3 + maturin, zero runtime dependencies)
- Engine API: ReAct loop, Park/Resume, Checkpoint, Pool, Routing
- MCP support: Local + Remote + Script tools
- User API reference (`docs/api/`)
- Developer documentation (`docs/dev/`)
- Architecture documentation (`docs/architecture/`)

### Changed
- **BREAKING**: Complete rewrite from Python framework to Rust + PyO3
- Restructured repository directory layout (`examples/{rust,python}/`)
- Renamed `docs/v1.x/` to `docs/dev/`
- Consolidated `pyproject.toml` at root, built on maturin
- Version bumped to 1.0.0-alpha.0

### Removed
- Legacy Python framework (`arf/` directory, 179 files)
- Legacy Python tests for the old framework
- Legacy dependencies: fastapi, uvicorn, langgraph, websockets, jinja2, python-multipart, openai, pyyaml, watchfiles, httpx, pydantic
- Old benchmark JSON files (`benchmarks/`)