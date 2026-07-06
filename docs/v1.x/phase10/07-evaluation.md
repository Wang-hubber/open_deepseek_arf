# Phase 10 / 07 — Evaluation: ARFV1 vs DeepAgents

> Atomic-level comparison covering LLM-as-judge harness, behavioral evals, benchmark integration, RubricMiddleware, unit evals, and performance benchmarks. Every claim cites `path:line`.

---

## 1. Eval Catalog (cross-cutting)

### ARFV1
- File(s): `crates/arf-e2e/tests/*.rs` (~67 files; e.g. `react_loop.rs`, `multi_agent_peer_and_subagent.rs`, `checkpoint_rules.rs`), `py-arf/tests/*.py` (15 files; e.g. `test_boundary.py`, `test_lifecycle.py`, `test_concurrency.py`), `tests/e2e/test_codecompass_fs.py`.
- Implementation: No explicit eval catalog. Tests are organized per surface (Bus, Node, MCP, Engine, Session, Multi-Agent), each file groups cases by angle tag — `[构造][方法][边界][序列化][并发][trait]` — declared in module docstrings (`py-arf/tests/test_boundary.py:1-4`, `crates/arf-e2e/tests/react_loop.rs:1-6`). The convention is enforced informally by code review per CLAUDE.md "V1.x workflow / Boundary-first testing".
- Strengths: Coverage is dense (~80+ Rust files + 15 Python files); each test declares its angle up-front.
- Weaknesses: No top-level catalog enumerating what is tested vs untested; gaps are visible only by reading every file.

### DeepAgents
- File(s): `libs/deepagents/libs/EVAL_CATALOG.md` (129 evals across 8 categories).
- Implementation: A single Markdown index lists 129 named behavioral evals grouped into 8 categories (`todos`, `memory`, `memory_multiturn`, `skills`, `subagents`, `summarization`, `system_prompt`, `tool_selection`, `tool_usage_relational`, `tool_usage_incident_graph`, `iterative_constraint_satisfaction`, `followup_quality`, `file_operations`, `langchain_middleware_todo`). Tests live at `libs/deepagents/tests/evals/test_*.py`.
- Strengths: Public, browsable, cite-by-name. New evals must be added to the catalog.
- Weaknesses: Catalog drifts if contributors forget to update it; manual maintenance burden.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important · Recommendation: Generate `docs/v1.x/eval-catalog.md` from test-file docstrings (each `[angle]` tag becomes a category) and check it in. Low cost, high visibility for what ARFV1 actually exercises.

---

## 2. LLM-as-Judge Harness

### ARFV1
- File(s): None. `py-arf/tests/` and `crates/arf-e2e/tests/` contain only deterministic assertions.
- Implementation: No LLM-as-judge exists. Tests assert Python `bool`/`str`/`dict` results directly (`py-arf/tests/test_boundary.py:24-35`). Live provider testing exists (`py-arf/tests/test_model_adapter_live.py`) but uses env-var gated real APIs to compare JSON shape, not graded semantics.
- Strengths: Reproducible, no flake from model variance.
- Weaknesses: Cannot evaluate "is this answer helpful / on-brand / safe" — only "does it equal X".

### DeepAgents
- File(s): `libs/deepagents/tests/evals/llm_judge.py`.
- Implementation: A reusable `LLMJudge` class scores agent outputs against natural-language rubrics. Evals like `test_followup_quality.py` instantiate the judge with a `rubric` string and a `model`, run the agent, and pass when score ≥ threshold. The judge is model-agnostic (uses LangChain `BaseChatModel`).
- Strengths: Decouples judge from SUT; rubric in plain English; multi-judge ensembles trivial.
- Weaknesses: Judge quality bounds SUT quality ceiling; cost per eval is non-trivial.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🔴 Critical · Recommendation: Add `py-arf/evals/llm_judge.py` mirroring the class, plus one rubric per Phase 9 L-level capability (chat, tool_use, subagent_delegate). Gate behind `ARF_EVAL_LIVE=1` env-var to keep `make test-py` deterministic.

---

## 3. Behavioral Evals per Feature

