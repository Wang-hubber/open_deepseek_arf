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
| **[Tool Sandbox →](docs/tool-sandbox.md)**<br>Security boundaries | System calls + protection rings (Ring 0–3) + ACL | `PathCheckToolGuard` recursive scan (.., symlink, depth/count quota). Permission deny→ask→allow. Human approval channel with SSE push + 60s timeout. | Per-invocation sandbox; MCP protocol |
| **[Concurrency →](docs/skill-pipeline.md)**<br>Deadlock prevention | Superscalar execution + dependency graph | Sequential agent loop; parallel tool calls within a turn via `ConcurrentToolExecutor`. Skills declare tool pipelines with explicit dependencies — engine enforces order. Hook `asyncio.gather` concurrency. | Multi-agent DAG analysis; worktree isolation |
| **[Interrupt →](docs/interrupt.md)**<br>User intervention | Hardware interrupt: save state → ISR → restore | `asyncio.Event` cancellation. 3-snapshot undo (state + files) via API or in-conversation `undo` tool. Hook exit-code-2 message injection. | Pause/redirect vectors; idle timeout |
| **[Trace →](docs/trace.md)**<br>Observability | System monitoring + structured event log | 18 event types in EventType Literal → `FileTraceStore` (JSON) + `UsageTracker`. Frontend waterfall grouped by interaction round. Standalone viewer. | SQLite trace DB; OpenTelemetry export |

### Framework vs. Application

**Boundary principle**: The framework provides the mechanism (how); the application decides what to do through configuration + instantiation. `agent.yaml` is the bridge — the framework reads it and auto-assembles all capabilities; the app declares "what to use" without needing to know "how to implement."

| Layer | Scope | Capabilities |
|-------|-------|-------------|
| **Framework** (`arf/`) | **Execution Engine** | `GraphEngine` (invoke + astream dual mode), state repair, checkpoint/undo mechanism, cancel token, Memory extract→retrieve→write pipeline, Compaction context compression, Guardrails three-line defense, ModelRouter dispatch, Transaction rollback |
| | **Resource System** | `ResourceResolver` (unified resolution entry), `ToolProvider` / `SkillProvider` / `ModelProvider`, `ResourceCache` (kernel/dynamic split), `FileWatcher` (inotify/polling change detection), dual-source loading (filesystem + `agent.yaml` override merge) |
| | **Agent Assembly** | `BaseAgent` — DI wires all protocol implementations, `AgentConfig` — YAML-driven configuration, `ModelAdapter` — auto-injects call/stream, `LoopStrategy` — ReAct strategy |
| | **Infrastructure** | `EventBus` (`InMemoryEventBus`), `FileTraceStore` (per-session JSON persistence), `FileStateStore` / `InMemoryStateStore`, `UsageTracker` (token accounting), `SubprocessHookRunner` (exit-code contract: rc=2 → message injection), `PathSandbox` (path traversal guard), `TwoTierRouter` (LLM classifier routing), `SlidingWindowCompactor` (sliding window compaction), `SkillPipeline` (ordered tool execution), `DefaultErrorPolicy` / `SnapshotRollback` (transaction rollback), `GuardDefaults` (PathCheck / Regex / None three-line defense) |
| | **Protocols** | Protocol classes (`core/protocols/`) — defines `MemoryStore`, `MemoryWriter`, `HookRunner`, `GuardRunner`, `EventBus`, `LoopStrategy` and all other abstract interfaces |
| **Application** (`app/`) | **Frontend** | Vue 3 + TypeScript + Vite SPA, Pinia state management / VueRouter, ECharts charts / i18n (zh-CN + en-US), ChatPanel / TraceView / ResourcePanel and other components |
| | **HTTP Service** | FastAPI + Uvicorn + SSE streaming, REST endpoints (chat / trace / resources / config / usage …), WebSocket endpoint, CORS / SPA fallback / StaticFiles |
| | **CLI** | init / start / stop / chat / list / validate / config |
| | **Config & Data** | `agent.yaml` — agent behavior + routing strategy + memory strategy + compaction strategy, `models/deep.yaml` + `models/quick.yaml`, custom `tools/` (undo, file_*, web_*, python_exec …), custom `skills/` (code_review, debug, file_ops …), custom `hooks/`, DeepSeek API key management |

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

