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

<h3 align="center">A self-hosted, dual-agent AI assistant that evolves with you.</h3>
<p align="center">Local-first. Filesystem-native. Convention over configuration. Fully traceable.</p>

<br/>

## Design Philosophy

ARF is a **single-user, self-hosted dual-agent framework**. It runs on your machine, stores everything as files, and leaves a complete trace of every decision.

### 本地优先，基于文件系统

Everything lives in a workspace directory. Models, tools, skills, memory, session archives — all files. No cloud SaaS, no managed database, no telemetry. Git-native: your entire agent configuration and history is version-controllable.

```
my_workspace/
├── arf_agent.yaml          # agent config
├── models/                 # model definitions (API endpoints, credentials)
├── tools/                  # custom tools
├── skills/                 # reusable prompt + tool templates
├── memory/
│   ├── session.md          # short-term context
│   ├── long_term.md        # persistent profile & facts
│   └── sessions/           # archived sessions with full trace
└── .hooks.json             # lifecycle hook definitions
```

### 约定大于配置

Four entity types govern the entire framework: **model**, **tool**, **skill**, **hook**. Each follows a predictable directory convention. The framework discovers; you don't register. No decorators, no base classes, no import hooks. A tool is a directory with two files — `tool.yaml` (schema) and `function.py` (implementation). That's it.

### 渐进式披露

The agent doesn't dump every capability into every API call. 9 kernel tools (~800 tokens) are always active. Everything else is loaded on demand via `resource_loader`, used, and deactivated. Long tool results are saved to disk with a summary in context. The agent only pays for what it actually uses.

### 可追踪可回溯

Every conversation, every model call, every tool execution, every hook invocation — recorded. ARF's trace system is not an afterthought; it's a first-class design feature.

- **6-table SQLite trace database** captures the full lifecycle: session start/end, model calls (tokens, latency, snippets), tool executions (input/output), hook runs (exit codes, stdout/stderr), prompt snapshots, and graph node transitions
- **Waterfall visualization** in the frontend renders each turn as a time-proportional cascade of classify → compact → call_model → execute_tools → respond
- **Session archives** persist complete conversation history with traces and usage stats as JSON files, greppable and portable

Trace data answers the questions that matter: *What did the agent do? Why did it choose that model? How long did each step take? What tokens were consumed?*

### 单用户自持的双Agent智能体

ARF presents as a two-agent system: a **User Agent** handles your tasks directly; a **System Agent** handles internal operations (memory extraction, title generation, error recovery). They share the same workspace but operate independently. The user sees a single, coherent assistant — the dual-agent architecture is an implementation detail that improves reliability without adding cognitive overhead.

<br/>

## Framework vs. MVP

ARF is a **framework** — a set of conventions, a resource system, a graph engine, and an observability layer. What ships today is the **MVP application** built on top of it: a single-user chat assistant with a Vue 3 frontend.

| Layer | What it is | Examples |
|-------|-----------|----------|
| **Framework** | Conventions, engine, resource system, trace infrastructure | `ResourceRegistry`, `GraphEngine`, dual-source resource loading, hook exit-code contract, SQLite trace schema, prompt pipeline |
| **MVP App** | A concrete chat application built on the framework | Vue 3 frontend, session sidebar, multi-model routing, `session_archiver` hook, `title_generator` hook |
| **User workspace** | Your models, tools, skills, memory — what you build on top | Model configs, custom tools, `long_term.md`, workspace YAML |

The MVP demonstrates what the framework can do. You can build something entirely different — a CLI tool, a headless automation agent, a code review bot — using the same framework conventions without touching the frontend at all.

**The current MVP has a multi-session sidebar.** This is an MVP implementation detail, not a framework requirement. The framework itself has no opinion on session management — it provides `SessionManager` as a building block.

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

