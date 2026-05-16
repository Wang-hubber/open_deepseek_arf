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

<h3 align="center">Filesystem-native, self-evolving AI agent framework.</h3>
<p align="center">Execution and control, decoupled by design. Convention over configuration. Progressive disclosure of capabilities. Out-of-the-box and ready to evolve.</p>

<br/>

## Design Philosophy

ARF is not yet another AI agent framework that layers abstraction upon abstraction. It is built on a single insight: **the file system is the ideal medium for agent resource management.** Directories are namespaces, YAML files are declarations, Python files are implementations — no database, no registry server, no UI-driven configuration wizard.

### Core Principles

**1. Execution Layer and Control Layer — Decoupled by the File System**

The control layer (WHAT) is pure declaration: YAML files describe what resources exist, their schemas, and when they should be used. The execution layer (HOW) is pure logic: a LangGraph engine that dynamically loads and invokes functions at runtime. The file system bridges them — edit a YAML, save, and it's live. No restart, no recompile, no deploy step.

- **Control layer** — human-readable, git-trackable YAML configs (`tool.yaml`, `skill.yaml`, `config.yaml`)
- **Execution layer** — LangGraph StateGraph with dependency injection, completely agnostic to specific resources
- **ARFAgent orchestrator** — reads from the filesystem registry, dispatches to the engine, adapts instantly via hot-reload

**2. Filesystem-Native, Self-Evolving**

ARF is not just configurable — it can evolve itself at runtime.

- `arf init` creates directories. No database migrations ever.
- The agent uses `resource_scaffold` + `file_writer` to create new tools and skills during a conversation. A chat session can produce a permanent capability.
- Memory is files: `session.md` (short-term), `long_term.md` (persistent profile), `sessions/*.json` (archives). Grep-able, backup-able, transparent.
- Dual-source resources: system resources ship with the framework (read-only), user resources live in the workspace (read-write, override system by name). Upgrades never clobber customizations.

**3. Convention Over Configuration — Four Entity Types**

Every resource follows the same minimal convention. The framework discovers; you don't register.

| Entity | Definition | Purpose |
|--------|-----------|---------|
| **Model** | `models/<name>/config.yaml` | API endpoint, credentials, parameters |
| **Tool** | `tools/<name>/tool.yaml` + `function.py` | Callable capability with JSON Schema |
| **Skill** | `skills/<name>/skill.yaml` | Reusable prompt + tool orchestration template |
| **Hook** | `.hooks.json` → subprocess script | Lifecycle event interceptor |

No decorators. No base classes. No `__init__.py`. No import hooks. A tool is a directory with two files. A model is a directory with one file. That's the entire taxonomy.

**4. Progressive Disclosure — ~800 Token System Prompt**

The agent only pays for context it actually uses.

- **Kernel layer** (9 tools, always active): file operations, resource loading, memory, model management, hook management
- **Discoverable layer** (everything else): loaded on demand via `resource_loader`, deactivated when done
- **Skill layer**: prompt templates that orchestrate tools — loaded when the agent or user invokes them

No dumping 50+ tool definitions into every API call. The initial system prompt is ~800 tokens.

**5. One Default Per Feature**

One `web_fetch`, not three HTTP clients. One `memory_store`, not five backends. One model adapter (OpenAI-compatible API), not a multi-provider abstraction. This reduces choice fatigue, maintenance burden, and bug surface. Need something different? `arf clone` the default and customize it.

**6. Self-Hosted, Git-Native, No Vendor Lock-In**

No cloud SaaS. No managed service. No telemetry. Your workspace is a directory. Your config is YAML. Your version control is git. Deploy anywhere that runs Python — single process, Docker, or your own infrastructure.

### Feature Overview

