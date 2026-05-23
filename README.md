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
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.10+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.10+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">Harness = OS Kernel. Model = CPU. Agent = Computer.</h3>
<p align="center">Local-first. Filesystem-native. Convention over configuration. Fully traceable. Self-evolving.</p>

<br/>

> **Built by DeepSeek V4 Pro & Claude Code.** The author provided design direction and code review only — not a single line was manually written. If you still doubt whether Agent Harness works, this project is the proof.

<br/>

## Design Philosophy

A model is raw compute — powerful, but not a computer. It needs memory management, process scheduling, interrupt handling, a file system, and security boundaries. ARF provides those. It is an **agent framework** built on a single architectural insight: **the Harness layer is the kernel of AI-native computing**.

The primitives of operating systems — virtual memory, cache hierarchies, system calls, protection rings — map directly onto the problems every agent engineer faces. ARF does not invent new abstractions. It adapts proven OS patterns to the token era.

<br/>

## Vision

ARF is a **breeding ground for self-growing agents**. Every agent spawned under this framework is an autonomous individual that grows within its task domain. They share the same foundation — **resource perception and utilization** — yet develop distinct techniques and behavioral emphases shaped by their specific scenarios.

Each task domain runs as a closed loop:

```
Perceive → Reason → Act → Verify → Perceive ...
```

Agents iterate, converging toward **local optima** within their domains. When a cluster of such specialists collaborates, the system exhibits capabilities beyond any individual. **Emergence happens. Generalization follows.**

This is the path: not a monolithic super-model, but a **society of self-growing agents** — scaffolding from specialized competence toward general intelligence.

<br/>

## Harness as Kernel — Problem-Domain Architecture

> **Model + Harness = Agent. CPU + Kernel = Computer.**
>
> The Harness is the kernel of AI-native computing. Token is the instruction. Agent session is the process. Tool call is the system call. Every hard problem in agent engineering has a mature counterpart in operating systems. We adapt, not invent.

The following table structures the entire Harness problem space by domain, mapping each to its OS counterpart, ARF's current implementation, and the planned evolution path.

| Problem | OS Solution | Current (MVP) | Evolution |
|---------|-------------|---------------|-----------|
| **Memory management (OOM + persistence)** | Virtual memory + file system: page swapping, persistent storage | Token-aware sliding window compaction at 75% threshold, LLM summarization of evicted turns. Automatic fact/preference/decision extraction with dedup, semantic retrieval injected as working set. [Compaction →](docs/compaction.md) [Memory →](docs/memory-pipeline.md) | Fine-grained page fault: semantic-unit retrieval; agent-maintained knowledge graph with path-based + semantic access |
| **Model routing & resource allocation** | Multi-level cache (L1/L2) + big.LITTLE heterogeneous scheduling | Two-tier LLM classifier routes user tasks by complexity (medium→quick, complex→deep). Dedicated model handles framework background work (memory extraction, classification). Per-turn dynamic switching. [Design doc →](docs/model-routing.md) | Hardware-aware dispatch: task complexity × latency budget × KV cache occupancy; same-session transparent model switching |
| **External capability invocation & tool sandbox** | System calls + coprocessors + process isolation (chroot, containers): CPU invokes accelerator via interrupt, each syscall gates through kernel permission check | Tool calling: `tool.yaml` (JSON Schema) + `function.py` per tool, loaded via dual-source registry. Hook exit-code contract (0=continue, 1=block, 2=inject). Path sandbox prevents workspace escape. Tools execute in-process; no per-invocation sandbox or deny→ask→allow permission pipeline. MCP protocol in roadmap. | Per-tool deny→ask→allow permission gating. Each invocation runs in isolated sandbox with resource quotas (CPU, memory, network). Hardware-accelerated dispatch with DMA-style async I/O for high-frequency tools |
| **Task parallelism & concurrency** | Superscalar/out-of-order execution + multi-core: independent instructions issued in parallel | Sequential two-phase dispatch: UserAgent → SysAgent handoff for privileged operations. Hooks execute in thread pool with parallel subprocess isolation. No multi-agent concurrent execution yet. | Multi-agent pipeline optimization: automatic task dependency graph analysis; worktree isolation for independent subtasks; dynamic parallelism tuning; inter-agent pipeline depth and throughput optimization |
| **External interrupt & user intervention** | Hardware interrupt: save state → ISR → restore state | User can abort streaming mid-response (`AbortController`). Hook inject via exit code 2: engine inserts `[Hook message]` into conversation and continues loop. No mid-stream user injection; no session idle timeout implemented. | Agent signal standardization: universal interrupt vectors (pause / redirect / undo / inject); keyboard and voice multi-modal real-time interruption without session restart |
| **Resource deadlock & contention** | Resource allocation graph + detection/avoidance: lock hierarchy, timeout rollback | Sequential agent execution avoids concurrency by design. File editing serialized through `file_writer` with workspace-scoped path sandbox (no traversal). No explicit deadlock detection; no multi-agent shared resource protocols. | Agent concurrency control: distributed lock manager (DLM) for shared resources; optimistic concurrency; automatic deadlock detection and cycle breaking between agents |
| **Identity, permissions & security boundaries** | Protection rings (Ring 0–3) + ACL: kernel mode has full privilege, user mode restricted | Dual-source isolation: system resources read-only (Ring 0), user workspace read-write (Ring 3). Path sandbox prevents workspace traversal. UserAgent restricted from writing to `tools/`, `skills/`, `models/` directories. No user-in-the-loop approval flow for sensitive operations. | Least-privilege auto-derivation: agent granted minimal toolset and data view for current task; permission scope expands/contracts dynamically with task phase; human-in-the-loop approval for high-risk tool calls |

