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
<p align="center">Local-first. Convention over configuration. Fully traceable. Self-evolving.</p>

<br/>

> **Built by DeepSeek V4 Pro & Claude Code.** The author provided design direction and code review only — not a single line was manually written.

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

| Problem | OS Solution | Current | Evolution |
|---------|-------------|---------|-----------|
| **Memory management (OOM + persistence)** | Virtual memory + file system | Token-aware sliding window compaction at 75% threshold. LLM summarization of evicted turns. Automatic fact/preference/decision extraction with dedup and semantic retrieval. Long tool outputs written to disk. [Compaction →](docs/compaction.md) [Memory →](docs/memory-pipeline.md) | Semantic-unit retrieval; knowledge graph index |
| **Model routing & resource allocation** | Multi-level cache + big.LITTLE scheduling | Two-tier LLM classifier (medium→quick, complex→deep). Dedicated model for framework background tasks. Per-turn dynamic switching. [Model Routing →](docs/model-routing.md) | Hardware-aware dispatch; weak-model ensemble |
| **Tool sandbox & security boundaries** | System calls + protection rings (Ring 0–3) + ACL | `tool.yaml` + `function.py` per tool. `PathCheckToolGuard` blocks traversal. Dual-source isolation: framework read-only, workspace read-write. Hook exit-code contract (0/1/2). Permission deny→ask→allow pipeline. [Sandbox →](docs/tool-sandbox.md) | Per-invocation sandbox; MCP protocol |
| **Concurrency & deadlock prevention** | Superscalar execution + dependency graph | Sequential execution. Skills declare tool pipelines with explicit dependencies — engine enforces order. Hook thread-pool parallelization. [Skill Pipeline →](docs/skill-pipeline.md) | Multi-agent DAG analysis; worktree isolation |
| **External interrupt & user intervention** | Hardware interrupt: save state → ISR → restore | `asyncio.Event` cancellation. 3-snapshot undo (state + files) via API or in-conversation `undo` tool. Hook exit-code-2 message injection. [Interrupt →](docs/interrupt.md) | Pause/redirect vectors; idle timeout |
| **Trace & observability** | System monitoring + structured event log | 13 event types via EventBus → `FileTraceStore` (JSON) + `UsageTracker`. Frontend waterfall grouped by interaction round. Resource stats API. Standalone viewer. [Trace →](docs/trace.md) | SQLite trace DB; OpenTelemetry export |

### Framework vs. Application

| Layer | Scope | Examples |
|-------|-------|----------|
| **Framework** (`arf/`) | Conventions, engine, resource system, trace infrastructure | `GraphEngine`, `ResourceRegistry`, dual-source loading, hook exit-code contract, `EventBus`, `FileTraceStore` |
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

[Design doc →](docs/memory-pipeline.md)

### Compaction — Token-Aware Context Management

The `SlidingWindowCompactor` monitors the previous turn's token usage. At 75% of the model's context window, it triggers: keeps the last 4 messages, summarizes older turns via LLM, and appends to `context_summary`. Long tool outputs are written to disk with a summary in context.

```yaml
advanced:
  compaction:
    strategy: sliding_window
    threshold: 0.75
```

[Design doc →](docs/compaction.md)

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

13 event types stream via `EventBus` → `FileTraceStore` (JSON) + `UsageTracker` (token stats). Each event carries `round` (user interaction) and `turn` (internal iteration). The waterfall view at `/traces` groups by round with expandable iterations: model response → tool calls → hooks. Standalone HTML viewer at `/trace-viewer`.

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
| `arf web` | Backend only (FastAPI + SSE) |
| `arf stop` | Stop running processes |
| `arf reload` | Stop + restart |
| `arf list [tools\|skills\|models]` | List registered resources |
| `arf validate` | Check workspace resource integrity |
| `arf clone <type> <name>` | Copy a system resource to workspace for customization |

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARF_SERVE_STATIC` | `1` | Serve frontend from backend |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS allowed origins |
| `ARF_IDLE_TIMEOUT` | `600` | Session idle timeout (seconds) |

Model config: `agent.yaml` — `base_url`, `api_key_env`, `model`, `context_window`, `temperature`, etc.

<br/>

## Contributing

See [贡献者须知.md](./贡献者须知.md).

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
```

**Core stack:** Python 3.10+ · FastAPI · Vue 3 · TypeScript · Vite

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