| Capability | Implementation |
|------------|---------------|
| **Agent Engine** | LangGraph StateGraph with compact → classify → call_model → execute_tools/respond → recovery |
| **Model Routing** | Two-tier classifier (medium/complex): quick_thinking ↔ deep_thinking auto-switch |
| **Context Compaction** | Automatic sliding-window + summary when context exceeds 75% of 1M window |
| **Progressive Disclosure** | Long tool results saved to disk, context shows summary + file pointer |
| **API Server** | FastAPI + WebSocket + SSE streaming |
| **Frontend** | Vue 3 + TypeScript + Vite (6400+ lines, 8 views, 13 components) |
| **Observability** | SQLite trace database (6 tables) with waterfall visualization |
| **Memory System** | Three-layer: session → long-term → archive, with auto-extraction and compression |
| **Hook Engine** | Subprocess-based lifecycle hooks (6 events, 4 built-in) with exit-code contract |
| **Self-Evolution** | Agent can scaffold, write, and register new tools/skills at runtime |
| **Hot Reload** | File watcher detects resource changes, registry updates without restart |
| **Docker Support** | Multi-stage Dockerfile + docker-compose |
| **Cross-Platform** | Windows + Linux support, UTF-8 file I/O, Vite auto-install |

<br/>

## Quick Start

Requires Python ≥ 3.10 and Node.js ≥ 18.

```bash
# Clone and install
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

# Create a workspace and start (backend + frontend together)
arf init my_workspace
arf start --workspace my_workspace
```

Browser opens at **http://localhost:5173** — enter your DeepSeek API key and start chatting. The key is saved to `models/<name>/config.yaml` in your workspace.

### CLI Reference

| Command | Purpose |
|---------|---------|
| `arf init <name>` | Create a new workspace. **Start here.** |
| `arf start` | Launch backend + frontend together (recommended). |
| `arf web` | Launch the web server only (FastAPI + WebSocket + SSE). |
| `arf stop` | Stop running backend and frontend processes. |
| `arf reload` | Stop + restart, preserving configuration. |
| `arf list [tools\|skills\|models]` | List registered resources. `[sys]` = framework built-in. |
| `arf validate` | Check workspace resource integrity. |
| `arf clone <type> <name>` | Copy a system resource to your workspace for customization. |
| `arf chat` | Interactive chat CLI (stub). |
| `arf run` | Headless script execution (stub). |

### Workspace Structure

```
my_workspace/
├── arf_agent.yaml          # workspace config (agent name, model, max_turns, preload)
├── .arf/                   # runtime state (PID files, run config)
├── .hooks.json             # lifecycle hook definitions
├── models/                 # user model configs
│   └── deep_thinking/
│       └── config.yaml     # base_url, api_key, model_name, temperature...
├── tools/                  # user custom tools
├── skills/                 # user custom skills
├── memory/                 # memory system
│   ├── session.md          # short-term session context
│   ├── long_term.md        # persistent user profile & facts
│   └── sessions/           # archived session JSON with traces
└── .git/                   # initialize yourself: git init && git add -A
```

### Configuration

**Environment variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARF_SERVE_STATIC` | `1` | Serve built frontend from backend. Set `0` for dev proxy. |
| `ARF_DB_NAME` | `arf.db` | SQLite database filename |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS allowed origins, comma-separated |
| `ARF_IDLE_TIMEOUT` | `600` | Session idle timeout in seconds |
| `ARF_API_MAX_RETRIES` | `3` | Max API call retries |
| `ARF_API_RETRY_BACKOFF` | `1.5` | Retry backoff base in seconds |

| `ARF_CLASSIFIER_ENABLED` | `0` | Enable auto model routing (set `1` to activate) |
| `ARF_DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Base URL for one-click DeepSeek config |

**Model config (`models/<name>/config.yaml`):**

```yaml
name: deep_thinking
model_type: deep_thinking
context_window: 1048576   # 1M tokens
config:
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."
  model_name: "deepseek-v4-pro"
  temperature: 0.7
  max_tokens: 100000
  thinking_enabled: true
  reasoning_effort: "max"
```

**Classifier config (`arf_agent.yaml`):**

```yaml
agent:
  name: "My Workspace"
  model: "quick_thinking"
  max_turns: 10
  max_tool_result_chars: 2000  # truncate long tool results
  context_window: 1048576      # 1M token context window
  classifier_enabled: true     # auto-route tasks by complexity

resources:
  preload: []                  # tools to activate at session start
```

<br/>

## How ARF Compares

ARF occupies a distinct position in the AI agent landscape: it is neither a library nor a low-code platform, but a **workspace-as-code framework** where the file system is the source of truth.

