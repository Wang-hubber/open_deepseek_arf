<p align="center">
  <h1 align="center">ARF — Agent Resource Framework</h1>
</p>

<p align="center">
  <strong>English</strong>
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

<h3 align="center">A LangGraph-native AI agent framework with a Vue 3 frontend.</h3>
<p align="center">Workspace-based. Dual-source resources (system + user). Hot-reload. Built-in trace observability. Docker one-command deploy.</p>

<br/>

> [!TIP]
> ARF treats your local filesystem as the source of truth. Tools, skills, and models are just directories with YAML configs — no database migration, no web console, no vendor lock-in. `git push` and your entire agent configuration is shared.

> [!NOTE]
> **LangGraph engine (default):** structured multi-node agent graph with SQLite trace observability, classifier-driven model routing, and streaming SSE events — all served through a single FastAPI process.

<br/>

## Quick Start

Requires Python ≥ 3.10 and Node.js ≥ 18.

```bash
# Clone and install
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

# Create a workspace and start
arf init my_workspace
arf web --workspace my_workspace

# In another terminal, start the frontend dev server
cd frontend && npm run dev
```

Browser opens at **http://localhost:5173** — configure your LLM connection and start chatting.

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

## CLI

| Command | When |
|---------|------|
| `arf init <name>` | Create a new workspace. **Start here.** |
| `arf web` | Launch the web server (FastAPI + WebSocket + SSE). |
| `arf list [tools\|skills\|models]` | List registered resources. `[sys]` = framework built-in. |
| `arf validate` | Check workspace resource integrity. |
| `arf clone <type> <name>` | Copy a system resource to your workspace for customization. |
| `arf vault init` | Create an encrypted vault for API keys and credentials. |

<br/>

## What makes ARF different

| Pillar | Idea |
|--------|------|
| **Filesystem as registry** | A tool is a directory with `tool.yaml` + `function.py`. A skill is a directory with `skill.yaml`. No database, no web console — `ls` and `git diff` tell you everything. |
| **Dual-source resources** | System resources ship with the package and update via `pip install --upgrade`. User resources live in your workspace and override system ones by name. You never lose your customizations. |
| **Hot-reload** | Edit a tool's YAML or Python file — the agent picks it up immediately. No restart, no re-registration. |
| **LangGraph engine** | StateGraph with explicit nodes (classify → call_model → route → execute_tools/respond). Structured state, conditional edges, built-in SQLite tracing. |
| **Progressive disclosure** | Initial system prompt ~800 tokens. 7 kernel tools always active. Everything else loaded on demand — the agent only pays for context it actually uses. |

