<p align="center">
  <h1 align="center">ARF — Agent Resource Framework</h1>
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

> [!TIP]
> ARF treats your local filesystem as the source of truth. Tools, skills, and models are just directories with YAML configs — no database migration, no web console, no vendor lock-in. `git push` and your entire agent configuration is shared. The agent can even create and modify its own resources at runtime — self-evolution is a first-class concept.

> [!NOTE]
> **LangGraph engine (default):** structured multi-node agent graph with SQLite trace observability, classifier-driven model routing, and streaming SSE events — all served through a single FastAPI process. Only 9 kernel tools are always active (~800 tokens); everything else loads on demand.

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

Browser opens at **http://localhost:5173** — configure your LLM connection and start chatting.

For API key security, use the encrypted vault:

```bash
arf vault init          # create vault, set password
arf vault unlock        # enter password to decrypt credentials
```

### Docker (one command)

```bash
docker compose up -d
# → http://localhost:8000
```

Pre-built images via GitHub Actions (ghcr.io) and Gitee CI (Aliyun ACR). See [`.github/workflows/docker-publish.yml`](./.github/workflows/docker-publish.yml) and [`.gitee-ci.yml`](./.gitee-ci.yml).

| Registry | Pull command |
|----------|-------------|
| **GitHub (ghcr.io)** | `docker pull ghcr.io/Wang-hubber/open_deepseek_arf:latest` |
| **Aliyun ACR** (国内) | `docker pull registry.cn-hangzhou.aliyuncs.com/<ns>/arf:latest` |

<br/>

## Design Philosophy

ARF is built on five principles that shape every design decision.

### 1. Execution Layer and Control Layer — Highly Decoupled

The file system is the bridge between WHAT to do and HOW to do it.

