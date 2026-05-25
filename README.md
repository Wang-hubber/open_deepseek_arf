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

| Problem | OS Solution | Current | Evolution |
|---------|-------------|---------|-----------|
| **[Memory →](docs/memory-management.md)**<br>OOM + persistence | Virtual memory + file system | Token-aware sliding window compaction at 75% threshold. LLM summarization of evicted turns. Automatic fact/preference/decision extraction with dedup and semantic retrieval. Long tool outputs written to disk. | Semantic-unit retrieval; knowledge graph index |
| **[Model Routing →](docs/model-routing.md)**<br>KV cache | Multi-level cache + big.LITTLE scheduling | Two-tier LLM classifier (medium→quick, complex→deep). Dedicated model (v4-flash with no thinking) for framework background tasks. KV cache is handled by the inference side. | Model-hardware codification; LLM as hardware |
| **[Resource Discovery →](docs/resource-registry.md)**<br>Registration & lifecycle | Registry + service manager (systemd/udev/launchd) | Convention over configuration: `tool.yaml`+`function.py` per tool, `skills/*.yaml`, `models/*.yaml`. Kernel/dynamic split with freeze-once semantics. FileWatcher inotify+polling dual-track hot reload. ResourceResolver override merge + `generate_config()` dump. | Hierarchical override merging; MCP multi-source Provider; cross-reference validation |
| **[Tool Sandbox →](docs/tool-sandbox.md)**<br>Security boundaries | System calls + protection rings (Ring 0–3) + ACL | `tool.yaml` + `function.py` per tool. `PathCheckToolGuard` blocks traversal. Dual-source isolation: framework read-only, workspace read-write. Permission deny→ask→allow pipeline. | Per-invocation sandbox; MCP protocol |
| **[Concurrency →](docs/skill-pipeline.md)**<br>Deadlock prevention | Superscalar execution + dependency graph | Sequential agent loop; parallel tool calls within a turn via `ConcurrentToolExecutor`. Skills declare tool pipelines with explicit dependencies — engine enforces order. Hook `asyncio.gather` concurrency. | Multi-agent DAG analysis; worktree isolation |
| **[Interrupt →](docs/interrupt.md)**<br>User intervention | Hardware interrupt: save state → ISR → restore | `asyncio.Event` cancellation. 3-snapshot undo (state + files) via API or in-conversation `undo` tool. Hook exit-code-2 message injection. | Pause/redirect vectors; idle timeout |
| **[Trace →](docs/trace.md)**<br>Observability | System monitoring + structured event log | 15 event types in EventType Literal → `FileTraceStore` (JSON) + `UsageTracker`. Frontend waterfall grouped by interaction round. Standalone viewer. | SQLite trace DB; OpenTelemetry export |

### Framework vs. Application

| Layer | Scope | Examples |
|-------|-------|----------|
| **Framework** (`arf/`) | Conventions, engine, resource system, trace infrastructure | `GraphEngine`, `ResourceResolver`, three Providers (Tool/Skill/Model), `ResourceCache`, `FileWatcher`, dual-source loading, hook exit-code contract, `EventBus`, `FileTraceStore` |
| **Reference App** (`app/`) | A concrete agent built on the framework | Vue 3 frontend, model routing, `session_archiver`, memory pipeline, sandbox, undo |
| **User workspace** | What you build on top | Model configs, custom tools and skills, `agent.yaml` |

<br/>

---

## Part II — Reference App: How It Uses the Framework

The reference app at `app/arf_default_assistant/` demonstrates how an application calls every framework capability. Each section below shows the application-layer design and links to the framework implementation.

### Convention over Configuration

Four entity types — **model**, **tool**, **skill**, **hook** — each following a predictable directory convention. The framework discovers; you don't register. A tool is two files: `tool.yaml` (schema) + `function.py` (logic). That's the entire API surface.

The app declares tools and skills in `agent.yaml`:

```yaml
tools:
  - name: file_reader
    description: 读取文件或列出目录
    parameters: {type: object, properties: {operation: ...}, required: [operation, path]}
    activation: kernel

skills:
  - name: code_review
    description: Review code changes for correctness
    tools: [file_reader, file_writer]
    activation: discoverable
```

### Progressive Disclosure

Only essential kernel tools are always active. Everything else loads on demand via `resource_loader`, runs, and deactivates. The agent pays only for what it actually uses.

### Memory — Automatic Extraction & Retrieval

The app **does not** implement its own memory. The framework's `LLMMemoryWriter` extracts facts, preferences, and decisions after every turn. `LLMMemoryRetriever` injects relevant memories into the system prompt. All backed by `FileMemoryStore` → `memory/memory.json`.

```yaml
advanced:
  memory:
    store: file
    retriever: llm
    writer: llm
    model: quick            # cheap model for memory ops
    temperature: 0.3
    thinking_enabled: false
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
  - name: quick
    model: deepseek-v4-flash
    context_window: 800000
  - name: deep
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

The engine checks an `asyncio.Event` cancellation token each turn. `POST /api/chat/cancel` or client disconnect stops the agent. Three rolling snapshots per interaction round enable state + file undo via API or the in-conversation `undo` tool. Hook exit-code-2 messages are injected into the conversation.

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

15 event types in EventType Literal, 13 emitted by engine → `FileTraceStore` (JSON) + `UsageTracker` (token stats). Each event carries `round` (user interaction) and `turn` (internal iteration). The waterfall view at `/traces` groups by round with expandable iterations: model response → tool calls → hooks. Standalone HTML viewer at `/trace-viewer`.

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

## TODO

> Issues found during systematic source-code and documentation fact-checking. Contributors welcome.

### Pending Fixes — Code

*Confirmed 2026-05-25 via source review.*

| # | Issue | Location |
|---|-------|----------|
| 1 | (fixed 2026-05-25) `ResourceCache` wired into all three Providers | `arf/resources/cache.py` |
| 2 | (fixed 2026-05-25) CLI commands missing `/api/` prefix — all 5 occurrences corrected | `app/arf_default_assistant/cli.py` |
| 3 | `agent.yaml` fields parsed but never read: `role`/`task` wired into system prompt; `reload` wired; remaining `guardrails`/`human_loop`/`streaming`/`sandbox`/`tool_retrieval` + multi-agent fields still pending | `arf/agent/config.py` |
| 4 | (fixed 2026-05-25) `FileStateStore` added as default; app saves removed | `arf/engine/checkpoint.py` |
| 5 | `FileWatcher` lifecycle managed by app (`_agent._file_watcher.start/stop`), not framework | `arf/resources/file_watcher.py`, `server.py` |
| 6 | App accesses `_agent._engine`, `_agent._resource_resolver` etc. (private internals) | `app/arf_default_assistant/server.py` |
| 7 | (closed — orphan tools are filesystem source-of-truth, agent.yaml is override layer only) | `app/arf_default_assistant/tools/` |
| 8 | (fixed 2026-05-25) `set_agent()` called twice in lifespan — duplicate removed | `app/arf_default_assistant/server.py:70` |
| 9 | `ToolConfig.provider`/`backend`/`execution`/`source` parsed but never enforced | `arf/core/config_base.py` |
| 10 | Multi-agent (`agents:`/`handover:`/`supervisor:`) parsed but never wired into engine | `arf/agent/config.py` |
| 11 | (fixed 2026-05-25) `ReloadConfig` wired — `watch` and `poll_interval` now read by BaseAgent | `arf/agent/base.py`, `config_base.py` |
| 12 | `EventType` Literal defines 15 types; `approval_required`/`approval_resolved` reserved for approval channel; `user_input` added to Literal | `arf/core/events.py` |
| 13 | (fixed 2026-05-25) `CompactionConfig.strategy` — removed `"summarization"` from Literal | `arf/core/config_base.py` |
| 14 | (fixed 2026-05-25) README concurrency description updated — agent loop sequential, tool calls parallel | README overview table |

### Pending Fixes — Docs

*Confirmed 2026-05-25 via cross-referencing docs vs code.*

| # | Issue | Location |
|---|-------|----------|
| D1 | (fixed 2026-05-25) Event type count unified across docs; `user_input` added to EventType Literal | `arf/core/events.py`, `docs/trace.md`, README |
| D2 | (fixed 2026-05-25) `ResourceCache` now wired — doc description matches actual architecture | `docs/resource-registry.md` |
| D3 | Line counts in 16 locations off by 1 (tool_provider, skill_provider, etc.) and `pipeline.py` off by 45 | 7 design docs |
| D4 | (fixed 2026-05-25) `ReloadConfig.watch` default now `True`, `poll_interval` added to model | `arf/core/config_base.py` |
| D5 | (fixed 2026-05-25) Summarizer code location corrected to `base.py:186-214` | `docs/memory-management.md` |
| D6 | Dual-agent architecture described as working in README but multi-agent scheduler not wired | README Part II |
| D7 | (fixed 2026-05-25) App README tool count corrected from 14 to 15 | `app/arf_default_assistant/README.md` |
| D8 | (fixed 2026-05-25) Hook execution order doc corrected — `asyncio.gather` parallel, not sequential | `docs/app/hooks.md` |
| D9 | (fixed 2026-05-25) agent.yaml `models:` section removed — filesystem is sole source of truth | `app/arf_default_assistant/agent.yaml` |

[Full fact-check report →](docs/fact-check-2026-05-25.md)

### Evolution Directions

*See Chapter 3 of each design doc for details.*

| Module | Doc | Directions |
|--------|-----|------------|
| Memory | [memory-management.md](docs/memory-management.md) | Semantic-unit retrieval · knowledge graph index · prefetch · hot/cold tiering · memory decay |
| Model Routing | [model-routing.md](docs/model-routing.md) | Three-tier classifier · continuous load tracking · model-as-hardware |
| Tool Sandbox | [tool-sandbox.md](docs/tool-sandbox.md) | Per-invocation sandbox · MCP protocol · approval channel · recursive param checking |
| Resource Discovery | [resource-registry.md](docs/resource-registry.md) | Hierarchical override merge · MCP multi-source · cross-reference validation · resource versioning |
| Concurrency | [skill-pipeline.md](docs/skill-pipeline.md) | Multi-agent DAG scheduling · worktree isolation · transactional file ops |
| Interrupt | [interrupt.md](docs/interrupt.md) | Pause/resume · persistent checkpoints · idle timeout · interrupt priority |
| Trace | [trace.md](docs/trace.md) | SQLite trace DB · OpenTelemetry export · real-time alerts · performance profiling |

### App/Framework Boundary

*Capabilities that should move from app to framework layer.*

| # | Issue | Current (App) | Target (Framework) |
|---|-------|---------------|-------------------|
| B1 | (fixed 2026-05-25) Disk state store | `FileStateStore` as default, JSON per session | ✅ Done |
| B2 | FileWatcher lifecycle manual | `server.py` calls `_agent._file_watcher.start/stop` | Framework auto-starts with engine |
| B3 | API key management | `server.py` `.env` parser, validator, cache, register endpoint | Framework key store + validation |
| B4 | Session management | `server.py` `_active_cancel_events`, session stubs | Framework session manager |
| B5 | Trace API endpoints | `server.py` `/api/trace/*` routes | Framework FastAPI router |
| B6 | SSE event translation | `server.py` `_sse_chat()` 139-line event translator | Framework SSE adapter |

<br/>

## Framework Dev / App Building

**Building apps on ARF**: See the [App Developer Guide](./贡献者须知.md) — start from a minimal `agent.yaml`, configure models/tools/skills/hooks, launch the server.

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

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
