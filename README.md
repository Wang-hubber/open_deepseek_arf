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

**Companion project — [ARF App](https://gitee.com/dalaydata/arf_app_021)**: A 7-unit progressive tutorial that teaches building on ARF from zero to a production Agent — covering Hello ARF, session management, tools, approval, guardrails, memory, and agent tuning. Each unit includes runnable code snapshots. The tutorial doubles as user-acceptance testing for the framework, validating API design completeness through real usage.

<br/>

---

## Reading Guide

This document is organized in two parts plus research roadmap:

- **Part I — Framework**: The Brain-Spine-Body design model, the three mechanical layers, the 6-skeleton architecture, and the plugin system
- **Part II — Research Roadmap**: Mapping the paper's five experiments to current ARF capabilities and extension points
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

## Part II — Research Roadmap

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

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
