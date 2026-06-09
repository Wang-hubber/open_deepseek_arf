<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
  <p align="center"><em>A Research Scaffold & Harness MVP for &ldquo;Parameter Is All You Need&rdquo;</em></p>
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

<h3 align="center">Parameter Is All You Need — A New Paradigm for Harness</h3>
<p align="center">Hard-line (zero-cognition, never-changing) + Soft-line (ICL → LoRA MOE progressive internalization)</p>
<p align="center">Local-first. Convention over configuration. Fully traceable. Self-evolving.</p>

<br/>

> **Built by DeepSeek V4 Pro & Claude Code.** The author provided design direction and code review only — not a single line was manually written.

<br/>

## Research Context

ARF is the **engineering companion** to the research paper [*"Parameter Is All You Need — A New Paradigm for Harness"*](docs/paper/framework.md).

**The core thesis**: Nearly a decade after *Attention Is All You Need*, Agent systems have fallen into another "all you need" — everything is in context. System Prompts define identity, RAG pipelines inject knowledge, memory.md files carry long-term memory, natural language text serves as inter-Agent communication protocol. In-Context Learning became the universal hammer. The paper argues for a paradigm shift: identity, knowledge, memory, and communication should migrate from the context window into model parameters — via LoRA adapters that can be hot-swapped, composed, and progressively updated at runtime. **Parameter Is All You Need.**

**The Harness role in this paradigm**: If parameters carry cognitive signals, what remains for the Harness? Two parallel lines: a **hard-line** (zero-cognition, never-changing — safety gating, archival, trace, action execution, hooks) and a **soft-line** (the progressive migration of identity/knowledge/memory/communication signals from ICL → LoRA MOE). The Harness is the spinal cord — it doesn't think, but it is the carrier on which learning happens.

**ARF's dual role**:
- **As an MVP**: ARF implements the hard-line in full — safety, error recovery, archival, tracing, evaluation, action execution, hook surface, and agent orchestration. The soft-line (LoRA MOE routing + online SFT pipeline) is the target of experiments 1–4.
- **As a research scaffold**: ARF provides the unified testbed for all five experiments — four per-dimension validations (memory, identity, compression, TFlow communication) plus the culminating HOT (LoRA MOE) vs COLD (ICL-only) Harness comparison.