<br/>

## Architecture

ARF decouples **what** (control) from **how** (execution). The filesystem is the bridge.

```
┌─────────────────────────────────────────────────────┐
│              CONTROL LAYER (declarative)              │
│  models/    tools/    skills/    arf_agent.yaml      │
│  (YAML — human-readable, git-trackable)              │
└──────────────────────┬──────────────────────────────┘
                       │  filesystem discovery
┌──────────────────────┴──────────────────────────────┐
│              EXECUTION LAYER (LangGraph)              │
│  ResourceRegistry → UserAgent/SysAgent → GraphEngine │
│  classify → compact → call_model → execute_tools     │
│  FastAPI + WebSocket + SSE → Vue 3 frontend          │
└─────────────────────────────────────────────────────┘
```

Key engineering choices:

- **Dual-source resources** — system resources ship with the framework (read-only, 18 tools + 18 skills). User resources live in the workspace (read-write, override by name). Framework upgrades never clobber customizations.
- **Self-evolution** — the agent scaffolds, writes, and registers new tools and skills at runtime. A conversation can produce a permanent capability.
- **Subprocess hooks** — six lifecycle events, exit-code contract (0 = continue, 1 = block, 2 = inject). Hooks run as independent processes with their own timeout and failure domain.
- **Hot reload** — file watcher detects resource changes; registry updates without restart.

### Framework vs. Application

| Layer | Scope | Examples |
|-------|-------|----------|
| **Framework** | Conventions, engine, resource system, trace infrastructure | `ResourceRegistry`, `GraphEngine`, dual-source loading, hook exit-code contract, SQLite trace schema, prompt pipeline |
| **Reference App** | A concrete agent built on the framework | Vue 3 frontend, session sidebar, model routing, `session_archiver`, `title_generator` |
| **User workspace** | What you build on top | Model configs, custom tools, `long_term.md`, workspace YAML |

**The multi-session sidebar is a reference implementation detail**, not a framework constraint. The framework provides `SessionManager` as a building block and makes no further prescription.

<br/>

## Trace System

