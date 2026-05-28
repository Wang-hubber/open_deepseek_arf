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

### Harness as Kernel — Problem-Domain Architecture

> **Model + Harness = Agent. CPU + Kernel = Computer.**
>
> Token is the instruction. Agent session is the process. Tool call is the system call.

*Click any problem name for the full design document.*

| # | Problem | OS Analogy | Current | Evolution |
|---|---------|------------|---------|-----------|
| 1 | **[Agent Execution →](docs/agent-execution.md)**<br>Lifecycle + loop control | Process management (fork/exec/scheduler) | `GraphEngine` dual-mode invoke/astream main loop. `BaseAgent` DI assembly of all protocols. `LoopStrategy` ReAct pattern. `max_turns` per-session circuit breaker. | Multi-agent DAG orchestration; pause/resume/checkpoint; plan-execute loop strategy |
| 2 | **[LLM Scheduling →](docs/model-routing.md)**<br>Model dispatch + API protection | CPU scheduling (big.LITTLE/CFS) + process supervision | `TwoTierRouter` — LLM classifier dispatches simple→flash, complex→pro. `system_model` for background tasks. `TokenBucket` per-endpoint rate limiting (configurable rps + burst). `CircuitBreaker` per-model with exponential cooldown — trips after consecutive failures, probes via HALF_OPEN, auto-recovers. `ModelAdapter` exponential backoff retry. | Adaptive thresholds (history-based failure_threshold); priority queuing (system vs user requests); distributed rate limiting (multi-agent quota sharing) |
| 3 | **[Context Management →](docs/memory-management.md)**<br>Context window compaction | Virtual memory (paging/swapping) | `SlidingWindowCompactor` — token-aware, triggers at 75% threshold, keeps last 4 msgs + LLM summary. Long tool outputs summarized to disk. | Semantic-unit compaction; adaptive threshold; cross-session summary reuse |
| 4 | **[Interrupt & Recovery →](docs/interrupt.md)**<br>Cancel + undo + rollback | Hardware interrupt (ISR) + signals | `asyncio.Event` cancellation token checked each turn. `RoundManager` — configurable snapshot window (default 3), state + file rollback across handoff boundaries. `FunctionBackend` rollback — tools export optional `rollback()` called on `execute()` exception. `SubprocessHookRunner` exit-code 2 → message injection. | Pause/redirect vectors; idle timeout; interrupt priority levels |
| 5 | **[A2A Communication →](docs/a2a-communication.md)**<br>Agent-to-agent interaction | IPC (pipe/signal/shared memory/message queue) | `HandoffManager` signal-based agent switching in invoke/astream loop. `InMemoryAgentBus` — asyncio.Queue message routing (broadcast, targeted, capability discovery). `PeerAgent` — P2P negotiate/handoff/discover. `DictWorkspace` shared memory. `InMemoryLock` synchronisation. `MajorityVoteConsensus`. Protocol layer for AgentBus/Supervisor/Consensus. `SkillPipeline` — tool execution order with explicit dependencies. `ConcurrentToolExecutor` parallel execution. | Network A2A (gRPC); pub/sub agent discovery; DAG multi-agent scheduling |
| 6 | **[Resource System →](docs/resource-registry.md)**<br>Tool/skill/model discovery | File system + udev + systemd | Convention over configuration: `tool.yaml`+`function.py` per tool, `skills/*.yaml`, `models/*.yaml`. kernel/dynamic split with freeze-once semantics. `FileWatcher` inotify+polling hot reload. `ResourceResolver` override merge + `generate_config()` dump. | Hierarchical override merging; MCP multi-source Provider; cross-reference validation |
| 7 | **[Security & Sandbox →](docs/tool-sandbox.md)**<br>Access control + path safety | Protection rings (Ring 0-3) + ACL | `PathCheckToolGuard` — recursive scan (.., symlink, depth/count quota). `ToolPermissionChecker` deny→ask→allow enforcement. `HumanLoop` approval channel with SSE push + 60s timeout. `GuardDefaults` three-line defense (PathCheck/Regex/None). | Per-invocation sandbox; MCP protocol; OAuth-scoped permissions |
| 8 | **[Observability →](docs/trace.md)**<br>Event tracing + metrics | syslog / dtrace / perf | `EventType` Literal covering the full lifecycle. `InMemoryEventBus` → `FileTraceStore` (per-session JSON). `UsageTracker` token accounting. Standalone HTML trace viewer. Vue SPA waterfall grouped by interaction round. `SseStream` for real-time events. | SQLite trace DB; OpenTelemetry export; Prometheus metrics |
| 9 | **[Built-in Tools →](docs/api-protection.md)**<br>Plugin system | OS bundled software (coreutils, Notepad) | `arf/plugins/` directory — `agent.yaml` `plugins:` field activates by name. `PluginProvider` scans plugin dirs, `ResourceResolver` merges into tool/skill lists. P0 plugins: `planner` (task decomposition via system_model), `todo` (workspace task list), `undo` (round checkpoint rollback). App-layer overrides plugin tools (app > plugin). | P1: bash, code_interpreter, file_ops; P2: web_search, web_fetch, memory_tools; community plugin registry |
| 10 | **[Quality Assurance →](docs/eval-benchmark.md)**<br>Regression testing | CI test suite + session replay | `BenchmarkBuilder` creates test cases from real session traces. `EvalRunner` replays via `agent.chat()`, captures via `EventBus.events_since()`. 4 built-in metrics (success rate, tool accuracy, turn efficiency, output contains). `EvalComparator` diffs reports. 198 unit/functional tests. | CLI integration; HTML visual report; semantic similarity metric; CI pipeline |