- **Control layer** — YAML configs (`tool.yaml`, `skill.yaml`, `config.yaml`) define what resources exist, their schemas, and when they should be used. This layer is human-readable, git-trackable, and requires no code to understand.
- **Execution layer** — The LangGraph engine dynamically loads and invokes tool functions at runtime. It receives all dependencies through constructor injection and has no knowledge of specific resources.
- **The Agent as orchestrator** — `ARFAgent` reads from the filesystem registry and dispatches to the execution engine. Changes to control files (edit a tool's YAML, add a new skill) are picked up instantly via hot-reload — the execution layer adapts without restart.

This decoupling means you can reason about agent behavior by reading YAML files, and you can swap or extend capabilities by adding directories.

### 2. Filesystem-Native, Self-Evolving Agent

ARF is not just configurable — it can evolve itself.

- **No database migrations.** `arf init` creates directories. `git push` shares configuration. `ls` inspects state.
- **Runtime self-evolution.** The agent can create, modify, and register new tools and skills at runtime using `resource_scaffold` + `file_writer`. A conversation can produce a permanent capability.
- **Memory is files.** Session context (`session.md`), long-term memory (`long_term.md`), and archived sessions (`sessions/*.json`) are all files on disk — grep-able, backup-able, and transparent.
- **Dual-source resources.** System resources ship with the framework (read-only, updated via `pip install --upgrade`). User resources live in the workspace (read-write, override system by name). Your customizations survive framework updates.

### 3. High Cohesion, Low Coupling — Convention Over Configuration

Every resource follows the same minimal convention. The framework discovers, you don't register.

- A **tool** is a directory with `tool.yaml` + `function.py`. That's it — no decorators, no base class, no `__init__.py`, no import hook.
- A **skill** is a directory with `skill.yaml`. Tools referenced by name, not import path.
- A **model** is a directory with `config.yaml`. The adapter resolves configuration at call time.
- Resource directories at known paths are automatically scanned and indexed. Adding a tool means creating the right directory structure — period.

```
models/deep_thinking/config.yaml    →  available as "deep_thinking"
tools/web_fetch/tool.yaml           →  available as "web_fetch"
skills/error_handler/skill.yaml     →  available as "error_handler"
```

### 4. One Default Implementation Per Feature

For non-developer users, ARF ships with exactly one implementation for each capability.

- One `web_fetch` — not three HTTP clients.
- One `memory_store` — not five storage backends.
- One `model_adapter` — OpenAI-compatible API, not a multi-provider abstraction.

This is deliberate: reduce choice fatigue, reduce maintenance burden, reduce bug surface. If you need something different, `arf clone` the default to your workspace and customize it.

### 5. Progressive Disclosure

The initial system prompt is ~800 tokens. Only 9 kernel tools are always active.

- **Kernel tools** (always active): `file_reader`, `file_writer`, `file_deleter`, `resource_loader`, `memory_store`, `model_manager`, `model_switch`, `resource_registrar`, `manage_hooks`
- **Everything else** loads on demand: the agent reads a skill that references a tool, activates it via `resource_loader`, and uses it. Deactivates when done.
- The agent only pays for context it actually uses — no dumping 50+ tool definitions into every prompt.

### 6. No Unnecessary Entities

The complete extension taxonomy is: **model · skill · tool · hook**. Four entities. No plugins, no middleware chains, no provider interfaces, no component registries, no abstract factories. If a feature can't be expressed as one of these four, reconsider whether it belongs.

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

The control layer (YAML in directories) defines WHAT resources exist. The execution layer (LangGraph engine) determines HOW they run. The file system bridges them — `edit, save, and it's live`.

<br/>

## Implementation Status

### CLI

| Command | Status |
|---------|--------|
| `arf init` · `arf web` · `arf start` · `arf stop` · `arf reload` | ✅ Implemented |
| `arf list` · `arf validate` · `arf clone` · `arf vault *` | ✅ Implemented |
| `arf chat` · `arf run` | 🚧 Stub (prints "not yet implemented") |

### Engine

All nodes fully implemented: `classify` → `call_model` → `execute_tools` / `respond`, with `recovery` for max_tokens continuation and API error handling. Classifier-driven three-tier model routing (`quick_no_thinking` → `quick_thinking` → `deep_thinking`) with automatic degradation.

### Tools (17 total)

| Status | Count | Tools |
|--------|-------|-------|
| **Kernel** (always active) | 9 | `file_reader` · `file_writer` · `file_deleter` · `resource_loader` · `memory_store` · `model_manager` · `model_switch` · `resource_registrar` · `manage_hooks` |
| **Discoverable** — implemented | 2 | `web_fetch` · `git_pusher` |
| **Discoverable** — stub | 6 | `web_search` · `image_understanding` · `ocr` · `speech_output` · `speech_understanding` · `video_understanding` |

Kernel tools are always in the system prompt (~800 tokens). Discoverable tools are activated on demand via `resource_loader`. Stub tools have `config_default.yaml` but no `function.py` — endpoint reserved, implementation pending.

### Models (9 total)

| Status | Count | Types |
|--------|-------|-------|
| **Reasoning** — configurable | 3 | `deep_thinking` · `quick_thinking` · `quick_no_thinking` |
| **Multimodal** — stub | 6 | `embedding` · `rerank` · `vision` · `vlm` · `tts` · `stt` |

Reasoning models are fully configurable with DeepSeek-compatible API. Multimodal model configs exist as placeholders (no integration yet).

### Skills (14 total, all with `skill.yaml`)

| Category | Skills |
|----------|--------|
| **Memory** | `memory_extract` · `memory_compress` · `memory_management` |
| **Model** | `model_switch` · `model_manager` · `model_configurator` |
| **Tool** | `tool_generator` · `tool_manager` · `validate_tool` |
| **Skill** | `skill_generator` · `skill_manager` |
| **Infra** | `resource_scaffold` · `error_handler` · `db_operator` |

Note: `rag_operator` has `config_default.yaml` only (partial).

### Server & Infrastructure (all implemented)

| Component | Status |
|-----------|--------|
| FastAPI + WebSocket + SSE streaming | ✅ |
| SQLite trace observability (6 tables) | ✅ |
| Session management (CRUD, archive, idle lock) | ✅ |
| Hot-reload file watcher (resources) | ✅ |
| Subprocess hook engine (4 built-in hooks) | ✅ |
| AES-256-GCM encrypted vault | ✅ |
| Multi-stage Docker + docker-compose | ✅ |
| CI/CD (GitHub Actions + Gitee CI) | ✅ |

### Frontend

Two implementations serving different purposes:

| | Production SPA | Dev project |
|---|---|---|
| **File** | `server/static/index.html` | `frontend/src/` |
| **Size** | 2062 lines | 6400+ lines (8 views, 13 components, 8 composables, 3 stores) |
| **Tech** | Vanilla HTML/JS with Vue 3 CDN | Vue 3 + TypeScript + Vite |
| **Use** | Served directly by backend | `npm run dev` with HMR |

### Framework TODO

| Item | Status |
|------|--------|
| **SandBox runtime security isolation** | 🔴 Planned — tools currently execute in-process |
| `arf chat` interactive CLI | 🟡 Stub |
| `arf run` headless execution | 🟡 Stub |
| Multi-user mode | 🟡 Commented out in `cli.py` |
| Multimodal tool implementations | 🟡 6 stubs |

<br/>

## CLI Reference

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
| `arf vault init` | Create an encrypted vault for API keys and credentials. |
| `arf vault unlock` · `lock` · `status` | Manage vault lifecycle. |

<br/>

## Built-in Resources

### Kernel Tools (always active, ~800 tokens)

| Tool | Purpose |
|------|---------|
| `file_reader` · `file_writer` · `file_deleter` | File system operations |
| `resource_loader` | On-demand tool activation / deactivation |
| `memory_store` | Long-term memory read/write with backup rotation |
| `model_manager` | Model CRUD, connection test, activation switch |
| `model_switch` | Runtime three-tier model hot-switch with degradation |
| `resource_registrar` | Query resource configuration status |
| `manage_hooks` | View and toggle hook definitions at runtime |

### Discoverable Tools (load on demand)

| Tool | Status | Purpose |
|------|--------|---------|
| `web_fetch` | ✅ | Fetch and process web content |
| `git_pusher` | ✅ | Stage, commit, and push files |
| `web_search` | 🚧 | Web search (config only) |
| `image_understanding` | 🚧 | Image analysis (config only) |
| `ocr` | 🚧 | Optical character recognition (config only) |
| `speech_output` | 🚧 | Text-to-speech (config only) |
| `speech_understanding` | 🚧 | Speech-to-text (config only) |
| `video_understanding` | 🚧 | Video analysis (config only) |

### Skills (14 built-in, grouped by category)

| Category | Skill | Purpose |
|----------|-------|---------|
| **Memory** | `memory_extract` | Extract long-term memories from conversation |
| | `memory_compress` | Compress long-term memory (triggers at 700 KB) |
| | `memory_management` | Inspect and manage memory files |
| **Model** | `model_switch` | Switch model tier at runtime |
| | `model_manager` | Full model lifecycle management |
| | `model_configurator` | Interactive model configuration |
| **Tool** | `tool_generator` | Generate new tools from conversation |
| | `tool_manager` | Manage tool lifecycle |
| | `validate_tool` | Validate tool YAML and function.py |
| **Skill** | `skill_generator` | Generate new skills from conversation |
| | `skill_manager` | Manage skill lifecycle |
| **Infra** | `resource_scaffold` | Scaffold resource directory structures |
| | `error_handler` | Structured error recovery flow |
| | `db_operator` | SQLite database query and inspection |

<br/>

## How ARF Compares

| | ARF | LangChain | Dify | Raw FastAPI + SDK |
|---|---|---|---|---|
| **Approach** | Workspace-as-code | Library | Low-code platform | Do-it-yourself |
| **Agent engine** | LangGraph StateGraph | LangGraph | Custom | You build it |
| **Resource model** | **File system directories** | Python classes | Web UI forms | N/A |
| **Self-evolution** | **Yes — agent creates resources** | Manual | Partial (plugin store) | Manual |
| **Hot-reload** | **Yes (built-in)** | Manual | Partial | Manual |
| **Progressive disclosure** | **Yes — 9 kernel tools, ~800 tokens** | No (full tool list) | Partial | Manual |
| **Frontend** | Vue 3 + TypeScript | None (LangServe) | React | You build it |
| **Trace observability** | SQLite + waterfall | LangSmith (paid) | Built-in | You build it |
| **Sandbox isolation** | Planned | Optional | Partial | Manual |
| **Self-hosted** | Yes, single process | Yes | Yes (Docker) | Yes |
| **Vendor lock-in** | None — git-native | LangChain ecosystem | Dify platform | None |
| **Open source** | MIT | MIT | Apache 2 | N/A |

<br/>

## Workspace Structure

```
my_workspace/
├── arf_agent.yaml          # workspace config (agent name, model, max_turns, preload)
├── .arf/                   # runtime state (PID files, run config)
├── models/                 # user model configs
│   └── deep_thinking/
│       └── config.yaml     # base_url, api_key, model_name, temperature...
├── tools/                  # user custom tools
├── skills/                 # user custom skills
├── memory/                 # memory system
│   ├── session.md          # short-term session context
│   ├── long_term.md        # persistent user profile & facts
│   └── sessions/           # archived session JSON with traces
└── .git/
```

Enable the classifier for automatic model routing:

```yaml
# arf_agent.yaml
agent:
  name: "My Workspace"
  model: "quick_no_thinking"
  max_turns: 10
  classifier_enabled: true       # auto-route tasks by complexity

resources:
  preload: []                    # tools to activate at session start
```

<br/>

## Memory System

| Layer | Storage | Purpose |
|-------|---------|---------|
| **Session** | `memory/session.md` | Current conversation context, injected into every prompt |
| **Long-term** | `memory/long_term.md` | User profile, preferences, facts — persists across sessions |
| **Archive** | `memory/sessions/*.json` | Completed sessions with structured trace & usage data |

Extraction and compression are fully automatic: the `memory_extractor` hook runs after each session ends, and `memory_compress` triggers when long-term memory exceeds 700 KB (configurable). `memory_store` creates automatic backups before every long-term memory write.

<br/>

## Configuration

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARF_SERVE_STATIC` | `1` | Serve built frontend from backend. Set `0` for dev proxy. |
| `ARF_DB_NAME` | `arf.db` | SQLite database filename |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS allowed origins, comma-separated |
| `ARF_IDLE_TIMEOUT` | `600` | Session idle timeout in seconds |
| `ARF_API_MAX_RETRIES` | `3` | Max API call retries |
| `ARF_API_RETRY_BACKOFF` | `1.5` | Retry backoff base in seconds |
| `ARF_WORKSPACE` | — | Workspace directory path (Docker) |
| `ARF_DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Base URL for one-click DeepSeek config |

### Model config (`models/<name>/config.yaml`)

```yaml
name: deep_thinking
model_type: deep_thinking
config:
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."             # or omit and use vault
  model_name: "deepseek-chat"
  temperature: 0.7
  max_tokens: 10240
  thinking_enabled: true
  reasoning_effort: "max"
```

For production, store `api_key` in the encrypted vault (`arf vault init`) rather than plaintext YAML.

<br/>

## Roadmap

| Timeline | Item |
|----------|------|
| **Next** | Sandbox runtime — security isolation for tool execution (currently in-process) |
| **Next** | `arf chat` — interactive chat CLI |
| **Next** | `arf run` — headless script execution |
| **Later** | Multimodal tool implementations (web_search, image_understanding, ocr, etc.) |
| **Later** | Multi-user mode — authentication and per-user sessions |

<br/>

## Design Decisions

> [!IMPORTANT]
> ARF is opinionated. These choices are by design.

**Convention over configuration.** Paths are predictable, not configurable. `models/<name>/config.yaml` always works. No path aliases, no `$MODEL_DIR` env vars.

**One default per feature.** One web fetch, one memory store, one model adapter. Reduces maintenance, avoids analysis paralysis, keeps the system prompt clean.

**Four entity types.** model / skill / tool / hook. If a feature needs a fifth, it might not belong in the framework layer.

**No multi-provider abstraction.** ARF uses OpenAI-compatible APIs. Configure `base_url` and go — no provider-specific wrappers.

**No cloud SaaS.** Self-hosted by design. No managed service, no telemetry, no accounts (unless you enable multi-user mode).

**No code-free builder.** ARF expects you to write YAML and Python. The web UI is for interaction, not for building resources.

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

**Dependencies:** uvicorn · websockets · openai · PyYAML · watchfiles · jinja2 · cryptography

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>Built with LangGraph, FastAPI, and Vue 3</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