18 event types in EventType Literal, all emitted by engine across invoke + astream dual paths → `FileTraceStore` (JSON) + `UsageTracker` (token stats). Each event carries `round` (user interaction) and `turn` (internal iteration). The waterfall view at `/traces` groups by round with expandable iterations: model response → tool calls → hooks. Standalone HTML viewer at `/trace-viewer`.

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

> ~~**1-4 均已修复**~~ — agent.yaml 死引用、invoke/astream 事件统一、双 Agent 调度器接入均已完成。

### 设计 vs 代码不一致 (2026-05-26 事实校验)

> 以下为设计文档与当前代码实现的不一致项，需团队决策后处理。

- [ ] **System prompt 模板占位符 `{{MEMORY}}`/`{{WORKSPACE}}`/`{{LANGUAGE}}` 未实现** — 设计文档 `docs/superpowers/specs/2026-05-24-arf-framework-design.md` system_prompt.template 示例中定义了这些占位符，但 `arf/agent/base.py` `_build_system_prompt()` 仅支持 `{{AGENT_NAME}}`、`{{AGENT_ROLE}}`、`{{AGENT_TASK}}`、`{{CRITICAL_RULES}}`、`{{INVENTORY}}`。记忆/压缩上下文通过 `context_summary` 注入 messages[0] 前缀而非模板替换。建议：框架层实现指定位置的摘要替换（初步默认实现为在系统提示词之后）。

- [ ] **System prompt `pipeline` 分段组装未实现** — 设计文档定义了基于 priority 的分段组装管道（workspace → memory → critical_rules → inventory → language），但代码 `_build_system_prompt()` 使用简单字符串 replace + `{{INVENTORY}}` 拼接。`SystemPromptConfig` 中没有 `pipeline` 字段。涉及：设计 `docs/superpowers/specs/2026-05-24-arf-framework-design.md` system_prompt.pipeline；代码 `arf/agent/base.py:26-74`。

- [ ] **`StreamingConfig` / `SandboxConfig` 死代码** — 两个配置模型在 `arf/core/config_base.py` 中定义，但未包含在 `AgentConfig` 或 `AdvancedConfig` 中，运行时无法配置。涉及：代码 `arf/core/config_base.py:101-108`（StreamingConfig, SandboxConfig）；`arf/agent/config.py`（AdvancedConfig 缺少对应字段）。设计文档 `2026-05-24-arf-framework-design.md` AgentConfig 伪代码中列出了 `streaming` 和 `sandbox` 字段。

- [ ] **`passive` activation 语义未实现** — `ModelConfig`/`SkillConfig`/`ToolConfig` 均定义了 `activation: "passive"` 字面量，但 Provider 代码（如 `arf/resources/providers/tool_provider.py:105-113`）将 `passive` 与 `discoverable` 同等处理（均进入 dynamic 缓存）。文档描述的"需显式激活"行为未实现。建议：实现 passive 差异逻辑，或从类型中移除。

- [ ] **`AdvancedConfig` 缺少 `concurrency` 配置字段** — 工具并发度硬编码 `max_concurrency=5`（`arf/engine/tool_executor.py`），无配置入口。设计文档列出的 `AgentConfig` 字段也未包含 concurrency。建议：在 `AdvancedConfig` 增加 concurrency 配置段。

### 演进方向

参见各模块设计文档第三章：
- [Memory](docs/memory-management.md#3-演进方向) — 语义单元检索、知识图谱索引、记忆衰减
- [Model Routing](docs/model-routing.md#3-演进方向) — 三级分类器、连续负载跟踪、模型硬件化
- [Resource Registry](docs/resource-registry.md#3-演进方向) — 层次化覆盖合并、MCP 多源 Provider
- [Tool Sandbox](docs/tool-sandbox.md#3-演进方向) — Per-invocation sandbox、MCP 协议
- [Skill Pipeline](docs/skill-pipeline.md#3-演进方向) — 多 Agent DAG 分析、Worktree 隔离
- [Interrupt](docs/interrupt.md#3-演进方向) — 暂停/重定向、空闲超时
- [Trace](docs/trace.md#3-演进方向) — SQLite Trace DB、OpenTelemetry 导出

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