### Framework vs. Application

**Boundary principle**: The framework provides the mechanism (how); the application decides what to do through configuration + instantiation. `agent.yaml` is the bridge — the framework reads it and auto-assembles all capabilities; the app declares "what to use" without needing to know "how to implement."

| Layer | Scope | Capabilities |
|-------|-------|-------------|
| **Framework** (`arf/`) | **Agent Execution** | `GraphEngine` (invoke + astream dual mode), `BaseAgent` DI assembly, `LoopStrategy` ReAct, `RoundManager` checkpoint/undo, `HandoffManager` multi-agent switching, `ConcurrentToolExecutor` parallel execution, `SkillPipeline` dependency ordering |
| | **LLM Scheduling** | `TwoTierRouter` fast/slow dispatch, `ModelAdapter` exponential backoff retry, `TokenBucket` per-endpoint rate limiting, `CircuitBreaker` per-model fault isolation, `ModelCallProtector` decorator-pattern injection |
| | **Context Management** | `SlidingWindowCompactor` (75% threshold + LLM summary), `_load_resident_memory()` (loads `memory.md` at startup, injects into `{{MEMORY}}` placeholder) |
| | **Resource System** | `ResourceResolver` (unified resolution), `ToolProvider`/`SkillProvider`/`ModelProvider`, `PluginProvider` (scans `arf/plugins/`), `ResourceCache` (kernel/dynamic), `FileWatcher` (inotify/polling hot reload) |
| | **Security** | `PathCheckToolGuard` (.., symlink, depth/count), `ToolPermissionChecker` deny→ask→allow, `HumanLoop` SSE approval + 60s timeout, `GuardDefaults` three-line defense |
| | **Observability** | `InMemoryEventBus`, `FileTraceStore` (per-session JSON), `UsageTracker` (token accounting), standalone HTML trace viewer, Vue SPA waterfall |
| | **Infrastructure** | `SubprocessHookRunner` (exit-code contract), `DefaultErrorPolicy`/`FunctionBackend` rollback, `EvalRunner`/`BenchmarkBuilder`/`EvalComparator` (session replay & regression) |
| | **Protocols** | Protocol classes (`core/protocols/`) — defines `MemoryStore`, `MemoryWriter`, `HookRunner`, `GuardRunner`, `EventBus`, `ModelRouter`, `LoopStrategy` and all other abstract interfaces |
| **Application** (`app/`) | **Frontend** | Vue 3 + TypeScript + Vite SPA, Pinia state management / VueRouter, ECharts charts / i18n (zh-CN + en-US), ChatPanel / TraceView / ResourcePanel and other components |
| | **HTTP Service** | FastAPI + Uvicorn + SSE streaming, REST endpoints (chat / trace / resources / config / usage …), WebSocket endpoint, CORS / SPA fallback / StaticFiles |
| | **CLI** | init / start / stop / chat / list / validate / config |
| | **Config & Data** | `agent.yaml` — agent behavior + `plugins:` activation + routing + memory + compaction, `models/deep.yaml` + `models/quick.yaml`, custom `tools/` (file_*, web_*, python_exec …), custom `skills/`, custom `hooks/`, DeepSeek API key management |

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

### Memory — Automatic Extraction & Retrieval

The app **does not** implement its own memory. The framework's `LLMMemoryWriter` extracts facts, preferences, and decisions after every turn. `LLMMemoryRetriever` injects relevant memories into the system prompt. All backed by `FileMemoryStore` → `memory/memory.json`.

```yaml
advanced:
  system_model: quick     # system model shared by memory, routing, compaction
  memory:
    store: file
    retriever: llm
    writer: llm
```

[Design doc →](docs/memory-management.md)

### Compaction — Token-Aware Context Management

The `SlidingWindowCompactor` monitors the previous turn's token usage. At 75% of the model's context window, it triggers: keeps the last 4 messages, summarizes older turns via LLM, and appends to `context_summary`. Long tool outputs are written to disk with a summary in context.