### ARFV1
- File(s): `py-arf/tests/test_team.py`, `py-arf/tests/test_team_membership.py`, `py-arf/tests/test_relay.py`, `py-arf/tests/test_reconnect.py`, `py-arf/tests/test_resource_leak.py`, `py-arf/tests/test_shutdown.py`, `py-arf/tests/test_mcp.py`, `examples/multi_agent_team/tests/test_subagent_delegate.py`, `examples/multi_agent_team/tests/test_agent_teams_coord.py`.
- Implementation: One pytest module per feature, deterministic asserts on state (e.g. `test_team_membership.py` checks roster transitions). No behavioral rubric; cases are "given X input, assert Y state change" with no LLM in the loop.
- Strengths: Fast (sub-second per file), CI-friendly, no flakiness.
- Weaknesses: Doesn't cover behaviors that emerge from LLM choices (e.g. "does the agent pick the right tool under ambiguity").

### DeepAgents
- File(s): `libs/deepagents/tests/evals/test_todos.py`, `test_memory.py`, `test_skills.py`, `test_subagents.py`, `test_summarization.py`, `test_tool_selection.py`, `test_tool_usage_relational.py`, `test_tool_usage_incident_graph.py`, `test_file_operations.py`, `test_followup_quality.py`.
- Implementation: Each file is a self-contained behavioral eval — sets up a `create_deep_agent` with specific middleware, sends a prompt, asserts on message history, file contents, todo list state, or LLM-judge score.
- Strengths: One eval = one user-visible behavior; regression source is obvious from filename.
- Weaknesses: Setup duplication across files; no shared fixture beyond `conftest.py`.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Reorganize `py-arf/tests/` so each file is named `test_<behavior>_<angle>.py` (e.g. `test_subagent_delegation_method.py`) — matches DeepAgents' discoverability without rewriting tests.

---

## 4. External Benchmark Integration (Tau Bench τ², Terminal Bench 2.0 via Harbor)

### ARFV1
- File(s): None.
- Implementation: ARFV1 ships no integration with external benchmarks. The closest analog is `crates/arf-e2e/tests/react_loop.rs:1-79` which exercises the ReAct loop end-to-end but against synthetic `ScriptedProvider` mocks (see `crates/arf-e2e/tests/common/provider.rs`), not against Tau Bench customer-service scenarios.
- Strengths: Tightly scoped; tests run without external dataset download.
- Weaknesses: No industry-standard leaderboard signal; impossible to compare ARFV1 vs LangGraph/LlamaIndex objectively.

### DeepAgents
- File(s): `libs/deepagents/tests/evals/tau2_airline/` (Tau Bench τ² airline), `libs/deepagents/deepagents_harbor/` (Harbor / Laude Institute Terminal Bench 2.0), `libs/deepagents/deepagents_clbench/` (clbench sync).
- Implementation: `tau2_airline/` adapts the Tau Bench customer-service dataset as DeepAgents runs; `deepagents_harbor/` is a separate Python package that registers a Harbor `Task` adapter so Terminal Bench 2.0 orchestrator can run DeepAgents as a solution. `deepagents_clbench/` syncs code-LLM-bench results.
- Strengths: Three independent external signals; community comparison possible.
- Weaknesses: Each benchmark pins a snapshot; upstream dataset churn requires manual sync.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important · Recommendation: Add `py-arf/evals/tau2_airline/` first (cheapest, dataset is CC-BY) before Harbor. Harbor integration needs a containerized runner, larger effort.

---

## 5. Memory Agent Bench (multi-turn external benchmark)

### ARFV1
- File(s): None.
- Implementation: Memory persistence is tested at the protocol level (`py-arf/tests/test_lifecycle.py`, `crates/arf-e2e/tests/session_persist.rs`) but no standardized multi-turn benchmark exercises long-horizon recall, contradiction handling, or fact decay.
- Strengths: Unit-level invariants are tight.
- Weaknesses: No way to claim "ARF remembers across 100 turns".

### DeepAgents
- File(s): `libs/deepagents/tests/evals/memory_agent_bench/`.
- Implementation: Multi-turn conversation dataset (sub-folder under `tests/evals/`) replayed against `create_deep_agent` with the `MemoryMiddleware`. After each turn, the harness asserts whether the agent recalled facts, refused contradictions, and retrieved prior entities.
- Strengths: Standardized; comparable across implementations.
- Weaknesses: Dataset license / versioning must be tracked.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Defer until Phase 9 L6 (session_persist) audit is green; then port `memory_agent_bench/` with ARFV1's `SessionStore` as the backing store.