| | ARF | LangChain | Dify | Raw FastAPI + SDK |
|---|---|---|---|---|
| **Approach** | Workspace-as-code | Library | Low-code platform | Do-it-yourself |
| **Agent engine** | LangGraph StateGraph | LangGraph | Custom | You build it |
| **Resource model** | File system directories | Python classes | Web UI forms | N/A |
| **Self-evolution** | Yes — agent creates resources at runtime | Manual | Partial (plugin store) | Manual |
| **Hot-reload** | Yes (built-in file watcher) | Manual | Partial | Manual |
| **Progressive disclosure** | Yes — 9 kernel tools, ~800 tokens | No (full tool list) | Partial | Manual |
| **Context efficiency** | On-demand tool activation/deactivation | All tools always in prompt | Limited | Up to you |
| **Frontend** | Vue 3 + TypeScript (built-in) | None (LangServe) | React | You build it |
| **Trace observability** | SQLite + waterfall visualization | LangSmith (paid) | Built-in | You build it |
| **Memory system** | Three-layer file-based (session/long-term/archive) | Limited | Limited | You build it |
| **Hook system** | Subprocess-based, 6 lifecycle events | Callbacks | Limited | You build it |
| **Vendor lock-in** | None — git-native | LangChain ecosystem | Dify platform | None |
| **Self-hosted** | Yes, single process | Yes | Yes (Docker) | Yes |
| **Open source** | MIT | MIT | Apache 2 | N/A |

### When to Choose ARF

- You want an agent that can **evolve itself** — creating tools and skills from conversation
- You value **git-native workflows** — config as code, not click-ops
- You need **transparent, file-based state** — no black-box databases
- You want **progressive disclosure** — context-efficient agent that doesn't bloat every prompt
- You prefer **convention over configuration** — predictable paths, minimal boilerplate

### When to Choose Alternatives

- You need a **visual workflow builder** → Dify or n8n
- You need **production multi-tenancy** with auth, rate limiting, and billing → Dify or build on LangChain
- You're building a **one-off prototype** and want maximum library ecosystem → LangChain
- You need **fine-grained control** over every aspect and have a dedicated team → Raw FastAPI + SDK

<br/>

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   CONTROL LAYER (WHAT)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │tool.yaml │  │skill.yaml│  │config.yaml│  │arf_agent │  │
│  │(schema,  │  │(prompt,  │  │(model,   │  │ .yaml    │  │
│  │ desc)    │  │ tools)   │  │ params)  │  │(worksp.) │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │              │              │              │       │
│  ┌────┴──────────────┴──────────────┴──────────────┴───┐  │
│  │        File System (the bridge)                       │  │
│  │  system/ (read-only, shipped with package)           │  │
│  │  user/   (read-write, in workspace)                  │  │
│  └─────────────────────────┬────────────────────────────┘  │
└────────────────────────────┼───────────────────────────────┘
                             │
