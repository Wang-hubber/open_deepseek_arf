<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
</p>

<p align="center">
  <strong>English</strong>
  &nbsp;·&nbsp;
  <a href="./README.zh-CN.md">简体中文</a>
  &nbsp;·&nbsp;
  <a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">Harness = OS Kernel. Model = CPU. Agent = Computer.</h3>
<p align="center">Local-first. Convention over configuration. Fully traceable. Self-evolving.</p>

<br/>

> **Built by DeepSeek V4 Pro & Claude Code.** The author provided design direction and code review only — not a single line was manually written.

<br/>

## Reading Guide

This document is organized in two parts plus a bottom section:

- **Part I — Framework**: Core design philosophy and problem-domain landscape. Each problem name in the table links to a deep-dive design doc (three chapters: OS evolution / current implementation / evolution direction)
- **Part II — Reference App**: How the application layer calls each framework capability, with config examples and design doc links
- **Bottom [TODO](#todo)**: Known issues and evolution directions for contributors

New readers: scan the overview table first for the big picture, then dive into specific docs as needed.

<br/>

---

## Part I — Framework

### Design Philosophy

A model is raw compute — powerful, but not a computer. It needs memory management, process scheduling, interrupt handling, a file system, and security boundaries. ARF provides those. It is an **agent framework** built on a single architectural insight: **the Harness layer is the kernel of AI-native computing**.

The primitives of operating systems — virtual memory, cache hierarchies, system calls, protection rings — map directly onto the problems every agent engineer faces. ARF does not invent new abstractions. It adapts proven OS patterns to the token era.

### Harness as Kernel — 6-Skeleton Architecture

> **Model + Harness = Agent. CPU + Kernel = Computer.**
>
> Token is the instruction. Agent session is the process. Tool call is the system call.

ARF is built on **6 skeletons** — the minimum viable framework. Each skeleton maps to a Protocol. The framework can run an Agent with only these 6; everything else is a **Plugin** mounted on lifecycle Hook points.

*Click any skeleton name for the full design document.*

| # | Skeleton | OS Analogy | Current | Evolution |
|---|----------|------------|---------|-----------|
| 1 | **[Prompt Assembly](docs/prompt-assembly.md)** | Program loader (execve) | `SystemPromptProvider` — prefix (role + critical_rules) + suffix (`$INVENTORY` template). `string.Template` placeholders (`$MEMORY`, `$WORKSPACE`, `$TURN_BUDGET`). Per-turn replacement by engine. | Multi-agent prompt composition; role-based template dispatch |
| 2 | **[Resource Registry (MCP)](docs/resource-registry.md)** | File system + udev + systemd | Convention over configuration: `tool.yaml`+`function.py` per tool, `skills/*.yaml`. Models defined inline in `agent.yaml` (`model_defs`). `FileWatcher` inotify+polling hot reload. `ResourceResolver` override merge. MCP-based unified interface via local MCP Server (stdio JSON-RPC) — aggregates local + external resources. | Hierarchical override merging; MCP multi-source Provider; cross-reference validation |
| 3 | **[Permission Control](docs/tool-sandbox.md)** | ACL + capability bits | `SessionModeManager` (auto/ask/plan) + `PermissionRegistry` deny→ask→allow enforcement. Per-agent `policy` override. `deny_patterns` regex matching. | OAuth-scoped permissions; role-based access control |
| 4 | **[Security Audit](docs/tool-sandbox.md)** | Protection rings (Ring 0-3) | `PathCheckToolGuard` — recursive scan (.., symlink, depth/count quota). `ContentGuard` — pre/post execution + pre-output rule-based screening. `GuardDefaults` three-line defense. | Per-invocation sandbox; content-aware scanning |
| 5 | **[Executor (Sandbox)](docs/tool-sandbox.md)** | Process isolation (chroot/namespace) | `SandboxManager` — per-session isolated workspace, configurable blacklist, auto-destroy. `ConcurrentToolExecutor` parallel execution. `FunctionBackend` with optional `rollback()`. | Container-based sandbox; resource quotas |
| 6 | **[Control Plane](docs/agent-execution.md)** | Process scheduler + signals | `GraphEngine` single `_execute` path. `LoopStrategy` ReAct pattern + TODO tracking. State management (runtime session state). 9 Hook injection points (`session_start`, `round_start`, `pre_model_call`, `post_model_call`, `post_permission`, `pre_tool_exec`, `post_tool_exec`, `sandbox_persist`, `round_end`, `session_end`). | Plan-Execute loop strategy; pause/resume/checkpoint; multi-agent DAG |

### Plugin System

**Plugin ≠ Tool.** Tools are MCP-managed function resources the Agent calls. Plugins are behaviors mounted on Hook points — they fire automatically at framework lifecycle events. The framework runs without plugins; plugins add preset or custom capabilities.

| Plugin | Hook | Status | Description |
|--------|------|--------|-------------|
| **Memory** | `round_end` | DONE | Long-term memory extraction via system model, atomic write to `memory.md` |
| **TODO** | `round_start`, `round_end` | DONE | Task list tracking with reminder injection |
| **UNDO** | `round_end`, `sandbox_persist` | DONE | Round-level state + file rollback |
| ~~**Model Routing**~~ | `pre_model_call` | **DEPRECATED** | `TwoTierRouter` — cheap LLM classifies simple→flash, complex→pro. Deprecated in favor of direct model configuration. |
| **Human Loop** | `post_permission`, `pre_tool_exec` | DONE | SSE approval channel with 60s timeout |
| **Compaction** | `round_end` | DONE | `CompactionPlugin` — token-aware, 75% threshold, keeps last 8 msgs + LLM summary |
| **Checkpoint** | `round_end`, `session_end` | DONE | `CheckpointPlugin` — round snapshots + session archiving for undo/restore |
| **Trace** | all hooks (cross-cutting) | DONE | `TracePlugin` — JSONL event recording for debugging, replay, evaluation |
| **Evaluation** | offline | DONE | `EvalPlugin` — replay traces, compute metrics, diff reports |
| Planner | (deferred) | P1 | Task decomposition via system model |
| bash | (deferred) | P1 | Shell executor with injection safety |
| code_interpreter | (deferred) | P1 | Python sandbox |

### Deprecated / Deferred

| Module | Action | Reason |
|--------|--------|--------|
| A2A Communication (`arf/communication/`) | Deprecated | Focus on agent+subagent first |
| TaskScheduler (`arf/concurrency/`) | Deprecated | Single-agent execution only |
| Plan-Execute strategy | Deferred | ReAct + TODO sufficient for now |

### Framework vs. Application

**Boundary principle**: The framework provides the mechanism (how); the application decides what to do through configuration + instantiation. `agent.yaml` is the bridge — the framework reads it and auto-assembles all capabilities; the app declares "what to use" without needing to know "how to implement."

| Layer | Scope | Capabilities |
|-------|-------|-------------|
| **Framework** (`arf/`) | **6 Skeletons** | **Prompt Assembly** — `SystemPromptProvider` (prefix + suffix + `$INVENTORY` template). **Resource Registry** — MCP-based unified interface, `ResourceResolver`, `FileWatcher` hot reload. **Permission Control** — `SessionModeManager` + `PermissionRegistry` deny→ask→allow. **Security Audit** — `PathCheckToolGuard`, `ContentGuard` rule-based screening. **Executor** — `SandboxManager` per-session isolation, `ConcurrentToolExecutor` parallel exec. **Control Plane** — `GraphEngine`+`LoopStrategy` ReAct, State management, 9 Hook injection points. |
| | **Plugins** | `InProcessHookRunner` executes `PluginProtocol` instances on lifecycle hooks. Built-in: `CompactionPlugin` (token-aware sliding window), `CheckpointPlugin` (round snapshots + session archive), `TracePlugin` (JSONL event recording), `EvalPlugin` (offline trace replay + metrics), `MemoryPlugin` (long-term extraction), `TodoPlugin` (task tracking), `UndoPlugin` (round rollback), `ModelRouterPlugin` (fast/slow dispatch), `HumanLoopPlugin` (SSE approval). |
| | **Infrastructure** | `ModelAdapter` exponential backoff + retry, `TokenBucket` rate limiting, `CircuitBreaker` fault isolation, `DefaultErrorPolicy`/`FunctionBackend` rollback, `SubprocessHookRunner` for external hook scripts, `SkillPipeline` dependency ordering |
| | **Protocols** | Protocol classes (`core/protocols/`) — defines `LoopStrategy`, `StateStore`, `ToolExecutor`, `PluginProtocol`, `HookRunner`, `GuardRunner`, `EventBus`, `ModelRouter`, and all other abstract interfaces |
| **Application** (`app/`) | **Frontend** | Vue 3 + TypeScript + Vite SPA, Pinia state management / VueRouter, ECharts charts / i18n (zh-CN + en-US), ChatPanel / TraceView / ResourcePanel and other components |
| | **HTTP Service** | FastAPI + Uvicorn + Streamable HTTP (NDJSON), REST endpoints (chat / trace / resources / config / usage …), WebSocket endpoint, CORS / SPA fallback / StaticFiles |
| | **CLI** | init / start / stop / chat / list / validate / config |
| | **Config & Data** | `agent.yaml` — model definitions (`model_defs`) + agent/subagent model refs (`agent_models`) + plugin config (`plugins_config`), custom `tools/` (file_*, web_*, python_exec …), custom `skills/`, custom `hooks/`, DeepSeek API key management |

<br/>

---

## Part II — Reference App: How It Uses the Framework

The reference app at `app/arf_default_assistant/` demonstrates how an application calls every framework capability. Each section below shows the application-layer design and links to the framework implementation.

### Convention over Configuration

Four entity types — **model**, **tool**, **skill**, **hook** — each following a predictable directory convention. The framework discovers; you don't register. A tool is two files: `tool.yaml` (schema) + `function.py` (logic). That's the entire API surface.

Tools and skills live on the filesystem — `tool.yaml` + `function.py` per tool, `skills/*.yaml`. The framework discovers them automatically. `agent.yaml` only overrides specific fields when needed:

```yaml
tools:
  - name: file_reader
    activation: kernel   # override activation only; the rest from filesystem

skills:
  - name: code_review
    activation: discoverable
```

### Progressive Disclosure

Only essential kernel tools are always active. Everything else loads on demand via `resource_loader`, runs, and deactivates. The agent pays only for what it actually uses.

### MCP Unified Resources

Tools and skills are accessed through a single MCP (Model Context Protocol) interface. A local MCP Server subprocess aggregates local filesystem resources (`tools/`, `skills/`, `plugins/`) with optional external MCP connections:

```yaml
# agent.yaml — optional external MCP servers
mcp_servers:
  - name: search
    transport: sse
    url: http://localhost:9000/sse
```

The agent communicates via stdio JSON-RPC. The app layer is source-agnostic — tool origins (local, plugin, remote) are transparent.

### Memory — Automatic Extraction & Retrieval

The app **does not** implement its own memory. Framework memory extraction lives in the [`memory` plugin](docs/plugins/memory.md) (`arf/plugins/memory/`) — mounted on `round_end` hook, configured with its own model via `plugins_config.memory.model`. Extracted facts, preferences, and decisions are written atomically to `memory.md` (≤300KB), loaded at session startup and injected into the system prompt.

```yaml
plugins_config:
  memory:
    model: deepseek-v4-flash        # model ref from model_defs
    interval: 5                      # extract every 5 rounds
    max_memory_size: 300             # KB limit for memory.md
```

[Design doc →](docs/plugins/memory.md)

### Compaction — Token-Aware Context Management

`CompactionPlugin` (mounted on `round_end` hook) monitors the previous turn's token usage. At 75% of the model's context window, it triggers: keeps the last 8 messages, summarizes older turns via LLM, and appends to `context_summary`. Long tool outputs (>2000 chars) are written to disk with a summary pointer in context. Configurable via `plugin.yaml`:

```yaml
# arf/plugins/compaction/plugin.yaml
config:
  threshold: 0.75
  window_size: 131072
  keep_count: 8
```

[Design doc →](docs/context-management.md)

### Model Configuration

Models are defined inline at the top of `agent.yaml`. The `model` field is the unique identifier. Agent and SubAgent reference models by name with ordered fallback; Plugins reference a single model.

```yaml
model_defs:                          # top-level definitions
  - model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY    # env var name, not the key value
    kwargs: {reasoning_effort: max}
  - model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs: {temperature: 0.7}

agent_models:                        # agent: ordered fallback [pro → flash]
  - model: deepseek-v4-pro
  - model: deepseek-v4-flash

plugins_config:                      # plugins: single model ref
  compaction:
    model: deepseek-v4-flash
  memory:
    model: deepseek-v4-flash
```

Reference with partial override (inherits from definition, overrides specified fields):
```yaml
agent_models:
  - model: deepseek-v4-pro
  - model: deepseek-v4-flash
    kwargs: {temperature: 0.0}      # override just temperature
```

Fallback triggers on 5xx, 429, and network errors. Client errors (4xx) do not trigger fallback.

### Sandbox & Permissions

`PathCheckToolGuard` blocks path traversal and absolute paths before every tool call. `SessionModeManager` controls the global permission mode (`auto` / `ask` / `plan`), with optional per-agent `policy` overrides. `PermissionRegistry` enforces deny→ask→allow lists. Tools run in-process; the guard checks every invocation.

Three session modes:
- **auto** — all tools execute directly, ignores permission lists
- **ask** — evaluate deny/ask/allow lists per tool; unknown tools require approval (default, recommended)
- **plan** — global read-only; all write/exec tools are denied (security review)

```yaml
session_mode: ask                # global mode: auto | ask | plan

advanced:
  guardrails:
    permissions:
      policy: ask                # per-agent override (auto/ask/plan), only active in global ask mode
      deny: []
      ask: [python_exec, file_deleter]
      allow: [file_reader, web_search, web_fetch]
```

[Design doc →](docs/tool-sandbox.md)

### Interrupt — Cancel & Undo

The engine checks an `asyncio.Event` cancellation token each turn. `POST /api/chat/cancel` or client disconnect stops the agent. `RoundManager` maintains 3 rolling round-level snapshots — undo restores state + files to the beginning of any recent round, even across agent handoff boundaries. The `undo_executed` trace event marks rollback boundaries without deleting history. Data-modifying tools can export an optional `rollback()` function — `FunctionBackend` calls it automatically when `execute()` throws, rolling back side effects at the individual tool level. Hook exit-code-2 messages are injected into the conversation.

[Design doc →](docs/interrupt.md)

### Skill Pipeline — Tool Execution Order

Skills can declare tool pipelines with explicit dependencies. The engine enforces execution order — a tool step cannot run until all `depends_on` are completed.

```yaml
- name: resource_scaffold
  tools: [file_writer, resource_loader]
  pipeline:
    - tool: file_writer
    - tool: resource_loader
      depends_on: [file_writer]
```

[Design doc →](docs/skill-pipeline.md)

### Trace — Full Pipeline Visibility

`TracePlugin` (cross-cutting, mounted on all 9 hook points) records every lifecycle event to JSONL trace files. Each event carries `round` (user interaction) and `turn` (internal iteration). The waterfall view at `/traces` groups by round with expandable iterations: model response → tool calls → hooks. `UsageTracker` provides token stats. Standalone HTML viewer at `/trace-viewer`.

[Design doc →](docs/trace.md)

### Dual-Agent Architecture

User Agent handles your tasks. System Agent handles internal operations — resource creation, tool generation, validation. Separate execution, shared workspace. The user sees one assistant; the dual architecture is an implementation detail.

```yaml
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    models:
      - model: deepseek-v4-pro
```

<br/>

---

## Quick Start

Requires Python ≥ 3.11.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/arf_default_assistant
python test_setup.py   # verify environment
python cli.py start    # launch service
```

Browser opens at **http://127.0.0.1:8000** — enter your API key and start.

<br/>

## Framework Dev / App Building

**Building apps on ARF**: See the [APP Developer Guide](./APP开发者指南.md) — start from a minimal `agent.yaml`, configure models/tools/skills/hooks, launch the server.

**Hacking on the framework**: The framework is built on **6 skeletons** that run without plugins. Each skeleton maps to a Protocol. Plugins mount on lifecycle Hooks to extend functionality. Check the [TODO](#todo) section for pending fixes and evolution directions.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
```

**Core stack:** Python 3.11+ · FastAPI · Vue 3 · TypeScript · Vite

<br/>

---

## TODO

### 已知代码问题 (2026-05-26 事实校验)

### TODO — Improvement Items

| # | Title | Code Path | Domain | Type | Details |
|---|-------|-----------|--------|------|---------|
| 1 | ~~Engine `invoke`/`astream` duplication~~ → **FIXED** | `arf/engine/graph.py` | Process Scheduling | Framework | ~~~400 lines of identical Agent Loop logic in two methods.~~ → Extracted `_step_classify_tool_calls()` — guard pipeline, sandbox, permissions, and approval logic shared by both paths. |
| 2 | ~~`BaseAgent.__init__` oversized~~ → **FIXED** | `arf/agent/base.py` | Process Creation | Framework | ~~Constructor directly instantiates 20+ implementations inline.~~ → Extracted `_merge_models()` and `_build_resource_resolver()` factory methods; absorbs legacy `transaction_ctx` override. |
| 3 | ~~`server.py` monolithic~~ → **FIXED** | `app/arf_default_assistant/routers/` | User Interface | App | ~~REST routes, WebSocket, SSE streaming, CORS, file serving, state management, config APIs all in one file.~~ → Split into `routers/` by route group: `chat.py`, `trace.py`, `config.py`, `resources.py`, `misc.py`. `server.py` slimmed from 846→137 lines (app creation + lifespan + router mounts). Shared state in `routers/state.py`. |
| 4 | ~~`SnapshotRollback` null state snapshot~~ → **FIXED** | `arf/resources/backends/function.py` | Fault Recovery | Framework | ~~`begin()` always sets `"state_snapshot": None`~~ → Replaced with `FunctionBackend` inline rollback: tool `function.py` optionally exports `rollback()`, called automatically on `execute()` exception. `TransactionContext` protocol and `SnapshotRollback` class removed. |
| 5 | ~~`EvalRunner` computes empty traces~~ → **FIXED** | `arf/evaluation/runner.py` | Quality Assurance | Framework | ~~trace hardcoded as `{"turns": []}`~~ → Rewritten: `EvalRunner` captures real traces via `EventBus.events_since()`, `events_to_trace()` assembles structured turn data, all 4 metrics compute on real traces. `BenchmarkBuilder` creates benchmarks from `FileTraceStore` sessions, `EvalComparator` diffs cross-run reports for regression detection. |
| 6 | ~~Global state `registry._agent`~~ → **FIXED** | `arf/agent/registry.py` removed | Process Isolation | Framework | ~~`_agent: Any = None` module-level singleton~~ → Deleted. `_engine` and `_state_store` now injected via tool executor params (same pattern as `_agent_mode`). `undo` tool receives them through function signature. `server.py` no longer calls `set_agent()`. |
| 7 | ~~`PromptBasedPlanner` returns empty plans~~ → **FIXED** | `arf/plugins/planner/` | Task Planning | Framework | ~~`generate_plan()` always returned `{"steps": []}`~~ → Replaced by plugin system. `arf/plugins/` provides framework plugins (planner, todo, undo, ...). App declares `plugins: [planner, todo]` in `agent.yaml`. `PluginProvider` scans plugin directories, `ResourceResolver` merges plugin tools/skills with app resources. | `generate_plan()` always returns `{"steps": []}`, `detect_divergence()` always returns `{"diverged": False}`. Engine injects `_call_model` but the LLM is never called for plan generation. **Risk**: The `Planner` protocol is a key extension point for autonomous agents; callers receiving empty results may misinterpret as "no decomposition needed" |
| 8 | ~~SSE listener leak~~ → **FIXED** | `arf/streaming/adapters/sse.py` | Communication | Framework | ~~Callback removal relied on async generator `finally` which CPython doesn't run on `break`/exception.~~ → Replaced with `@asynccontextmanager`: `async with stream.listen() as queue` — `__aexit__` guarantees cleanup on all exit paths. |
| 9 | ~~Inconsistent code conventions~~ → **FIXED** | 13 files + `graph.py` + `planner.py` | Documentation | Framework | ~~14 files missing module docstrings; 10 bare `dict` annotations in signatures.~~ → All 13 files now have module docstrings. Core signatures use `dict[str, Any]` instead of bare `dict`. `test_code_style.py` enforces consistency. |
| 10 | ~~No rate limiting / circuit breaker~~ → **FIXED** | `arf/protection/` | Process Scheduling | Framework | ~~LLM API calls had no rate limiting or circuit breaker protection.~~ → `ModelCallProtector` with `TokenBucket` (per api_base) + `CircuitBreaker` (per model, exponential cooldown). Wraps `_call_model`/`_stream_model` closures in `BaseAgent._inject_model_calls()`. Five event types emitted via EventBus → trace viewers. Engine-level retry removed from `DefaultErrorPolicy`. Zero changes to GraphEngine/ModelAdapter. See [`docs/api-protection.md`](docs/api-protection.md). |
| 11 | Missing open-source infrastructure | — | Distribution | Framework | No `CONTRIBUTING.md`, PR/Issue templates, `CHANGELOG.md`, or versioned release process. Documentation is rich but there's no guidance for external contributions. **Risk**: Potential contributors don't know submission standards; users can't assess upgrade impact without changelog |

**Plugins** — Hook-mounted capability bundles. Each plugin has `plugin.yaml` (name + hooks + config) and `plugin.py` (PluginProtocol impl). `PluginLoader` scans `arf/plugins/{name}/`. Community-contributable. Plugin ≠ Tool — plugins fire on lifecycle hooks, tools are Agent-called MCP resources.

| # | Plugin | Status | Hook | Description |
|---|--------|--------|------|-------------|
| P-1 | ✅ `compaction` | DONE | `round_end` | Token-aware context compaction, 75% threshold + LLM summary |
| P-2 | ✅ `checkpoint` | DONE | `round_end`, `session_end` | Round snapshots + session archiving, restore support |
| P-3 | ✅ `trace` | DONE | all 9 hooks | JSONL event recording for debug, replay, evaluation |
| P-4 | ✅ `eval` | DONE | offline | Trace replay + metric computation + diff reports |
| P-5 | ✅ `memory` | DONE | `round_end` | Long-term memory extraction via system model, atomic write to `memory.md` |
| P-6 | ✅ `todo` | DONE | `round_start`, `round_end` | Task list tracking with reminder injection |
| P-7 | ✅ `undo` | DONE | `round_end`, `sandbox_persist` | Round-level state + file rollback |
| ~~P-8~~ | ~~`model_router`~~ | **DEPRECATED** | `pre_model_call` | TwoTierRouter fast/slow dispatch — deprecated |
| P-9 | ✅ `human_loop` | DONE | `post_permission` | SSE approval channel with 60s timeout |
| P-10 | `bash` | P1 | `pre_tool_exec` | Shell executor, community-audited injection safety |
| P-11 | `code_interpreter` | P1 | `pre_tool_exec` | Python sandbox |

### Evolution

See per-module design documents for evolution directions:
- [Context Management](docs/context-management.md) — semantic-unit compaction, adaptive threshold, cross-session summary reuse
- [Memory Plugin](docs/plugins/memory.md) — multi-round trigger, custom prompt templates
- [Resource Registry](docs/resource-registry.md) — hierarchical override merge, MCP multi-source Provider
- [Tool Sandbox](docs/tool-sandbox.md) — per-invocation sandbox, content-aware scanning
- [Skill Pipeline](docs/skill-pipeline.md) — multi-agent DAG, worktree isolation
- [Interrupt](docs/interrupt.md) — pause/redirect, idle timeout
- [Trace](docs/trace.md) — SQLite trace DB, OpenTelemetry export
- [Eval Benchmark](docs/eval-benchmark.md) — CLI integration, semantic similarity metrics

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