---

## 6. RubricMiddleware (self-evaluation loop with grader sub-agent)

### ARFV1
- File(s): None.
- Implementation: No middleware equivalent. ARFV1's engine (`crates/arf-engine/src/engine.rs`) runs the ReAct loop with `InboundDedupCache` and `CheckpointRule` factories, but no grader sub-agent is spawned mid-loop.
- Strengths: Predictable latency — no surprise LLM call before final answer.
- Weaknesses: No built-in self-critique; agent cannot revise its own output before returning to the user.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/rubric.py:805` (`RubricMiddleware` class), `libs/deepagents/libs/CHANGELOG.md:0.6.5` (added).
- Implementation: `RubricMiddleware` wraps the agent's invoke; after the final AI message it spawns a grader sub-agent with a configurable rubric, collects a `GraderResponse`, merges into `RubricResult`, and (optionally) loops back for a second pass if score < threshold.
- Strengths: Self-improvement without app code; rubric in plain English; bounded iteration prevents infinite loops.
- Weaknesses: Doubles token cost; grader prompt must be tuned to avoid over-rejection.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important · Recommendation: Implement `GraderSubAgent` as a `RuntimeModule` so existing `Engine` loops can opt in via `EngineConfig::grader_rubric: Option<String>`. Reuses sub-agent pool (Phase 9 Issue 1 wiring).

---

## 7. GraderResponse / RubricResult Types

### ARFV1
- File(s): None.
- Implementation: No equivalent types. The closest is `EngineResponse` in `crates/arf-engine/src/engine.rs` (struct with `text`, `tool_calls`, `usage`); no `score` / `feedback` / `pass: bool` fields.
- Strengths: Minimal API surface; nothing to misuse.
- Weaknesses: Apps that want self-eval must roll their own dataclass.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/rubric.py` (`GraderResponse`, `RubricResult` Pydantic models).
- Implementation: `GraderResponse` = `{score: float, feedback: str, pass_: bool}`; `RubricResult` aggregates per-iteration `GraderResponse`s with `final_score`, `iterations`, `passed`. Typed via Pydantic so LangChain tool-binding validates rubric payloads automatically.
- Strengths: Strong typing; serialization trivial.
- Weaknesses: Pydantic v2 coupling leaks into framework API.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Define `GraderResponse` / `RubricResult` in Rust (`crates/arf-engine/src/grader.rs`) once `GraderSubAgent` lands, mirror via PyO3 — keeps the API language-agnostic.

---

## 8. Pytest Reporter (CI integration)

### ARFV1
- File(s): `Makefile:7-9` (`test-py: pytest tests/ -q`), `py-arf/tests/conftest.py`.
- Implementation: Standard `pytest -q` invocation. Output is plain pytest text. CI must parse `--tb=short` and grep for `FAILED`. No structured JUnit XML, no per-eval result store.
- Strengths: Zero config; works in any CI.
- Weaknesses: No machine-readable per-eval artifact; trends not captured.

### DeepAgents
- File(s): `libs/deepagents/tests/evals/pytest_reporter.py`.
- Implementation: A custom `pytest` plugin that, on session finish, writes a JSON report indexed by eval name (filename → pass/fail/score). Designed to be consumed by eval-driven tooling like `examples/better-harness/`.
- Strengths: Eval history is queryable; regression detection across runs is trivial.
- Weaknesses: One more plugin to maintain.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Add `py-arf/tests/pytest_reporter.py` that emits `eval-report.json`; wire `make test-py` to invoke it. Cheap, unlocks historical trend tracking.

---

## 9. Test Infrastructure (conftest, fixtures, data)

### ARFV1
- File(s): `tests/conftest.py` (cross-language, empty by design), `py-arf/tests/conftest.py:6-10` (`bus` fixture), `crates/arf-e2e/tests/common/mod.rs:1-12` (`mod env; mod harness; mod provider;`), `crates/arf-e2e/tests/common/harness.rs` (`E2EHarness`, `E2EHarnessBuilder`).
- Implementation: Shared `E2EHarness` unifies bus + nodes + engine setup; `provider.rs` exposes `simple_mock`, `text_response`, `tool_call_response` so tests don't repeat model stubbing. `env.rs` provides env-var skip-if-missing pattern for live API tests.
- Strengths: Very low fixture noise; tests read top-down.
- Weaknesses: No `data/` folder for shared eval inputs (tests inline their fixtures).