┌────────────────────────────┼───────────────────────────────┐
│                  EXECUTION LAYER (HOW)                      │
│  ┌─────────────────────────┴───────────────────────────┐   │
│  │        ResourceRegistry (dual-source loader)         │   │
│  │   Scans YAML → indexes by name → tracks source      │   │
│  └─────────────────────────┬───────────────────────────┘   │
│                            ▼                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           ARFAgent (Orchestrator)                     │   │
│  │  Prompt Pipeline: workspace → memory → identity →    │   │
│  │  inventory (active tools + skills) → language        │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │        LangGraph Engine (StateGraph)                  │   │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │   │
│  │  │ classify │→│call_model│→│execute_tools/respond│  │   │
│  │  │ (opt.)   │  │          │  │                   │  │   │
│  │  └──────────┘  └────┬─────┘  └───────────────────┘  │   │
│  │                     │recovery/error                  │   │
│  │                     └────────┐                       │   │
│  │              ┌───────────────┘                       │   │
│  │              ▼                                       │   │
│  │  ┌──────────────────────────────────────────────┐    │   │
│  │  │  FastAPI Server + WebSocket + SSE Streaming   │    │   │
│  │  │  REST /api/* | WS /ws | SessionManager       │    │   │
│  │  │  SQLite tracing | Hot-reload file watcher     │    │   │
│  │  └──────────────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **User message** arrives via WebSocket or REST API
2. **SessionManager** prepends system prompt (workspace → memory → identity → inventory → language)
3. **Compact node** checks context usage — if ≥ 75% of 1M window, compresses old turns into a structured summary
4. **Classifier** (optional) analyzes complexity → routes to `quick_thinking` / `deep_thinking`
5. **LangGraph engine** executes the agent loop: compact → call model → parse response → execute tools or respond
6. **Hook runner** fires lifecycle events (PreModelCall, PostToolUse, SessionEnd, etc.) via subprocess
7. **Tracing** records every step to SQLite for observability
8. **SSE stream** pushes tokens, tool calls, and usage data to the frontend in real time

### Memory Architecture

| Layer | Storage | Trigger | Purpose |
|-------|---------|---------|---------|
| **Session** | `memory/session.md` | Every prompt | Current conversation context, injected into system prompt |
| **Long-term** | `memory/long_term.md` | Auto-extract on SessionEnd | User profile, preferences, facts — persists across sessions |
| **Archive** | `memory/sessions/*.json` | Auto-archive on SessionEnd | Structured session data with traces and usage stats |

Memory extraction and compression are fully automatic: `memory_extractor` hook runs after each session, and `memory_compress` skill triggers when long-term memory exceeds 700 KB (configurable). Every long-term memory write creates an automatic backup.

### Hook System

Hooks are subprocess scripts triggered on 6 lifecycle events. They communicate via exit code contract:

| Exit Code | Meaning |
|-----------|---------|
| `0` | Continue (stdout may contain JSON data) |
| `1` | Block the current action (stderr = reason) |
| `2` | Inject a message (stderr = message text) |

| Event | When | Built-in Hooks |
|-------|------|---------------|
| `SessionStart` | New session begins | `system_log` |
| `PreModelCall` | Before each API call | `system_log` |
| `PostModelCall` | After each API response | `system_log` |
| `PreToolUse` | Before tool execution | `system_log` |
| `PostToolUse` | After tool execution | `system_log` |
| `SessionEnd` | Session terminates | `session_archiver`, `memory_extractor`, `system_log` |

Hooks run in parallel via thread pool, each with independent timeout. Config is defined in `.hooks.json`, managed at runtime via the `manage_hooks` tool. Context passes through environment variables (small payloads) and stdin JSON (large payloads like conversation history).

<br/>

## System Resources — Implementation Status

System resources ship with the framework under `src/arf/resources/system/`. They serve as defaults and as templates for user customization via `arf clone`. Each resource follows the convention: a directory named after the resource, containing a YAML definition and (for tools) a `function.py`.

### Models (9 total)

Models define API endpoints, credentials, and inference parameters. Each is a directory under `models/<name>/` with `config_default.yaml` (and optionally `config.yaml` for user credentials).

| Model | Type | Status | Description |
|-------|------|--------|-------------|
| `deep_thinking` | reasoning | ✅ Implemented | Maximum reasoning for complex tasks (architecture, refactoring, creative work) |
| `quick_thinking` | reasoning | ✅ Implemented | Default session model, balanced reasoning (code generation, debugging) |
| `quick_no_thinking` | reasoning | ✅ Implemented | Reserved for background tasks (compression, summaries, title generation) |
| `embedding` | multimodal | 🚧 Config only | Text embedding vector generation |
| `rerank` | multimodal | 🚧 Config only | Search result reranking |
| `vision` | multimodal | 🚧 Config only | Image understanding and analysis |
| `vlm` | multimodal | 🚧 Config only | Vision-language model for multimodal reasoning |
| `tts` | multimodal | 🚧 Config only | Text-to-speech synthesis |
| `stt` | multimodal | 🚧 Config only | Speech-to-text transcription |

Session routing uses a two-tier classifier (medium → `quick_thinking`, complex → `deep_thinking`). `quick_no_thinking` is reserved for background tasks (context compaction, memory extraction, title generation). When a target model is unavailable, the system degrades automatically.

### Tools (16 total)

Tools are callable capabilities. Each is a directory under `tools/<name>/` with `tool.yaml` (JSON Schema parameters), `config_default.yaml` (metadata), and optionally `function.py` (implementation).

**Kernel Tools (always active, ~800 tokens):**

| Tool | Status | Description |
|------|--------|-------------|
| `file_reader` | ✅ Implemented | Read file contents with line range support |
| `file_writer` | ✅ Implemented | Write or overwrite file contents |
| `file_deleter` | ✅ Implemented | Delete files with confirmation |
| `resource_loader` | ✅ Implemented | On-demand tool activation and deactivation |
| `memory_store` | ✅ Implemented | Long-term memory read/write with automatic backup rotation |
| `model_manager` | ✅ Implemented | Model CRUD, connection test, activation switch |
| `model_switch` | ✅ Implemented | Runtime two-tier model hot-switch (quick_thinking ↔ deep_thinking) with degradation |
| `resource_registrar` | ✅ Implemented | Query resource configuration status and dependencies |
| `manage_hooks` | ✅ Implemented | View, enable, disable, add, and remove hook definitions at runtime |

**Discoverable Tools (loaded on demand via `resource_loader`):**

| Tool | Status | Description |
|------|--------|-------------|
| `web_fetch` | ✅ Implemented | Fetch and process web page content |
| `web_search` | 🚧 Config only | Web search integration (endpoint reserved) |
| `image_understanding` | 🚧 Config only | Image analysis and description (endpoint reserved) |
| `ocr` | 🚧 Config only | Optical character recognition (endpoint reserved) |
| `speech_output` | 🚧 Config only | Text-to-speech output (endpoint reserved) |
| `speech_understanding` | 🚧 Config only | Speech-to-text input (endpoint reserved) |
| `video_understanding` | 🚧 Config only | Video content analysis (endpoint reserved) |

### Skills (14 total)

Skills are reusable prompt templates that orchestrate tools for specific workflows. Each is a directory under `skills/<name>/` with `skill.yaml` (prompt template + tool references) and optionally `config_default.yaml` (metadata).

**Memory Skills:**

| Skill | Status | Description |
|-------|--------|-------------|
| `memory_extract` | ✅ Implemented | Extract long-term memories from conversation history |
| `memory_compress` | ✅ Implemented | Compress long-term memory (triggers at 700 KB threshold) |
| `memory_management` | ✅ Implemented | Inspect, search, and manage memory files |

**Model Skills:**

| Skill | Status | Description |
|-------|--------|-------------|
| `model_switch` | ✅ Implemented | Switch model tier at runtime based on task needs |
| `model_manager` | ✅ Implemented | Full model lifecycle management (CRUD, test, configure) |
| `model_configurator` | ✅ Implemented | Interactive step-by-step model configuration wizard |

**Tool Skills:**

| Skill | Status | Description |
|-------|--------|-------------|
| `tool_generator` | ✅ Implemented | Generate new tools from conversation context |
| `tool_manager` | ✅ Implemented | Manage tool lifecycle (activate, deactivate, inspect) |
| `validate_tool` | ✅ Implemented | Validate tool YAML schema and function.py correctness |

**Skill Skills:**

| Skill | Status | Description |
|-------|--------|-------------|
| `skill_generator` | ✅ Implemented | Generate new skills from conversation context |
| `skill_manager` | ✅ Implemented | Manage skill lifecycle and dependencies |

**Infrastructure Skills:**

| Skill | Status | Description |
|-------|--------|-------------|
| `resource_scaffold` | ✅ Implemented | Scaffold proper directory structures for new resources |
| `error_handler` | ✅ Implemented | Structured error recovery with retry and degradation flows |
| `db_operator` | ✅ Implemented | SQLite database query, inspection, and schema exploration |
| `rag_operator` | 🚧 Config only | RAG (Retrieval-Augmented Generation) operations (endpoint reserved) |

### Hooks (4 built-in)

Hooks are subprocess scripts triggered on session lifecycle events. Each is an independent Python module under `src/arf/hooks/`.

| Hook | Events | Description |
|------|--------|-------------|
| `system_log` | All 6 events | Structured JSON logging of all lifecycle events to `memory/hook_events.log` |
| `session_archiver` | SessionEnd | Archives completed session with full trace and usage data to `memory/sessions/*.json` |
| `memory_extractor` | SessionEnd | Extracts key facts, preferences, and decisions from conversation into `long_term.md` |
| `title_generator` | SessionStart | Generates a descriptive session title based on first user message (via API call) |

Hooks are defined in `.hooks.json` and can be managed at runtime via the `manage_hooks` kernel tool or the REST API. Users can add custom hooks by writing scripts and registering them in the config.

### Server & Infrastructure (all implemented)

| Component | Description |
|-----------|-------------|
| **FastAPI Server** | REST API (`/api/*`) + WebSocket (`/ws`) + SSE streaming |
| **SQLite Tracing** | 6-table observability database with waterfall visualization |
| **Session Manager** | CRUD, archive, idle lock (configurable timeout), title generation |
| **Hot Reload** | `watchfiles`-based file watcher for resources, hooks, and agent config |
| **Hook Engine** | Subprocess runner with parallel execution, timeout, and exit-code contract |
| **Docker** | Multi-stage Dockerfile + docker-compose for production deployment |
| **CI/CD** | GitHub Actions + Gitee CI pipelines |

### Frontend

| | Details |
|---|---|
| **Stack** | Vue 3 + TypeScript + Vite |
| **Size** | 6400+ lines: 8 views, 13 components, 8 composables, 3 Pinia stores, Vue Router |
| **Views** | WelcomePage, ChatLayout, ConfigPage, ResourceDetailView, ResourceStatsView, TraceView, UsagePage |
| **Dev** | `npm run dev` with HMR on port 5173, API proxied to backend |
| **Production** | `npm run build` → `server/static/`, served directly by FastAPI |

<br/>

## TODO

### Short-term

| Priority | Item | Status |
|----------|------|--------|
| **P0** | Sandbox runtime — security isolation for tool execution (currently in-process) | 🔴 Planned |
| **P0** | Context compaction — automatic compression when context exceeds 75% window | ✅ Done |
| **P0** | Progressive tool result disclosure — long outputs saved to disk, summary in context | ✅ Done |
| **P1** | `arf chat` — interactive CLI chat with full agent loop | 🟡 Stub |
| **P1** | `arf run` — headless script/batch execution mode | 🟡 Stub |
| **P1** | `web_search` tool — web search integration with configurable backend | 🟡 Config only |

### Medium-term

| Priority | Item | Status |
|----------|------|--------|
| **P2** | Multimodal tool implementations: `image_understanding`, `ocr`, `speech_output`, `speech_understanding`, `video_understanding` | 🟡 Config only |
| **P2** | Multimodal model integrations: `vision`, `vlm`, `tts`, `stt`, `embedding`, `rerank` | 🟡 Config only |
| **P2** | `rag_operator` skill — full RAG pipeline implementation | 🟡 Config only |


### Long-term

| Priority | Item | Status |
|----------|------|--------|
| **P3** | Tool approval flow — user-in-the-loop confirmation for sensitive tool calls | 🔴 Planned |
| **P3** | Runtime permission control module | 🔴 Planned |
| **P3** | Plugin/extension system — third-party resource packages installable via pip | 🔴 Planned |
| **P3** | MCP (Model Context Protocol) support — connect external MCP servers as tools | 🔴 Planned |

<br/>

## Design Decisions

ARF is opinionated. These choices are by design.

**Convention over configuration.** Paths are predictable, not configurable. `models/<name>/config.yaml` always works. No path aliases, no `$MODEL_DIR` env vars.

**One default per feature.** One web fetch, one memory store, one model adapter. Reduces maintenance, avoids analysis paralysis, keeps the system prompt clean.

**Four entity types.** model / skill / tool / hook. If a feature needs a fifth, it might not belong in the framework layer.

**No multi-provider abstraction.** ARF uses OpenAI-compatible APIs. Configure `base_url` and go — no provider-specific wrappers, no adapter pattern, no plugin registry.

**No cloud SaaS.** Self-hosted by design. No managed service, no telemetry, no accounts.



**Subprocess hooks, not in-process callbacks.** Hooks run as independent processes with their own timeout, environment, and failure domain. A crashed hook cannot bring down the agent. The exit-code contract (0/1/2) is language-agnostic — write hooks in Python, bash, or any executable.

<br/>

## Contributing

See [贡献者须知.md](./贡献者须知.md) for the contributor guide, including:

- How to add a new tool, skill, model, or hook
- Convention-over-configuration coding standards
- Pull request workflow and testing

```bash
# Development setup
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && npm run dev
```

**Core stack:** Python 3.10+ · FastAPI · LangGraph · Vue 3 · TypeScript · Vite · SQLite

**Dependencies:** uvicorn · websockets · openai · PyYAML · watchfiles · jinja2 · cryptography · python-multipart · langchain-core

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>Built with LangGraph, FastAPI, and Vue 3</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