```yaml
advanced:
  compaction:
    strategy: sliding_window
    threshold: 0.75
```

[Design doc →](docs/memory-management.md)

### Model Routing — Fast/Slow Dispatch

`TwoTierRouter` classifies each user query via a cheap LLM: simple → `quick` (flash), complex → `deep` (pro). Background tasks (memory, classification) use a dedicated model. Per-turn dynamic switching.

```yaml
models:
  - type: quick
    model: deepseek-v4-flash
    context_window: 800000
  - type: deep
    model: deepseek-v4-pro
    context_window: 1000000

advanced:
  routing:
    strategy: two_tier
    default: quick
    classify: {medium: quick, complex: deep}
    fallback: {deep: quick}
```

[Design doc →](docs/model-routing.md)

### Sandbox & Permissions

`PathCheckToolGuard` blocks path traversal and absolute paths before every tool call. `ToolPermissionChecker` enforces deny→ask→allow rules. Tools run in-process; the guard checks every invocation.

```yaml
advanced:
  permissions:
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

Events emitted by engine across invoke + astream dual paths → `FileTraceStore` (JSON) + `UsageTracker` (token stats). Each event carries `round` (user interaction) and `turn` (internal iteration). The waterfall view at `/traces` groups by round with expandable iterations: model response → tool calls → hooks. Standalone HTML viewer at `/trace-viewer`.

[Design doc →](docs/trace.md)

### Dual-Agent Architecture

User Agent handles your tasks. System Agent handles internal operations — resource creation, tool generation, validation. Separate execution, shared workspace. The user sees one assistant; the dual architecture is an implementation detail.

```yaml
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    routing:
      strategy: static
      default: deep
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

**Hacking on the framework**: Check the [TODO](#todo) section for pending fixes and evolution directions. Framework code lives in `arf/`, with dependency injection allowing you to replace any default implementation.

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

**Plugins** — Framework-capability bundles, activated by `agent.yaml` `plugins:` field. `PluginProvider` scans `arf/plugins/{name}/`. Community-contributable.

| # | Plugin | Status | Description |
|---|--------|--------|-------------|
| P-1 | ✅ `planner` | DONE | Task decomposition via system_model, replaces empty PromptBasedPlanner |
| P-2 | ✅ `todo` | DONE | Task list management (add/check/list/clear), reads/writes `todo.md` |
| P-3 | ✅ `undo` migration | DONE | Move from `app/tools/` → `arf/plugins/undo/` |
| P-4 | ✅ `plugin_provider` | DONE | PluginProvider scans plugin dirs, `agent.yaml` `plugins:` field |
| P-5 | `bash` | P1 | Shell executor, community-audited injection safety |
| P-6 | `code_interpreter` | P1 | Python sandbox, replaces `app/tools/python_exec` |
| P-7 | `file_ops` | P1 | Read/write/list/delete consolidated from app tools |
| P-8 | `web_search` | P2 | DuckDuckGo search, move from app to plugin |
| P-9 | `web_fetch` | P2 | HTTP fetch, move from app to plugin |
| P-10 | `resource_loader` | P2 | Hot-reload resources, move from app to plugin |
| P-11 | ✅ `memory` | DONE | **[Long-term memory extraction →](docs/plugins/memory.md)** — subprocess-based extraction via sysmodel. Round-interval trigger (default 10). Atomic write to `memory.md` (≤300KB), loaded at session startup. Two trigger paths: `round_end` hook + `memory_extract` tool |

### Evolution

### 演进方向

参见各模块设计文档第三章：
- [Context Management](docs/memory-management.md#3-演进方向) — 语义单元压缩、自适应阈值、跨会话摘要复用
- [Memory Plugin](docs/plugins/memory.md) — 多轮次触发、自定义 prompt 模板、社区贡献
- [Model Routing](docs/model-routing.md#3-演进方向) — 三级分类器、连续负载跟踪、模型硬件化
- [Resource Registry](docs/resource-registry.md#3-演进方向) — 层次化覆盖合并、MCP 多源 Provider
- [Tool Sandbox](docs/tool-sandbox.md#3-演进方向) — Per-invocation sandbox、MCP 协议
- [Skill Pipeline](docs/skill-pipeline.md#3-演进方向) — 多 Agent DAG 分析、Worktree 隔离
- [A2A Communication](docs/a2a-communication.md) — 网络 A2A（gRPC）、发布/订阅 Agent 发现、DAG 多 Agent 调度
- [Interrupt](docs/interrupt.md#3-演进方向) — 暂停/重定向、空闲超时
- [Trace](docs/trace.md#3-演进方向) — SQLite Trace DB、OpenTelemetry 导出

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