### DeepAgents
- File(s): `libs/deepagents/tests/evals/conftest.py`, plus per-eval `data/` subfolders.
- Implementation: `conftest.py` exposes `model` (judge model), `agent` (factory), `workspace` (tmp dir). Per-eval `data/` folders hold golden transcripts and rubric prompts so evals are deterministic-given-input.
- Strengths: Eval inputs version-controlled; reviewers can diff a rubric change against goldens.
- Weaknesses: `data/` folders grow unbounded.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Add `crates/arf-e2e/tests/fixtures/` (Rust) and `py-arf/tests/data/` (Python) so future evals can reference shared transcripts instead of inlining JSON.

---

## 10. Unit Evals vs Behavioral Evals

### ARFV1
- File(s): `crates/arf-engine/tests/integration.rs` (inline mock responder pattern, unit-flavor), `crates/arf-e2e/tests/react_loop.rs:1-79` (full stack).
- Implementation: Two tiers — unit (`crates/*/tests/`) and e2e (`crates/arf-e2e/tests/`). The boundary is "does it need a real Bus + Node + Engine?". Unit tests use inline stubs; e2e uses `E2EHarness`.
- Strengths: Clear split; unit tests run in <1s.
- Weaknesses: No `pytest` equivalent of `pytest.mark.behavioral`; can't filter "I only want behavioral tests" in CI.

### DeepAgents
- File(s): `libs/deepagents/tests/evals/test_langchain_middleware_todo.py` (unit eval of a single middleware), plus the 129 behavioral evals.
- Implementation: Both styles co-exist in `tests/evals/`. `test_langchain_middleware_todo.py` is unit-flavor (instantiates middleware directly, asserts state). Behavioral evals instantiate the full agent. Filename conveys style.
- Strengths: Single root; filename convention = documentation.
- Weaknesses: No pytest marker — `pytest -m behavioral` not possible without marker convention.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Add `pytest.mark.behavioral` to behavioral-style tests in `py-arf/tests/`; update `Makefile` `test-py` target with `-m "not live"` default.

---

## 11. Constraint Satisfaction Evals

### ARFV1
- File(s): None.
- Implementation: No constraint-satisfaction eval. ARFV1 has `tool_permission` (Allow/Ask/Deny) at `crates/arf-e2e/tests/tool_permission_ask.rs`, `tool_permission_deny.rs`, but those test the gating mechanism, not "agent satisfied N user-stated constraints across K turns".
- Strengths: Permission model is testable in isolation.
- Weaknesses: No eval proves the agent respects user-stated rules (e.g. "never call tool X after turn 3").

### DeepAgents
- File(s): `libs/deepagents/tests/evals/test_iterative_constraint_satisfaction.py`.
- Implementation: Multi-turn scenario where user adds constraints incrementally; final assertion checks all constraints hold. Uses LLM judge for subjective constraints.
- Strengths: Captures real failure mode (agents drop earlier constraints).
- Weaknesses: Brittle if judge is weak.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Land this in Phase 11 once RubricMiddleware ships; without it, eval needs bespoke grader.

---

## 12. Tool Selection Evals

### ARFV1
- File(s): `crates/arf-e2e/tests/multi_mcp_dedup.rs`, `crates/arf-e2e/tests/mcp_pool_facade.rs` (deterministic assertions on routing), `py-arf/tests/test_filters.py`.
- Implementation: Tool selection is tested at the routing layer — given multiple MCP nodes, does the message reach the right one? Not at the LLM-decision layer.
- Strengths: Strict-route test coverage is excellent.
- Weaknesses: No eval of "given tool descriptions A,B,C, did the LLM pick A?".

### DeepAgents
- File(s): `libs/deepagents/tests/evals/test_tool_selection.py`, `test_tool_usage_relational.py`, `test_tool_usage_incident_graph.py`.
- Implementation: Each presents the agent with N tools and a task; asserts the right tool name appears in the AI message. Uses LLM judge for relational/incident-graph cases.
- Strengths: Catches description-quality regressions.
- Weaknesses: Requires tools with stable names; renaming breaks evals.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Once LLM-as-judge lands (§2), port `test_tool_selection.py` as `py-arf/tests/behavioral/test_tool_selection_method.py`.