<br/>

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    FastAPI Server                     │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ REST API │  │  WebSocket│  │  Static Files     │  │
│  │ /api/*   │  │ /ws      │  │  (Vue 3 SPA)      │  │
│  └────┬─────┘  └────┬─────┘  └───────────────────┘  │
│       │             │                                 │
│  ┌────┴─────────────┴────┐                           │
│  │    SessionManager      │                           │
│  │  ┌──────────────────┐  │                           │
│  │  │  LangGraph Agent  │  │                           │
│  │  │  classify → model │  │                           │
│  │  │  → tools → respond│  │                           │
│  │  └────────┬─────────┘  │                           │
│  │           │             │                           │
│  │  ┌────────┴─────────┐  │                           │
│  │  │  ResourceRegistry │  │                           │
│  │  │  tools · skills  │  │                           │
│  │  │  models · hooks  │  │                           │
│  │  └────────┬─────────┘  │                           │
│  └───────────┼────────────┘                           │
└──────────────┼────────────────────────────────────────┘
               │
     ┌─────────┴──────────┐
     ▼                    ▼
┌──────────┐      ┌──────────────┐
│  System  │      │     User     │
│  (read)  │      │  (read/write)│
│  package │      │  workspace/  │
└──────────┘      └──────────────┘
```

<br/>

## Built-in Resources

### Models (9 types)

| Type | Use |
|------|-----|
| `deep_thinking` | Complex reasoning — system design, multi-file refactors |
| `quick_thinking` | Medium tasks — code generation, debugging |
| `quick_no_thinking` | Simple — file reads, fact lookup, low latency |
| `embedding` | Vector embeddings for semantic search |
| `rerank` | Result re-ranking |
| `vision` · `vlm` · `tts` · `stt` | Multimodal — image, video, speech |

### Kernel Tools (always active)

| Tool | Purpose |
|------|---------|
| `file_reader` · `file_writer` · `file_deleter` | Filesystem operations |
| `resource_loader` | On-demand tool activation |
| `memory_store` | Long-term memory read/write with backup rotation |
| `model_manager` | Model CRUD, connection test, activation switch |
| `model_switch` | Runtime three-tier model hot-switch with degradation |

### Discoverable Tools (load on demand)

`web_fetch` · `web_search` · `git_pusher` · `resource_registrar` · `manage_hooks` · `image_understanding` · `ocr` · `speech_output` · `speech_understanding` · `video_understanding`

### Skills (15 built-in)

`error_handler` · `model_switch` · `model_manager` · `model_configurator` · `memory_extract` · `memory_compress` · `memory_management` · `resource_scaffold` · `tool_generator` · `tool_manager` · `skill_generator` · `skill_manager` · `validate_tool` · `db_operator` · `rag_operator`

<br/>

## How it compares

| | ARF | LangChain | Dify | Raw FastAPI + SDK |
|---|---|---|---|---|
| Approach | Workspace-as-code | Library | Low-code platform | Do-it-yourself |
| Agent engine | **LangGraph StateGraph** | LangGraph | Custom | You build it |
| Resource model | **Filesystem directories** | Python classes | Web UI forms | N/A |
| Hot-reload | **yes (built-in)** | manual | partial | manual |
| Frontend | **Vue 3 + TypeScript** | none (LangServe) | React | You build it |
| Trace observability | **SQLite + waterfall** | LangSmith (paid) | built-in | You build it |
| Self-hosted | **yes, single binary** | yes | yes (Docker) | yes |
| Vendor lock-in | none | LangChain ecosystem | Dify platform | none |
| Open source | **MIT** | MIT | Apache 2 | N/A |

<br/>

## Workspace Structure

```
my_workspace/
├── arf_agent.yaml          # workspace config (agent name, model, max_turns)
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
```

<br/>

## Memory System

| Layer | Storage | Purpose |
|-------|---------|---------|
| **Session** | `memory/session.md` | Current conversation context, injected into every prompt |
| **Long-term** | `memory/long_term.md` | User profile, preferences, facts — persists across sessions |
| **Archive** | `memory/sessions/*.json` | Completed sessions with structured trace & usage data |

Extraction and compression are automatic: the agent runs `memory_extract` after each session and triggers `memory_compress` when long-term memory exceeds 700 KB.

<br/>

## Development

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && npm run dev
```

**Core stack:** Python 3.10+ · FastAPI · LangGraph · Vue 3 · TypeScript · Vite · SQLite

**Dependencies:** uvicorn · websockets · openai · PyYAML · watchfiles · jinja2 · cryptography

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

### Model config (`models/<name>/config.yaml`)

```yaml
name: deep_thinking
model_type: deep_thinking
config:
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."
  model_name: "deepseek-chat"
  temperature: 0.7
  max_tokens: 10240
  thinking_enabled: true
  reasoning_effort: "max"
```

<br/>

## Non-goals

> [!IMPORTANT]
> ARF is opinionated. Some things it deliberately *doesn't* do.

- **Multi-provider abstraction layer.** ARF uses OpenAI-compatible APIs. It doesn't wrap every provider in a unified interface — configure the base URL and go.
- **No-code / low-code platform.** ARF expects you to write YAML and Python. The web UI is for interaction, not for building resources.
- **Cloud SaaS.** Self-hosted by design. No managed service, no telemetry, no accounts unless you enable multi-user mode.
- **Drop-in LangChain replacement.** ARF *uses* LangGraph internally but wraps it in a workspace-oriented framework with its own resource model.

<br/>

## Star History

<a href="https://www.star-history.com/?repos=Wang-hubber%2Fopen_deepseek_arf&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Wang-hubber/open_deepseek_arf&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Wang-hubber/open_deepseek_arf&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Wang-hubber/open_deepseek_arf&type=date&legend=top-left" />
 </picture>
</a>

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>Built with LangGraph, FastAPI, and Vue 3</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