**Companion project — [ARF App](https://gitee.com/dalaydata/arf_app_021)**: A 7-unit progressive tutorial that teaches building on ARF from zero to a production Agent — covering Hello ARF, session management, tools, approval, guardrails, memory, and agent tuning. Each unit includes runnable code snapshots. The tutorial doubles as user-acceptance testing for the framework, validating API design completeness through real usage.

<br/>

---

## Reading Guide

**For researchers** — interested in the architecture thesis and experimental roadmap:

- [Design Philosophy](#design-philosophy-the-brain-spine-body-model) → [Three Layers of Rule-Based Reflexes](#three-layers-of-rule-based-reflexes) → [The Control Plane](#the-control-plane--structured-state--lifecycle) → [Part II — Research Roadmap](#part-ii--research-roadmap)

**For framework users** — want to build on ARF:

- [5-Skeleton Architecture](#harness-as-kernel--5-skeleton-architecture) → [Plugin System](#plugin-system--reflex-arcs-not-cognitive-modules) → then head to the [ARF App tutorial](https://gitee.com/dalaydata/arf_app_021) for a 7-unit step-by-step guide from zero to production Agent

<br/>

---

## Part I — Framework

### Design Philosophy: The Brain-Spine-Body Model

A model is raw compute — powerful, but not a computer. It needs memory management, process scheduling, interrupt handling, a file system, and security boundaries. ARF provides those. But the design goes deeper than an OS analogy.

**The biological mapping that governs every architectural decision:**

| Biological System | Agent System | Responsibility | Cognitive Load |
|-------------------|-------------|----------------|----------------|
| **Brain** | Large Language Model | Conditioned reflex center. Receives encoded state packets, outputs action instructions. Post-training internalizes professional knowledge and identity boundaries. | **Full cognitive** — understanding, reasoning, deciding |
| **Brainstem / Spine** | Harness Core (5 Skeletons + Control Plane) | Fixed perception encoding, reliable action execution, unconditioned reflex. Multi-source signals time-aligned into fixed-schema State Packets. Function calls parsed, executed, feedback collected — no policy judgment. | **Zero cognition** — format, align, execute, guard |
| **Body** | Tool Ecosystem | The physical/virtual effectors the model operates through. File I/O, web fetch, shell execution, code interpretation. | **Zero cognition** — mechanical action only |

**The co-evolution imperative**: A monkey brain cannot operate a human body. The state schema defined by the Harness must become the model's native perception language through paired post-training. Brain and body co-evolve, or neither works.

The primitives of operating systems — virtual memory, cache hierarchies, system calls, protection rings — map directly onto the implementation. But the architecture thesis is biological: **cognitive work belongs to the brain; the harness is spinal cord, not a second brain.**

### Three Layers of Rule-Based Reflexes

ARF implements the Harness as three layers of rule-based reflexes — simple, deterministic, zero-cognition. Like biological reflexes (knee-jerk, hand-withdrawal), each layer transforms input to output via fixed rules with no semantic understanding. These layers correspond to Section 5 of the paper ("Design Principles for the Ideal Harness").

| Layer | Principle | Implementation in ARF | Skeletons |
|-------|-----------|----------------------|-----------|
| **L1: Fixed Perceptual Encoder** | Zero-cognition state encoding. Fixed period, fixed schema, format + time alignment only. No questioning, no clarification, no understanding. | `SystemPromptProvider` assembles structured context from fixed template. `ResourceResolver` + `FileWatcher` discover and hot-reload tools/skills/models by filesystem convention — no semantic interpretation. | [#1 Prompt Assembly](docs/prompt-assembly.md), [#2 Resource Registry](docs/resource-registry.md) |
| **L2: Reliable Action Executor** | Reversible action execution. Preview-execute-rollback cycle ensures execution damage is recoverable. Parallel execution with dependency ordering. | `SandboxManager` provides per-session isolated workspaces. `ConcurrentToolExecutor` runs independent tool calls in parallel. `FunctionBackend` supports optional `rollback()` per tool. | [#5 Executor](docs/tool-sandbox.md) |
| **L3: Unconditioned Reflex** | Hardcoded safety. Permission gating, withdrawal reflex, rhythmic checkpointing — independent of model decisions. The model cannot bypass these. | `PathCheckToolGuard` blocks path traversal + absolute paths. `ContentGuard` screens pre/post execution. `SessionModeManager` + `PermissionRegistry` enforce deny→ask→allow. `RoundManager` maintains rolling snapshots for undo. Cancel token checked every turn. | [#3 Permission Control](docs/tool-sandbox.md), [#4 Security Audit](docs/tool-sandbox.md), [Interrupt](docs/interrupt.md) |

**Design Principle**: Each layer is *mechanical* — it transforms, routes, gates, or records via fixed rules. None of them *understand*. When the framework needs "intelligence" (memory extraction, context summarization), it calls a model through a Plugin — never through core engine logic.

### The Control Plane — Structured State & Lifecycle

The three layers don't float in isolation. The **[Control Plane](docs/agent-execution.md)** is the orchestrating surface they all connect to — the Harness equivalent of a spinal cord that routes signals between brain and body.

| Aspect | Implementation |
|--------|---------------|
| **Execution engine** | `ControlPlane` single `_execute` path — all three layers converge here. `LoopStrategy` ReAct pattern + TODO tracking |
| **Structured State** | Fixed-schema State Packets assembled at each turn. Checkpoint/restore via `RoundManager` (3 rolling snapshots). Session lifecycle: create → resume → archive |
| **Hook surface** | 9 lifecycle points (`session_start`, `round_start`, `turn_start`, `pre_action`, `post_action`, `turn_end`, `round_end`, `session_end`, `error`) — the 9 points where Plugins mount. `pre_action`/`post_action` wrap every model call and tool execution |

If the three layers are reflex arcs, the Control Plane is the spinal cord that coordinates when each reflex fires, routes state between them, and provides the attachment surface for every Plugin.

### Harness as Kernel — 5-Skeleton Architecture

> **Model + Harness = Agent. CPU + Kernel = Computer.**
>
> Token is the instruction. Agent session is the process. Tool call is the system call.

ARF is built on **5 skeletons** — the minimum viable framework. Each skeleton maps to a Protocol. Together with the [Control Plane](#the-control-plane--structured-state--lifecycle), they form the complete Harness core. Everything else is a **Plugin** mounted on lifecycle Hook points.

*Click any skeleton name for the full design document.*

| # | Skeleton | OS Analogy | Current | Evolution |
|---|----------|------------|---------|-----------|
| 1 | **[Prompt Assembly](docs/prompt-assembly.md)** | Program loader (execve) | `SystemPromptProvider` — prefix (role + critical_rules) + suffix (`$INVENTORY` template). `string.Template` placeholders (`$MEMORY`, `$WORKSPACE`, `$TURN_BUDGET`). Per-turn replacement by engine. | Multi-agent prompt composition; role-based template dispatch |
| 2 | **[Resource Registry (MCP)](docs/resource-registry.md)** | File system + udev + systemd | Convention over configuration: `tool.yaml`+`function.py` per tool, `skills/*.yaml`. Models defined inline in `agent.yaml` (`model_defs`). `FileWatcher` inotify+polling hot reload. `ResourceResolver` override merge. MCP-based unified interface via local MCP Server (stdio JSON-RPC) — aggregates local + external resources. | Hierarchical override merging; MCP multi-source Provider; cross-reference validation |
| 3 | **[Permission Control](docs/tool-sandbox.md)** | ACL + capability bits | `SessionModeManager` (auto/ask/plan) + `PermissionRegistry` deny→ask→allow enforcement. Per-agent `policy` override. `deny_patterns` regex matching. | OAuth-scoped permissions; role-based access control |
| 4 | **[Security Audit](docs/tool-sandbox.md)** | Protection rings (Ring 0-3) | `PathCheckToolGuard` — recursive scan (.., symlink, depth/count quota). `ContentGuard` — pre/post execution + pre-output rule-based screening. `GuardDefaults` three-line defense. | Per-invocation sandbox; content-aware scanning |
| 5 | **[Executor (Sandbox)](docs/tool-sandbox.md)** | Process isolation (chroot/namespace) | `SandboxManager` — per-session isolated workspace, configurable blacklist, auto-destroy. `ConcurrentToolExecutor` parallel execution. `FunctionBackend` with optional `rollback()`. | Container-based sandbox; resource quotas |

### Plugin System — Reflex Arcs, Not Cognitive Modules

**Plugin ≠ Tool.** Tools are MCP-managed function resources the Agent calls. Plugins are behaviors mounted on Hook points — they fire automatically at framework lifecycle events, like biological reflex arcs. The framework runs without plugins; plugins add preset or customizable capabilities. Critically: when a plugin needs intelligence (memory extraction, context summarization), it calls a model through the standard `_call_model` interface — **the intelligence comes from the model, not from the plugin**. See [Plugin Overview](docs/plugins/overview.md) for the full architecture.

## Part II — Research Roadmap

ARF is the testbed for all five experiments proposed in the paper. This section maps each experiment to current ARF capabilities and identifies what needs to be built.

| Experiment | Paper § | ARF Status | What Exists | What to Build |
|------------|---------|------------|-------------|---------------|
| **E1: Online LoRA Long-Term Memory** | §5.1 | **Extension point** | `MemoryPlugin` → `memory.md` + `ModelAdapter`. Datasets: LoCoMo, LongMemEval, self-built DFC. | Add LoRA B-matrix online SFT interface. Use memory entries as supervision. Sweep r=1–8. Compare parametric vs. external summary retention. |
| **E2: Identity Boundary Robustness** | §5.2 | **Extension point** | `Guardrails` + `deny_patterns` + `SessionModeManager`. Datasets: JailbreakBench, self-built Persona Conflict. | Train Identity LoRA from role-play data. Measure ASR, ICS, RQ against System Prompt baseline under adversarial attacks. |
| **E3: Parametric Context Compression** | §5.3 | **Extension point** | `CompactionPlugin` (token-aware sliding window + LLM summary). Dataset: LongBench QA. | Replace sync SFT with async dual-buffer LoRA B-matrix. Compare fidelity (F1) and latency (E2EPL) vs. LLM summarization. |
| **E4: TFlow Weight-Space Communication** | §5.4 | **Extension point** | `AgentBus` + `PeerAgent` + `ControlPlane.astream()`. Self-built DRSA simulation. | Implement perturbation compiler (internal activations → ΔW). Sweep sender count (2–32). Compare latency/bandwidth vs. NL text communication. |
| **E5 (Culmination): HOT LoRA MOE vs. COLD ICL Harness** | §5.5 | **Dependent on E1–4** | Same ARF hard-line. Two soft-line configs: A (ICL-only) vs. B (all four LoRA adapters active). | Same task, same base model, 4-dimensional scoring. Tests the title thesis: does "Parameter Is All You Need" beat "Context Is All You Need"? |

**Running an experiment**: Each experiment is designed to be run against the same ARF testbed. The framework's `EvalPlugin` + `TracePlugin` provide unified data collection and metric computation. See [Eval Benchmark docs](docs/eval-benchmark.md) for the evaluation infrastructure.

**Research log**: [`docs/paper/`](docs/paper/) contains the paper framework, reading summaries, and progressive research notes.

<br/>

---

## Evolution

### Short-term: Literature Review & Theory Validation

Compute-constrained. Focus on literature survey across the four soft-line dimensions:

- **Memory**: PEAM, TMEM ✅ — extend to Memorizing Transformer, Unlimiformer, MemGPT
- **Identity**: Character-LLM, Neeko, RoleLLM — adversarial robustness of LoRA-frozen personas
- **Knowledge**: P-RAG, MEGa — RAG vs. Fine-tuning systematic comparison
- **Communication**: TFlow — weight-space perturbation stability at scale
- **Capacity-aligned ablation design**: Finalize E1/E3 — variable control, benchmarks, metrics

See [reading notes](docs/paper/reading_summary/).

### Medium/Long-term: LoRA MOE + Online SFT Pipeline

When compute is available:

- LoRA MOE router — per-domain adapters (identity/knowledge/memory/comm), hot-swap at runtime
- Online SFT pipeline — dual-buffer LoRA B-matrix, async non-blocking updates
- E5 culmination — HOT LoRA MOE Harness vs. COLD ICL Harness, four-dimensional scoring

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