---

## 13. Multi-Turn Memory Evals

### ARFV1
- File(s): `crates/arf-e2e/tests/session_persist.rs`, `session_multi_id.rs`, `session_checkpoint_5pos.rs`, `recovery.rs`.
- Implementation: Multi-session persistence and recovery tested. No "recall fact from turn 17 of 50" eval.
- Strengths: Crash-and-recover is well covered.
- Weaknesses: Memory quality (precision/recall of recalled facts) untested.

### DeepAgents
- File(s): `libs/deepagents/tests/evals/test_memory_multiturn.py` (in addition to `test_memory.py`).
- Implementation: Long conversation seeded with entities; later turns query entities; harness scores recall precision.
- Strengths: Quantifies memory middleware quality.
- Weaknesses: Long-running (many LLM calls).

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: After Memory Agent Bench (§5), this is a cheap add — same scaffolding, simpler dataset.

---

## 14. ARFV1's e2e Testing (full-stack harness)

### ARFV1
- File(s): `crates/arf-e2e/tests/react_loop.rs:1-79`, `crates/arf-e2e/tests/skill_full_progressive.rs`, `crates/arf-e2e/tests/nested_subagent_three_layer.rs`, `crates/arf-e2e/tests/multi_bus_attach.rs`, `tests/e2e/test_codecompass_fs.py:1-60`.
- Implementation: Two e2e suites — Rust (`crates/arf-e2e/tests/`) and Python (`tests/e2e/test_codecompass_fs.py`). Rust suite uses `E2EHarness` + `ScriptedProvider` for determinism; Python suite uses the `CodecompassApp` reference app with `mode="mock"` (`test_codecompass_fs.py:38`).
- Strengths: 67+ Rust e2e files cover the whole L1-L8 capability matrix from `docs/v1.x/phase9/capability-matrix-and-audit-design.md:25-82`; Python e2e exercises the codecompass-fs reference app end-to-end.
- Weaknesses: No LLM judge; no external benchmark.

### DeepAgents
- File(s): `libs/deepagents/tests/evals/*.py` (behavioral), `libs/deepagents/tests/` (LangChain integration tests).
- Implementation: Behavioral evals are the e2e; each spins up a real `create_deep_agent` and asserts on outputs.
- Strengths: Same coverage axis (behavior), different angle (LLM-driven).
- Weaknesses: Slow when many evals run sequentially.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Keep current e2e as the deterministic backbone; add behavioral layer on top.

---

## 15. ARFV1's Boundary-First Testing Convention

### ARFV1
- File(s): `CLAUDE.md` "V1.x 逐任务开发工作流 / Boundary-first testing" line, `py-arf/tests/test_boundary.py:1-4`, `crates/arf-e2e/tests/react_loop.rs:1-6`.
- Implementation: Every test file declares its angle tags in the module docstring (`[构造][方法][边界][序列化][并发]`). CLAUDE.md mandates this convention as part of the V1.x task workflow.
- Strengths: Test intent is grep-able; reviewer can spot missing angles at a glance.
- Weaknesses: Manual discipline; easy to drift.

### DeepAgents
- File(s): None — DeepAgents has no equivalent tagging convention.
- Implementation: Filename + docstring only.
- Strengths: Lower friction to write tests.
- Weaknesses: Less searchable; "is boundary X covered?" requires reading tests.

### Gap Analysis
- Parity: ✅ (ARFV1 leads) · Severity: 🟡 Useful · Recommendation: Adopt this convention for any behavioral evals added under §3; codify in a `py-arf/tests/CONTRIBUTING.md`.

---

## 16. ARFV1's make test Infrastructure

### ARFV1
- File(s): `Makefile:1-21`, `Makefile:9-11` (`test`, `test-rust`, `test-py`).
- Implementation: Single entrypoint: `make test` runs `test-rust` then `test-py`. `test-rust` invokes `. "$HOME/.cargo/env" && cargo test --workspace`; `test-py` invokes `pytest tests/ -q`. `lint` target wraps `cargo fmt --check` + `cargo clippy`.
- Strengths: One command; no Makefile sprawl.
- Weaknesses: No separate `make eval` target; no `make test-live` for gated env-var tests.

