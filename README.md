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

<h3 align="center">Everything between the model and the user — resource system, agent engine,<br/>observability infrastructure — in a single, self-hosted directory.</h3>
<p align="center">Local-first. Filesystem-native. Convention over configuration. Fully traceable. Self-evolving.</p>

<br/>

## Design Philosophy

A model alone is not an agent. It needs tools, memory, skills — a runtime that orchestrates reasoning, action, and verification. ARF provides that layer. It is an **agent framework** built on a single conviction: the filesystem is the right abstraction for agent resource management.

### 本地优先，基于文件系统

Everything lives in a workspace directory. Models, tools, skills, memory, session archives — all files on disk. No cloud SaaS. No managed database. No telemetry. Your configuration is YAML, your version control is Git, your history is grep-able.

```
my_workspace/
├── arf_agent.yaml          # agent config
├── models/                 # model definitions (endpoints, credentials, parameters)
├── tools/                  # custom tools
├── skills/                 # reusable prompt + tool orchestration templates
├── memory/
│   ├── session.md          # short-term context
│   ├── long_term.md        # persistent profile & facts
│   └── sessions/           # archived sessions with full trace
└── .hooks.json             # lifecycle hook definitions
```

### 约定大于配置

Four entity types — **model**, **tool**, **skill**, **hook** — each following a predictable directory convention. The framework discovers; you don't register. No decorators. No base classes. No import hooks. A tool is two files: `tool.yaml` for the schema, `function.py` for the logic. That's the entire API surface.

### 渐进式披露

The agent doesn't blast every capability into every API call. Nine kernel tools (~800 tokens) are always active. Everything else loads on demand via `resource_loader`, runs, and deactivates. Long tool outputs land on disk with a summary in context. The agent pays only for what it actually uses. This is **context engineering** applied systematically — not an afterthought, but architecture.

### 可追踪可回溯

Every model call, every tool execution, every hook invocation — recorded. Trace is a first-class subsystem, not a log file bolted on.

- **6-table SQLite trace database** — session lifecycle, model calls (tokens, latency, snippets), tool I/O, hook exit codes, prompt snapshots, graph node transitions
- **Waterfall visualization** — each turn rendered as a time-proportional cascade of classify → compact → call_model → execute_tools → respond
- **Session archives** — complete conversation + trace + usage stats as portable JSON

Trace answers: *What did the agent do? Why this model? How long did each step take? What did it cost?*

### 单用户自持的双Agent智能体

User Agent handles your tasks. System Agent handles internal operations — memory extraction, title generation, error recovery. Separate execution, shared workspace. The user sees one assistant; the dual architecture is an implementation detail that raises reliability without adding cognitive load.

<br/>

## Vision

ARF is a **breeding ground for self-growing agents**. Every agent spawned under this framework is an autonomous individual that grows within its task domain. They share the same foundation — **resource perception and utilization** — yet develop distinct techniques and behavioral emphases shaped by their specific scenarios.

Each task domain runs as a closed loop:

```
感知 (Perceive) → 思考 (Reason) → 行动 (Act) → 验证 (Verify) → 感知 ...
```

Agents iterate, converging toward **local optima** within their domains. When a cluster of such specialists collaborates, the system exhibits capabilities beyond any individual. **Emergence happens. Generalization follows.**

This is the path: not a monolithic super-model, but a **society of self-growing agents** — scaffolding from specialized competence toward general intelligence. ARF provides the runtime, the resource system, and the trace infrastructure for that society to form.

<br/>

## Framework vs. Application

ARF is a **framework** — a set of conventions, a resource system, a graph engine, and an observability layer. What ships today is the **reference application**: a single-user chat assistant with a Vue 3 frontend.

| Layer | Scope | Examples |
|-------|-------|----------|
| **Framework** | Conventions, engine, resource system, trace infrastructure | `ResourceRegistry`, `GraphEngine`, dual-source loading, hook exit-code contract, SQLite trace schema, prompt pipeline |
| **Reference App** | A concrete agent built on the framework | Vue 3 frontend, session sidebar, model routing, `session_archiver`, `title_generator` |
| **User workspace** | What you build on top | Model configs, custom tools, `long_term.md`, workspace YAML |

The reference app demonstrates capability. You can build something entirely different — a CLI tool, a headless automation agent, a code review bot — using the same conventions, without touching the frontend.

**The multi-session sidebar in the current app is a reference implementation detail**, not a framework constraint. The framework provides `SessionManager` as a building block and makes no further prescription.

<br/>

## Quick Start

Requires Python ≥ 3.10 and Node.js ≥ 18.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

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
- **Subprocess hooks** — six lifecycle events, exit-code contract (0 = continue, 1 = block, 2 = inject). Hooks run as independent processes with their own timeout and failure domain. A crashed hook cannot bring down the agent.
- **Hot reload** — file watcher detects resource changes; registry updates without restart.

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

Per-turn, per-node traces:

| Node | Recorded |
|------|----------|
| `classify` | Classification (`medium`/`complex`), resolved model |
| `call_model` | Model, tokens (prompt/completion/total), I/O snippets, latency |
| `execute_tools` | Tool name, category, input/output snippets, latency |
| `hook` | Event type, exit status, hook name |
| `respond` | Response snippet, truncation flag |

Waterfall viewer at `/traces` — each turn as a time-proportional block, expandable to token counts, I/O snippets, and tool execution detail.

<br/>

## Roadmap

| Priority | Item |
|----------|------|
| **P0** | **Infinite-context single-session experience** — deprecate multi-session sidebar. The user never sees a session list. Conversations flow continuously; archiving and compaction happen transparently. |
| **P0** | Sandbox runtime for tool execution |
| **P1** | `arf chat` — interactive CLI |
| **P1** | `arf run` — headless batch execution |
| **P2** | Plugin/extension system |
| **P2** | MCP (Model Context Protocol) support |
| **P3** | Tool approval flow — human-in-the-loop for sensitive operations |

<br/>

## Contributing

See [贡献者须知.md](./贡献者须知.md) for the contributor guide.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && npm run dev
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