| Trace Event | Records |
|-------------|---------|
| `lifecycle.session_start` | Workspace, transport, timestamp |
| `lifecycle.session_end` | Message count, duration, trigger |
| `lifecycle.prompt_snapshot` | Prompt hash, length, active tools |
| `lifecycle.hook_execution` | Hook name, event, exit code, stdout/stderr |
| `lifecycle.init` | Registry counts, agent build params |
| `lifecycle.config` | Agent rebuild triggers |

| Node | Recorded |
|------|----------|
| `classify` | Classification (`medium`/`complex`), resolved model |
| `call_model` | Model, tokens, I/O snippets, latency |
| `execute_tools` | Tool name, category, I/O snippets, latency |
| `hook` | Event type, exit status, hook name |
| `respond` | Response snippet, truncation flag |

Waterfall viewer at `/traces` — each turn as a time-proportional block, expandable to token counts, I/O snippets, and tool execution detail.

<br/>

## Quick Start

Requires Python ≥ 3.10 and Node.js ≥ 18.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && cd ..

arf init my_workspace
arf start --workspace my_workspace
```

Browser opens at **http://localhost:5173** — enter your API key and start.

### CLI Reference

| Command | Purpose |
|---------|---------|
| `arf init <name>` | Create a new workspace |
| `arf start` | Launch backend + frontend |
| `arf web` | Backend only (FastAPI + WebSocket + SSE) |
| `arf stop` | Stop running processes |
| `arf reload` | Stop + restart |
| `arf list [tools\|skills\|models]` | List registered resources |
| `arf validate` | Check workspace resource integrity |
| `arf clone <type> <name>` | Copy a system resource to workspace for customization |

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARF_SERVE_STATIC` | `1` | Serve frontend from backend |
| `ARF_DB_NAME` | `arf.db` | SQLite trace database |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS allowed origins |
| `ARF_IDLE_TIMEOUT` | `600` | Session idle timeout (seconds) |
| `ARF_CLASSIFIER_ENABLED` | `0` | Auto model routing (set `1` to activate) |

Model config: `models/<name>/config.yaml` — `base_url`, `api_key`, `model_name`, `temperature`, etc.

<br/>

## Reference App Design

The following patterns are implementation details of the reference app (`app/arf_default_assistant/`), not framework constraints.

### Convention over Configuration

Four entity types — **model**, **tool**, **skill**, **hook** — each following a predictable directory convention. The framework discovers; you don't register. No decorators. No base classes. No import hooks. A tool is two files: `tool.yaml` for the schema, `function.py` for the logic. That's the entire API surface.

### Progressive Disclosure

The agent doesn't blast every capability into every API call. Nine kernel tools (~800 tokens) are always active. Everything else loads on demand via `resource_loader`, runs, and deactivates. Long tool outputs land on disk with a summary in context. The agent pays only for what it actually uses. This is **context engineering** applied systematically — not an afterthought, but architecture.

### Fully Traceable

Every model call, every tool execution, every hook invocation — recorded. Trace is a first-class subsystem, not a log file bolted on.

- **6-table SQLite trace database** — session lifecycle, model calls (tokens, latency, snippets), tool I/O, hook exit codes, prompt snapshots, graph node transitions
- **Waterfall visualization** — each turn rendered as a time-proportional cascade of classify → compact → call_model → execute_tools → respond
- **Session archives** — complete conversation + trace + usage stats as portable JSON

### Single-User, Self-Hosted Dual-Agent

User Agent handles your tasks. System Agent handles internal operations — memory extraction, title generation, error recovery. Separate execution, shared workspace. The user sees one assistant; the dual architecture is an implementation detail that raises reliability without adding cognitive load.

<br/>

## Contributing

See [贡献者须知.md](./贡献者须知.md) for the contributor guide.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
```

**Core stack:** Python 3.10+ · FastAPI · LangGraph · Vue 3 · TypeScript · Vite · SQLite

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>Built with LangGraph, FastAPI, and Vue 3</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