Browser opens at **http://localhost:5173** — enter your API key and start chatting.

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
| `ARF_DB_NAME` | `arf.db` | SQLite trace database filename |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS allowed origins |
| `ARF_IDLE_TIMEOUT` | `600` | Session idle timeout (seconds) |
| `ARF_CLASSIFIER_ENABLED` | `0` | Auto model routing (set `1` to activate) |

Model config lives in `models/<name>/config.yaml` — `base_url`, `api_key`, `model_name`, `temperature`, etc.

<br/>

## Architecture

ARF decouples **what** (control layer) from **how** (execution layer). The filesystem bridges them.

```
┌─────────────────────────────────────────────────────┐
│              CONTROL LAYER (declarative)              │
│  models/    tools/    skills/    arf_agent.yaml      │
│  (YAML definitions — human-readable, git-trackable)  │
└──────────────────────┬──────────────────────────────┘
                       │  filesystem discovery
┌──────────────────────┴──────────────────────────────┐
│              EXECUTION LAYER (LangGraph)              │
│  ResourceRegistry → UserAgent/SysAgent → GraphEngine │
│  classify → compact → call_model → execute_tools     │
│  FastAPI + WebSocket + SSE → Vue 3 frontend          │
└─────────────────────────────────────────────────────┘
```

**Dual-source resources:** system resources ship with the framework (read-only, 18 tools, 18 skills). User resources live in the workspace (read-write, override system by name). Upgrades never clobber customizations.

**Self-evolution:** the agent can scaffold, write, and register new tools and skills at runtime. A conversation can produce a permanent capability.

**Hook system:** subprocess-based lifecycle hooks (6 events) with exit-code contract (0 = continue, 1 = block, 2 = inject). A crashed hook cannot bring down the agent.

**Hot reload:** file watcher detects resource changes, registry updates without restart.

<br/>

## Trace System

Trace is the backbone of ARF's observability. Every lifecycle event is captured.

| Trace Event | Records |
|-------------|---------|
| `lifecycle.session_start` | Workspace, transport, timestamp |
| `lifecycle.session_end` | Message count, duration, trigger |
| `lifecycle.prompt_snapshot` | Prompt hash, length, active tools list |
| `lifecycle.hook_execution` | Hook name, event, exit code, stdout/stderr |
| `lifecycle.init` | Registry load counts, agent build params |
| `lifecycle.config` | Agent rebuild triggers |

**Graph node traces** (per turn, per node):

| Node | What's Recorded |
|------|----------------|
| `classify` | Classification result (`medium`/`complex`), resolved model |
| `call_model` | Model name, tokens (prompt/completion/total), I/O snippets, duration |
| `execute_tools` | Tool name, category, input/output snippets, duration |
| `hook` | Hook event type, exit status, hook name |
| `respond` | Response snippet, truncation flag |

**Frontend trace viewer** (`/traces`): waterfall timeline showing each turn as a time-proportional block sequence, expandable to reveal token counts, model I/O snippets, and tool execution details.

<br/>

## TODO

The framework's next milestone: **single-user, infinite-context Agent** — the MVP application evolves from a chat tool with session management into a seamless, continuous conversation experience.

| Priority | Item |
|----------|------|
| **P0** | **Infinite-context single-session experience** — deprecate multi-session sidebar, resume/delete buttons, and session-switching UX. The user never sees a "session list." Conversations flow continuously; archiving and context compaction happen transparently in the background. This is the natural expression of the framework's design philosophy: the framework provides the engine, the application becomes invisible. |
| **P0** | Sandbox runtime — security isolation for tool execution |
| **P1** | `arf chat` — interactive CLI chat |
| **P1** | `arf run` — headless batch execution mode |
| **P2** | Plugin/extension system — third-party resource packages |
| **P2** | MCP (Model Context Protocol) support |
| **P3** | Tool approval flow — user-in-the-loop for sensitive operations |

<br/>

## Contributing

See [贡献者须知.md](./贡献者须知.md) for the contributor guide.

```bash
# Development setup
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
