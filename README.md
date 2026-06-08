<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
  <p align="center"><em>A Research Scaffold & Harness MVP for the Brain-Spine-Body Architecture</em></p>
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
<p align="center">The Model is the Brain. The Harness is the Brainstem, Spine & Body.</p>
<p align="center">Local-first. Convention over configuration. Fully traceable. Self-evolving.</p>

<br/>

> **Built by DeepSeek V4 Pro & Claude Code.** The author provided design direction and code review only — not a single line was manually written.

<br/>

## Research Context

ARF is the **engineering companion** to the research paper *"Finding the Spine of Agent Systems — Strict Division of Labor and Co-evolution between Large Models and Harness."*

**The core thesis**: Current Agent systems suffer from *Harness bloat*. The coordination layer has absorbed cognitive responsibilities — RAG knowledge injection, system-prompt identity injection, context summarization, external memory management — that rightfully belong to the model. This bloat is not an implementation flaw; it signals a fundamental confusion of roles. The Harness should be a **zero-cognition mechanical layer**, analogous to the brainstem, spine, and body: it encodes perception, executes action, and runs hardwired reflexes. It does not think.

**ARF's dual role**:
- **As an MVP**: ARF implements the three mechanical layers and demonstrates that a Harness stripped of cognitive responsibility can run a fully functional Agent. The 6 skeletons + plugin system form a complete, testable embodiment of the design principles.
- **As a research scaffold**: ARF provides the unified testbed for all five experiments proposed in the paper — state interface stability, zero-cognition benchmarks, online LoRA memory, identity boundary robustness, and internal context compression.

**What this MVP proves**: A Harness built on Protocol-defined skeletons, where all "intelligent" behaviors (memory extraction, task tracking, context compaction) are implemented as pluggable reflex arcs — never as core engine logic — maintains strict separation between *cognitive work* (model) and *mechanical work* (harness).

<br/>

---

## Reading Guide

This document is organized in three parts plus research roadmap:

- **Part I — Framework**: The Brain-Spine-Body design model, the three mechanical layers, the 6-skeleton architecture, and the plugin system
- **Part II — Reference App**: How the application layer consumes every framework capability, with config examples
- **Part III — Research Roadmap**: Mapping the paper's five experiments to current ARF capabilities and extension points
- **Bottom [TODO](#todo)**: Known issues and evolution directions

New readers: scan the three mechanical layers first to understand the architecture thesis, then the 6-skeleton table for implementation details.

<br/>

---

## Part I — Framework

### Design Philosophy: The Brain-Spine-Body Model

A model is raw compute — powerful, but not a computer. It needs memory management, process scheduling, interrupt handling, a file system, and security boundaries. ARF provides those. But the design goes deeper than an OS analogy.

**The biological mapping that governs every architectural decision:**

| Biological System | Agent System | Responsibility | Cognitive Load |
|-------------------|-------------|----------------|----------------|
| **Brain** | Large Language Model | Conditioned reflex center. Receives encoded state packets, outputs action instructions. Post-training internalizes professional knowledge and identity boundaries. | **Full cognitive** — understanding, reasoning, deciding |
| **Brainstem / Spine** | Harness Core (6 Skeletons) | Fixed perception encoding, reliable action execution, unconditioned reflex. Multi-source signals time-aligned into fixed-schema State Packets. Function calls parsed, executed, feedback collected — no policy judgment. | **Zero cognition** — format, align, execute, guard |
| **Body** | Tool Ecosystem | The physical/virtual effectors the model operates through. File I/O, web fetch, shell execution, code interpretation. | **Zero cognition** — mechanical action only |

**The co-evolution imperative**: A monkey brain cannot operate a human body. The state schema defined by the Harness must become the model's native perception language through paired post-training. Brain and body co-evolve, or neither works.

The primitives of operating systems — virtual memory, cache hierarchies, system calls, protection rings — map directly onto the implementation. But the architecture thesis is biological: **cognitive work belongs to the brain; the harness is spinal cord, not a second brain.**

### The Three Mechanical Layers

ARF implements the Harness as three zero-cognition layers, each mapped to specific skeletons. These layers correspond to Section 5 of the paper ("Design Principles for the Ideal Harness").

| Layer | Principle | Implementation in ARF | Skeletons |
|-------|-----------|----------------------|-----------|
| **L1: Fixed Perceptual Encoder** | Zero-cognition state encoding. Fixed period, fixed schema, format + time alignment only. No questioning, no clarification, no understanding. | `SystemPromptProvider` assembles structured context from fixed template. `ResourceResolver` + `FileWatcher` discover and hot-reload tools/skills/models by filesystem convention — no semantic interpretation. | #1 Prompt Assembly, #2 Resource Registry |
| **L2: Reliable Action Executor** | Reversible action execution. Preview-execute-rollback cycle ensures execution damage is recoverable. Parallel execution with dependency ordering. | `SandboxManager` provides per-session isolated workspaces. `ConcurrentToolExecutor` runs independent tool calls in parallel. `FunctionBackend` supports optional `rollback()` per tool. `SkillPipeline` enforces dependency DAG. | #5 Executor (Sandbox), Skill Pipeline |
| **L3: Unconditioned Reflex** | Hardcoded safety. Permission gating, withdrawal reflex, rhythmic checkpointing — independent of model decisions. The model cannot bypass these. | `PathCheckToolGuard` blocks path traversal + absolute paths. `ContentGuard` screens pre/post execution. `SessionModeManager` + `PermissionRegistry` enforce deny→ask→allow. `RoundManager` maintains rolling snapshots for undo. Cancel token checked every turn. | #3 Permission Control, #4 Security Audit, Interrupt |
| **Cross-cutting** | Process scheduler + lifecycle signals. The control plane that orchestrates the three layers. | `GraphEngine` single `_execute` path with 9 Hook injection points. `LoopStrategy` ReAct pattern + TODO tracking. State management with checkpoint/restore. | #6 Control Plane |

**Design Principle**: Each layer is *mechanical* — it transforms, routes, gates, or records. None of them *understand*. When the framework needs "intelligence" (memory extraction, context summarization), it calls a model through a Plugin — never through core engine logic.

### Harness as Kernel — 6-Skeleton Architecture

> **Model + Harness = Agent. CPU + Kernel = Computer.**
>
> Token is the instruction. Agent session is the process. Tool call is the system call.

ARF is built on **6 skeletons** — the minimum viable framework. Each skeleton maps to a Protocol. The framework can run an Agent with only these 6; everything else is a **Plugin** mounted on lifecycle Hook points.

*Click any skeleton name for the full design document.*

| # | Skeleton | OS Analogy | Current | Evolution |
|---|----------|------------|---------|-----------|
| 1 | **[Prompt Assembly](docs/prompt-assembly.md)** | Program loader (execve) | `SystemPromptProvider` — prefix (role + critical_rules) + suffix (`$INVENTORY` template). `string.Template` placeholders (`$MEMORY`, `$WORKSPACE`, `$TURN_BUDGET`). Per-turn replacement by engine. | Multi-agent prompt composition; role-based template dispatch |
| 2 | **[Resource Registry (MCP)](docs/resource-registry.md)** | File system + udev + systemd | Convention over configuration: `tool.yaml`+`function.py` per tool, `skills/*.yaml`. Models defined inline in `agent.yaml` (`model_defs`). `FileWatcher` inotify+polling hot reload. `ResourceResolver` override merge. MCP-based unified interface via local MCP Server (stdio JSON-RPC) — aggregates local + external resources. | Hierarchical override merging; MCP multi-source Provider; cross-reference validation |
| 3 | **[Permission Control](docs/tool-sandbox.md)** | ACL + capability bits | `SessionModeManager` (auto/ask/plan) + `PermissionRegistry` deny→ask→allow enforcement. Per-agent `policy` override. `deny_patterns` regex matching. | OAuth-scoped permissions; role-based access control |
| 4 | **[Security Audit](docs/tool-sandbox.md)** | Protection rings (Ring 0-3) | `PathCheckToolGuard` — recursive scan (.., symlink, depth/count quota). `ContentGuard` — pre/post execution + pre-output rule-based screening. `GuardDefaults` three-line defense. | Per-invocation sandbox; content-aware scanning |
| 5 | **[Executor (Sandbox)](docs/tool-sandbox.md)** | Process isolation (chroot/namespace) | `SandboxManager` — per-session isolated workspace, configurable blacklist, auto-destroy. `ConcurrentToolExecutor` parallel execution. `FunctionBackend` with optional `rollback()`. | Container-based sandbox; resource quotas |
| 6 | **[Control Plane](docs/agent-execution.md)** | Process scheduler + signals | `GraphEngine` single `_execute` path. `LoopStrategy` ReAct pattern + TODO tracking. State management (runtime session state). 9 Hook injection points (`session_start`, `round_start`, `pre_model_call`, `post_model_call`, `post_permission`, `pre_tool_exec`, `post_tool_exec`, `sandbox_persist`, `round_end`, `session_end`). | Plan-Execute loop strategy; pause/resume/checkpoint; multi-agent DAG |

### Plugin System — Reflex Arcs, Not Cognitive Modules

**Plugin ≠ Tool.** Tools are MCP-managed function resources the Agent calls. Plugins are behaviors mounted on Hook points — they fire automatically at framework lifecycle events, like biological reflex arcs. The framework runs without plugins; plugins add preset or customizable capabilities. Critically: when a plugin needs intelligence (memory extraction, context summarization), it calls a model through the standard `_call_model` interface — **the intelligence comes from the model, not from the plugin**.

| Plugin | Hook | Status | Description |
|--------|------|--------|-------------|
| **Memory** | `round_end` | DONE | Long-term memory extraction via system model, atomic write to `memory.md` |
| **TODO** | `round_start`, `round_end` | DONE | Task list tracking with reminder injection |
| **UNDO** | `round_end`, `sandbox_persist` | DONE | Round-level state + file rollback |
| ~~**Model Routing**~~ | `pre_model_call` | **DEPRECATED** | `TwoTierRouter` — cheap LLM classifies simple→flash, complex→pro. Deprecated in favor of direct model configuration. |
| **Human Loop** | `post_permission`, `pre_tool_exec` | DONE | SSE approval channel with 60s timeout |
| **Compaction** | `round_end` | DONE | `CompactionPlugin` — token-aware, 75% threshold, keeps last 8 msgs + LLM summary |
| **Checkpoint** | `round_end`, `session_end` | DONE | `CheckpointPlugin` — round snapshots + session archiving for undo/restore |
| **Trace** | all hooks (cross-cutting) | DONE | `TracePlugin` — JSONL event recording for debugging, replay, evaluation |
| **Evaluation** | offline | DONE | `EvalPlugin` — replay traces, compute metrics, diff reports |
| Planner | (deferred) | P1 | Task decomposition via system model |
| bash | (deferred) | P1 | Shell executor with injection safety |
| code_interpreter | (deferred) | P1 | Python sandbox |

### Deprecated / Deferred

| Module | Action | Reason |
|--------|--------|--------|
| A2A Communication (`arf/communication/`) | Deprecated | Focus on agent+subagent first |
| TaskScheduler (`arf/concurrency/`) | Deprecated | Single-agent execution only |
| Plan-Execute strategy | Deferred | ReAct + TODO sufficient for now |

### Framework vs. Application

**Boundary principle**: The framework provides the mechanism (how); the application decides what to do through configuration + instantiation. `agent.yaml` is the bridge — the framework reads it and auto-assembles all capabilities; the app declares "what to use" without needing to know "how to implement."

| Layer | Scope | Capabilities |
|-------|-------|-------------|
| **Framework** (`arf/`) | **6 Skeletons** | **Prompt Assembly** — `SystemPromptProvider` (prefix + suffix + `$INVENTORY` template). **Resource Registry** — MCP-based unified interface, `ResourceResolver`, `FileWatcher` hot reload. **Permission Control** — `SessionModeManager` + `PermissionRegistry` deny→ask→allow. **Security Audit** — `PathCheckToolGuard`, `ContentGuard` rule-based screening. **Executor** — `SandboxManager` per-session isolation, `ConcurrentToolExecutor` parallel exec. **Control Plane** — `GraphEngine`+`LoopStrategy` ReAct, State management, 9 Hook injection points. |
| | **Plugins** | `InProcessHookRunner` executes `PluginProtocol` instances on lifecycle hooks. Built-in: `CompactionPlugin` (token-aware sliding window), `CheckpointPlugin` (round snapshots + session archive), `TracePlugin` (JSONL event recording), `EvalPlugin` (offline trace replay + metrics), `MemoryPlugin` (long-term extraction), `TodoPlugin` (task tracking), `UndoPlugin` (round rollback), `ModelRouterPlugin` (fast/slow dispatch), `HumanLoopPlugin` (SSE approval). |
| | **Infrastructure** | `ModelAdapter` exponential backoff + retry, `TokenBucket` rate limiting, `CircuitBreaker` fault isolation, `DefaultErrorPolicy`/`FunctionBackend` rollback, `SubprocessHookRunner` for external hook scripts, `SkillPipeline` dependency ordering |
| | **Protocols** | Protocol classes (`core/protocols/`) — defines `LoopStrategy`, `StateStore`, `ToolExecutor`, `PluginProtocol`, `HookRunner`, `GuardRunner`, `EventBus`, `ModelRouter`, and all other abstract interfaces |
| **Application** (`app/`) | **Frontend** | Vue 3 + TypeScript + Vite SPA, Pinia state management / VueRouter, ECharts charts / i18n (zh-CN + en-US), ChatPanel / TraceView / ResourcePanel and other components |
| | **HTTP Service** | FastAPI + Uvicorn + Streamable HTTP (NDJSON), REST endpoints (chat / trace / resources / config / usage …), WebSocket endpoint, CORS / SPA fallback / StaticFiles |
| | **CLI** | init / start / stop / chat / list / validate / config |
| | **Config & Data** | `agent.yaml` — model definitions (`model_defs`) + agent/subagent model refs (`agent_models`) + plugin config (`plugins_config`), custom `tools/` (file_*, web_*, python_exec …), custom `skills/`, custom `hooks/`, DeepSeek API key management |

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

### MCP Unified Resources

Tools and skills are accessed through a single MCP (Model Context Protocol) interface. A local MCP Server subprocess aggregates local filesystem resources (`tools/`, `skills/`, `plugins/`) with optional external MCP connections:

```yaml
# agent.yaml — optional external MCP servers
mcp_servers:
  - name: search
    transport: sse
    url: http://localhost:9000/sse
```

The agent communicates via stdio JSON-RPC. The app layer is source-agnostic — tool origins (local, plugin, remote) are transparent.

### Memory — Automatic Extraction & Retrieval

The app **does not** implement its own memory. Framework memory extraction lives in the [`memory` plugin](docs/plugins/memory.md) (`arf/plugins/memory/`) — mounted on `round_end` hook, configured with its own model via `plugins_config.memory.model`. Extracted facts, preferences, and decisions are written atomically to `memory.md` (≤300KB), loaded at session startup and injected into the system prompt.

```yaml
plugins_config:
  memory:
    model: deepseek-v4-flash        # model ref from model_defs
    interval: 5                      # extract every 5 rounds
    max_memory_size: 300             # KB limit for memory.md
```

[Design doc →](docs/plugins/memory.md)

### Compaction — Token-Aware Context Management

`CompactionPlugin` (mounted on `round_end` hook) monitors the previous turn's token usage. At 75% of the model's context window, it triggers: keeps the last 8 messages, summarizes older turns via LLM, and appends to `context_summary`. Long tool outputs (>2000 chars) are written to disk with a summary pointer in context. Configurable via `plugin.yaml`:

```yaml
# arf/plugins/compaction/plugin.yaml
config:
  threshold: 0.75
  window_size: 131072
  keep_count: 8
```

[Design doc →](docs/context-management.md)

### Model Configuration

Models are defined inline at the top of `agent.yaml`. The `model` field is the unique identifier. Agent and SubAgent reference models by name with ordered fallback; Plugins reference a single model.

```yaml
model_defs:                          # top-level definitions
  - model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY    # env var name, not the key value
    kwargs: {reasoning_effort: max}
  - model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs: {temperature: 0.7}

agent_models:                        # agent: ordered fallback [pro → flash]
  - model: deepseek-v4-pro
  - model: deepseek-v4-flash

plugins_config:                      # plugins: single model ref
  compaction:
    model: deepseek-v4-flash
  memory:
    model: deepseek-v4-flash
```

Reference with partial override (inherits from definition, overrides specified fields):
```yaml
agent_models:
  - model: deepseek-v4-pro
  - model: deepseek-v4-flash
    kwargs: {temperature: 0.0}      # override just temperature
```

Fallback triggers on 5xx, 429, and network errors. Client errors (4xx) do not trigger fallback.

### Sandbox & Permissions

`PathCheckToolGuard` blocks path traversal and absolute paths before every tool call. `SessionModeManager` controls the global permission mode (`auto` / `ask` / `plan`), with optional per-agent `policy` overrides. `PermissionRegistry` enforces deny→ask→allow lists. Tools run in-process; the guard checks every invocation.

Three session modes:
- **auto** — all tools execute directly, ignores permission lists
- **ask** — evaluate deny/ask/allow lists per tool; unknown tools require approval (default, recommended)
- **plan** — global read-only; all write/exec tools are denied (security review)

```yaml
session_mode: ask                # global mode: auto | ask | plan

advanced:
  guardrails:
    permissions:
      policy: ask                # per-agent override (auto/ask/plan), only active in global ask mode
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

`TracePlugin` (cross-cutting, mounted on all 9 hook points) records every lifecycle event to JSONL trace files. Each event carries `round` (user interaction) and `turn` (internal iteration). The waterfall view at `/traces` groups by round with expandable iterations: model response → tool calls → hooks. `UsageTracker` provides token stats. Standalone HTML viewer at `/trace-viewer`.

[Design doc →](docs/trace.md)

### Dual-Agent Architecture

User Agent handles your tasks. System Agent handles internal operations — resource creation, tool generation, validation. Separate execution, shared workspace. The user sees one assistant; the dual architecture is an implementation detail.

```yaml
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    models:
      - model: deepseek-v4-pro
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

**Hacking on the framework**: The framework is built on **6 skeletons** that run without plugins. Each skeleton maps to a Protocol. Plugins mount on lifecycle Hooks to extend functionality. Check the [TODO](#todo) section for pending fixes and evolution directions.

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
```

**Core stack:** Python 3.11+ · FastAPI · Vue 3 · TypeScript · Vite

<br/>

---

## Part III — Research Roadmap

ARF is the testbed for all five experiments proposed in the paper. This section maps each experiment to current ARF capabilities and identifies what needs to be built.

| Experiment | Paper Section | ARF Status | What Exists | What to Build |
|------------|--------------|------------|-------------|---------------|
| **E1: Fixed State Interface vs. Free-Text Prompts** — task stability comparison | §6.1 | **Testbed ready** | Resource Registry provides fixed-schema state assembly. `SystemPromptProvider` with `$INVENTORY` template already demonstrates structured context encoding. | Build a comparative benchmark: same tasks run through ARF's fixed StatePacket vs. traditional free-text prompts. Measure task completion stability (variance across runs). Candidate benchmarks: AgentBench, SWE-bench. |
| **E2: Zero-Cognition Harness Benchmark** | §6.2 | **Testbed ready** | All 6 skeletons implement the three mechanical layers. The full agent loop (ReAct + tools + guardrails) runs end-to-end. | Horizontal comparison: ARF vs. LangChain / OpenDevin on programming/CLI tasks. Metrics: code footprint, task completion rate, cognitive leakage points (modules that perform semantic interpretation outside model calls). |
| **E3: Online LoRA for Long-Term Memory** | §6.3 | **Extension point** | `MemoryPlugin` extracts facts to `memory.md`. `ModelAdapter` provides the model call abstraction. `FileMemoryStore` is the data source. | Add LoRA weight update interface to `ModelAdapter`. Use `memory.md` entries as training signal for online LoRA fine-tuning. Compare retention quality vs. external memory injection. |
| **E4: Post-Training Identity Boundary Robustness** | §6.4 | **Extension point** | `Guardrails` layer with `deny_patterns` regex matching. `SessionModeManager` enforces permission boundaries. `PathCheckToolGuard` blocks path traversal. | Extend guardrails for adversarial prompt testing. Build a jailbreak benchmark suite. Measure identity boundary preservation under prompt injection, role-play override, and few-shot manipulation attacks. |
| **E5: Internal Context Compression (Memory Tokens)** | §6.5 | **Extension point** | `CompactionPlugin` provides token-aware sliding window with LLM summarization. Token counting infrastructure exists. | Prototype memory-token-based compression: instead of external summarization, train the model to compress context into memory tokens internally. Compare compression fidelity against the current LLM-summary approach. |

**Running an experiment**: Each experiment is designed to be run against the same ARF testbed. The framework's `EvalPlugin` + `TracePlugin` provide unified data collection and metric computation. See [Eval Benchmark docs](docs/eval-benchmark.md) for the evaluation infrastructure.

**Research log**: `docs/paper/` (not yet created) will contain the paper framework, experiment protocols, and progressive results.

<br/>

---

## TODO

### Known Issues (2026-05-26 Fact Check)

| # | Title | Code Path | Domain | Type | Details |
|---|-------|-----------|--------|------|---------|
| 1 | ~~Engine `invoke`/`astream` duplication~~ → **FIXED** | `arf/engine/graph.py` | Process Scheduling | Framework | ~~~400 lines of identical Agent Loop logic in two methods.~~ → Extracted `_step_classify_tool_calls()` — guard pipeline, sandbox, permissions, and approval logic shared by both paths. |
| 2 | ~~`BaseAgent.__init__` oversized~~ → **FIXED** | `arf/agent/base.py` | Process Creation | Framework | ~~Constructor directly instantiates 20+ implementations inline.~~ → Extracted `_merge_models()` and `_build_resource_resolver()` factory methods; absorbs legacy `transaction_ctx` override. |
| 3 | ~~`server.py` monolithic~~ → **FIXED** | `app/arf_default_assistant/routers/` | User Interface | App | ~~REST routes, WebSocket, SSE streaming, CORS, file serving, state management, config APIs all in one file.~~ → Split into `routers/` by route group: `chat.py`, `trace.py`, `config.py`, `resources.py`, `misc.py`. `server.py` slimmed from 846→137 lines. |
| 4 | ~~`SnapshotRollback` null state snapshot~~ → **FIXED** | `arf/resources/backends/function.py` | Fault Recovery | Framework | ~~`begin()` always sets `"state_snapshot": None`~~ → Replaced with `FunctionBackend` inline rollback. |
| 5 | ~~`EvalRunner` computes empty traces~~ → **FIXED** | `arf/evaluation/runner.py` | Quality Assurance | Framework | ~~trace hardcoded as `{"turns": []}`~~ → Rewritten: captures real traces via `EventBus.events_since()`, all 4 metrics compute on real data. |
| 6 | ~~Global state `registry._agent`~~ → **FIXED** | `arf/agent/registry.py` removed | Process Isolation | Framework | ~~`_agent: Any = None` module-level singleton~~ → Deleted. Engine + state_store injected via tool executor params. |
| 7 | ~~`PromptBasedPlanner` returns empty plans~~ → **FIXED** | `arf/plugins/planner/` | Task Planning | Framework | Replaced by plugin system. `PluginProvider` scans plugin directories, `ResourceResolver` merges plugin tools/skills with app resources. |
| 8 | ~~SSE listener leak~~ → **FIXED** | `arf/streaming/adapters/sse.py` | Communication | Framework | Replaced with `@asynccontextmanager`: `async with stream.listen() as queue` — `__aexit__` guarantees cleanup. |
| 9 | ~~Inconsistent code conventions~~ → **FIXED** | 13 files + `graph.py` + `planner.py` | Documentation | Framework | All files have module docstrings. Core signatures use `dict[str, Any]`. `test_code_style.py` enforces consistency. |
| 10 | ~~No rate limiting / circuit breaker~~ → **FIXED** | `arf/protection/` | Process Scheduling | Framework | `ModelCallProtector` with `TokenBucket` + `CircuitBreaker`. See [`docs/api-protection.md`](docs/api-protection.md). |
| 11 | Missing open-source infrastructure | — | Distribution | Framework | No `CONTRIBUTING.md`, PR/Issue templates, `CHANGELOG.md`, or versioned release process. |

**Plugins** — Hook-mounted capability bundles. Each plugin has `plugin.yaml` (name + hooks + config) and `plugin.py` (PluginProtocol impl). `PluginLoader` scans `arf/plugins/{name}/`. Community-contributable. Plugin ≠ Tool — plugins fire on lifecycle hooks, tools are Agent-called MCP resources.

| # | Plugin | Status | Hook | Description |
|---|--------|--------|------|-------------|
| P-1 | `compaction` | DONE | `round_end` | Token-aware context compaction, 75% threshold + LLM summary |
| P-2 | `checkpoint` | DONE | `round_end`, `session_end` | Round snapshots + session archiving, restore support |
| P-3 | `trace` | DONE | all 9 hooks | JSONL event recording for debug, replay, evaluation |
| P-4 | `eval` | DONE | offline | Trace replay + metric computation + diff reports |
| P-5 | `memory` | DONE | `round_end` | Long-term memory extraction via system model, atomic write to `memory.md` |
| P-6 | `todo` | DONE | `round_start`, `round_end` | Task list tracking with reminder injection |
| P-7 | `undo` | DONE | `round_end`, `sandbox_persist` | Round-level state + file rollback |
| ~~P-8~~ | ~~`model_router`~~ | **DEPRECATED** | `pre_model_call` | TwoTierRouter fast/slow dispatch — deprecated |
| P-9 | `human_loop` | DONE | `post_permission` | SSE approval channel with 60s timeout |
| P-10 | `bash` | P1 | `pre_tool_exec` | Shell executor, community-audited injection safety |
| P-11 | `code_interpreter` | P1 | `pre_tool_exec` | Python sandbox |

### Evolution

See per-module design documents for evolution directions:
- [Context Management](docs/context-management.md) — semantic-unit compaction, adaptive threshold, cross-session summary reuse
- [Memory Plugin](docs/plugins/memory.md) — multi-round trigger, custom prompt templates
- [Resource Registry](docs/resource-registry.md) — hierarchical override merge, MCP multi-source Provider
- [Tool Sandbox](docs/tool-sandbox.md) — per-invocation sandbox, content-aware scanning
- [Skill Pipeline](docs/skill-pipeline.md) — multi-agent DAG, worktree isolation
- [Interrupt](docs/interrupt.md) — pause/redirect, idle timeout
- [Trace](docs/trace.md) — SQLite trace DB, OpenTelemetry export
- [Eval Benchmark](docs/eval-benchmark.md) — CLI integration, semantic similarity metrics

<br/>

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
