# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Phase 9 lesion fixes** (see `docs/v1.x/phase9/fix-design.md`):
  - C8/F-003: `ModelAdapterPoolNode` dispatches each `model_call` on its own task (spawn-per-task + correlation_id demux) so concurrent calls use the whole pool in parallel; pool acquire failure now returns `model_response{error}` instead of dropping the request.
  - C9/F-015: `Summarizer::summarize` now takes `CompactionRequest { instruction, messages }` — the raw conversation and the instruction are passed separately (was a single pre-baked `[system, user]` slice with a misleading param name).
  - C5/F-012,F-013,F-014: `SessionStore::save()` now persists `last_checkpoint`; `snapshot()` returns `SnapshotEffects` and documents its 4 side effects; Engine `run()` fails fast (`SessionNotPreSaved`) when a store is configured but the session was never saved, and aborts the round (`SnapshotFailed`) on a failed snapshot instead of silently continuing. Added `SessionStore::exists()`.
  - C7/F-010,F-011: added `McpNode::with_discovery()` to inject a custom `DiscoveryBackend` (+ `discovery()` accessor); `HttpProxyTool` now honours the MCP `isError` flag and surfaces failing tools as errors instead of success.
  - C3/A3-001,A4-001,F-020: new `arf_core::msg_type` constants module (single source of truth for wire message types); `Message::correlation_id()` typed accessor centralises Uuid↔string conversion; `Message::new_broadcast()` constructor for safe broadcast sends (skip online-check + silent NodeOffline).
  - C6/F-005,F-006: `ModelCall` now carries `model_params` (`thinking_enabled` etc.); Engine copies `ModelDecl.thinking_enabled` into the wire payload so it actually reaches the adapter. Capability-matrix spec uses `thinking_enabled` (the framework's actual field name).
  - C4/F-007,F-008: `Bus::graph()` now sorts nodes by `node_id` for deterministic routing across processes; `resolve_model` additionally checks `capabilities.models` so an unsupported `model_name` no longer silently routes to a wrong node.

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