### DeepAgents
- File(s): `pyproject.toml` `[tool.pytest.ini_options]`, no top-level Makefile — uses `uv run pytest tests/evals`.
- Implementation: pyproject-driven; no Makefile abstraction.
- Strengths: Modern; lockfile-driven reproducibility.
- Weaknesses: Harder to remember the exact command.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Add `make test-live` (gated) and `make eval` (behavioral subset) targets once §2 lands.

---

## 17. Eval Coverage Gap (summary)

### ARFV1
- File(s): entire eval axis.
- Implementation: ARFV1 has strong deterministic coverage (`crates/arf-e2e/tests/`, `py-arf/tests/`) but **zero** LLM-as-judge, zero external benchmark integration, zero RubricMiddleware, zero pytest_reporter, zero behavioral evals. The `examples/multi_agent_team/tests/` directory (`test_basic_flow.py`, `test_agent_teams_coord.py`, `test_subagent_delegate.py`, `test_hitl_approval.py`) is app-level smoke testing, not framework evals.
- Strengths: Strong deterministic backbone.
- Weaknesses: Cannot make quality claims (only structural claims).

### DeepAgents
- File(s): entire eval axis.
- Implementation: 129 behavioral evals + 3 external benchmarks + RubricMiddleware + pytest reporter.
- Strengths: Full quality-axis coverage.
- Weaknesses: Heavier maintenance; flake risk from judge variance.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🔴 Critical · Recommendation: Phase 11 should ship a thin eval layer (§2 LLM-as-judge, §8 reporter, §3 reorganization, §6 RubricMiddleware). External benchmarks (§4, §5) can wait for Phase 12.

---

## 18. Eval-Driven Profile Tuning (better-harness)

### ARFV1
- File(s): None.
- Implementation: No equivalent. Profiles are static config in `crates/arf-model-adapter/src/`; no loop tunes them against eval results.
- Strengths: Predictable; no surprise behavior shift.
- Weaknesses: Cannot auto-improve model/prompt choices against eval signal.

### DeepAgents
- File(s): `libs/deepagents/examples/better-harness/`.
- Implementation: Autonomous harness that loops: run eval → parse `pytest_reporter.py` JSON → mutate model profile (temperature, prompts, tool descriptions) → re-run eval → keep mutation if score improved. Stops after N iterations or plateau.
- Strengths: Self-improving; uses the eval infrastructure built in §8.
- Weaknesses: Risk of overfitting to eval suite; must guard with held-out set.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Long-term; depends on §2, §8 landing first. Out of scope for Phase 11.

---

## 19. Performance Benchmarks

### ARFV1
- File(s): None.
- Implementation: No `tests/benchmarks/` directory. `cargo test --workspace` runs functional tests only. No latency / throughput benchmarks.
- Strengths: None added.
- Weaknesses: Regression in `Bus::send` throughput or `Engine::chat` P99 latency would land undetected.

### DeepAgents
- File(s): `libs/deepagents/tests/benchmarks/`.
- Implementation: pytest-benchmark suites measuring middleware overhead, agent cold-start, and end-to-end invoke latency across model sizes.
- Strengths: Catch perf regressions in CI.
- Weaknesses: Run-on-CPU variance can flake.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Add `crates/arf-bus/benches/` (criterion) for Bus throughput, `crates/arf-engine/benches/` for engine cold-start. Cheap; criterion integrates with cargo.

---

## 20. CI Integration

### ARFV1
- File(s): `Makefile:7-9`, `CLAUDE.md` "常用操作 / 测试" line.
- Implementation: `make test` is the single CI command. `make test-py` is `pytest tests/ -q`; `make test-rust` is `cargo test --workspace`. CI parses stdout exit code only — no JUnit XML, no coverage threshold gate.
- Strengths: Trivial to wire in any CI provider.
- Weaknesses: No structured artifact; trend detection impossible.

### DeepAgents
- File(s): `libs/deepagents/tests/evals/pytest_reporter.py` (writes `eval-report.json`), `libs/deepagents/.github/workflows/` (consumes it).
- Implementation: pytest reporter writes JSON; CI workflow uploads it; trend dashboards compare across PRs.
- Strengths: Machine-readable history.
- Weaknesses: Repo-specific; harder to copy.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Add `pytest --junitxml=eval-report.xml` to `Makefile:9`; gate merge on coverage delta. Bridges gap until §8 ships.