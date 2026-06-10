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

**The core thesis**: Nearly a decade after *Attention Is All You Need*, Agent systems have fallen into another "all you need" — everything is in context. Harness hardcodes loop strategies deciding "how to think." System Prompts define identity. RAG pipelines inject knowledge. memory.md files carry long-term memory. Natural language text serves as inter-Agent communication protocol. In-Context Learning became the universal hammer. The paper argues for a paradigm shift: behavior strategy, identity, knowledge, memory, and communication should migrate from the context window into model parameters — via LoRA adapters that can be hot-swapped, composed, and progressively updated at runtime. **Parameter Is All You Need.**

**The Harness role in this paradigm**: If parameters carry cognitive signals, what remains for the Harness? Two parallel lines: a **hard-line** (zero-cognition, never-changing — safety gating, archival, trace, action execution, hooks) and a **soft-line** (the progressive migration of strategy/identity/knowledge/memory/communication signals from ICL → LoRA MOE). The Harness is the spinal cord — it doesn't think, but it is the carrier on which learning happens.

**ARF's dual role**:
- **As an MVP**: ARF implements the hard-line in full — safety, error recovery, archival, tracing, evaluation, action execution, hook surface, and agent orchestration. The soft-line (LoRA MOE routing + online SFT pipeline) is the target of experiments 1–4.
- **As a research scaffold**: ARF provides the unified testbed for all five experiments — five per-dimension validations (strategy, identity, memory, knowledge, TFlow communication) plus the culminating HOT (LoRA MOE) vs COLD (ICL-only) Harness comparison.

**Companion project — [ARF App](https://gitee.com/dalaydata/arf_app_021)**: A 7-unit progressive tutorial that teaches building on ARF from zero to a production Agent — covering Hello ARF, session management, tools, approval, guardrails, memory, and agent tuning. Each unit includes runnable code snapshots. The tutorial doubles as user-acceptance testing for the framework, validating API design completeness through real usage.

<br/>

---

## Reading Guide

**For researchers** — interested in the architecture thesis and experimental roadmap:

- [Research Roadmap](#part-i--research-roadmap) → [Design Philosophy](#part-ii--framework) → [Three Layers of Rule-Based Reflexes](#three-layers-of-rule-based-reflexes) → [The Control Plane](#the-control-plane--structured-state--lifecycle)

**For framework users** — want to build on ARF:

- [Research Roadmap](#part-i--research-roadmap) → [Framework Skeletons](#harness-as-kernel--framework-skeletons) → [Control Plane](#the-control-plane--structured-state--lifecycle) → [Plugin System](#plugin-system--reflex-arcs-not-cognitive-modules) → then head to the [ARF App tutorial](https://gitee.com/dalaydata/arf_app_021) for a 7-unit step-by-step guide from zero to production Agent

<br/>

---

## Part I — Research Roadmap

ARF is the unified testbed for all five experiments proposed in the paper. The experiments are organized by the five problem domains identified in §2 Related Work — ordered by cognitive progression from model-internal to inter-model.

| Domain | Paper § | Research Question | Old Harness | New Harness | Experiment |
|--------|---------|-------------------|-------------|-------------|------------|
| **Behavior Strategy** | §2.1 · §5 (framework) | Should the model choose its own reasoning strategy, rather than Harness hardcoding ReAct/Plan-Solve? | Harness hardcodes loop strategies in engine code — deciding "how to think" for the model. | Harness collects and provides training data; strategy selection migrates to model-endogenous. | — (Baseline) Survey: [behavior-strategy](docs/paper/reading_summary/2.1-behavior-strategy/) |
| **Identity** | §2.2 · §5.2 | Can Identity LoRA frozen personas resist jailbreak better than System Prompt injection? | System Prompt defines role boundaries — task-independent abstract norms (who I am, behavioral limits, capability boundaries). | Harness provides identity switching; identity norms fixed via parameterization as the model's behavioral baseline. | **E2: Identity Boundary Robustness** Survey: [identity](docs/paper/reading_summary/2.2-identity/) |
| **Memory** | §2.3 · §5.1, §5.3 | Does parameterized memory (online SFT → LoRA B-matrix) outperform external injection (memory.md + vector DB)? | memory.md + vector DB external injection for preferences, environment changes, task-scene abstractions. | Harness provides trigger points for memory extraction and injection; memory is endogenous, parameterized. | **E1: Online LoRA Long-Term Memory** · **E3: Parametric Context Compression** Survey: [memory](docs/paper/reading_summary/2.3-memory/) |
| **Knowledge** | §2.4 · §5.3 | Does Knowledge LoRA outperform RAG for stable domain knowledge, while retaining ICL for dynamic high-frequency updates? | RAG injects facts and scene-specific rules via context window — every inference re-injects. | Harness provides trigger points for knowledge injection; dynamic high-frequency data retains ICL channel. | **E3: Parametric Context Compression** Survey: [knowledge](docs/paper/reading_summary/2.4-knowledge/) |
| **A2A Communication** | §2.5 · §5.4 | Does weight-space perturbation (TFlow) outperform NL text in latency and bandwidth for Agent-to-Agent exchange? | Agents exchange NL text — sparse, human-facing, serialized through LLM inference. | Harness provides parameter-level communication pipeline — in-memory object passing, not text generation. | **E4: TFlow Weight-Space Communication** Survey: [communication](docs/paper/reading_summary/2.5-agent-communication/) |
| **Culmination** | §5.5 | Does "Parameter Is All You Need" beat "Context Is All You Need"? | Same ARF hard-line. Two soft-line configs: A (ICL-only, 5 dimensions injected via context) vs. B (all five LoRA adapters active, low context). | **E5: HOT LoRA MOE vs. COLD ICL Harness** — same task, same base model, 6-dimensional scoring. |

**Running an experiment**: Each experiment runs against the same ARF testbed. `EvalPlugin` + `TracePlugin` provide unified data collection and metric computation. See [Eval Benchmark docs](docs/eval-benchmark.md).

**Research log**: [`docs/paper/`](docs/paper/) — paper framework, reading summaries by domain, and progressive research notes.

**Thesis**: As models grow stronger, less information needs to be injected via ICL — the Harness gets thinner. At the same time, the migration from ICL to Parameter tightens the coupling between model and Harness: training data collection, injection trigger points, identity switching, and parameter-level communication pipelines all depend deeply on the specific model's architecture and post-training interfaces. What thins is the cognitive burden. What tightens is the engineering integration.

<br/>

---

## Part II — Framework

### Harness as Kernel — Framework Skeletons

> **Model + Harness = Agent. CPU + Kernel = Computer.**
>
> Token is the instruction. Agent session is the process. Tool call is the system call.

Regardless of how the soft-line evolves — whether identity, knowledge, memory, and communication signals are injected via ICL or LoRA MOE — the Harness hard-line must provide six cross-paradigm capabilities. Four are **control items** that directly shape Agent runtime behavior. Two are **non-control items** — paradigm-independent enterprise infrastructure that don't affect execution, but determine whether the framework is production-viable.

**Control Items (Runtime Skeletons)**

| # | Skeleton | Responsibility | ARF Implementation | Evolution |
|---|----------|---------------|-------------------|-----------|
| 1 | **[Context Assembly](docs/prompt-assembly.md)** | Assemble system instructions, task descriptions, tool inventories, memory summaries into structured context. Even when large identity prompts and memory injections are no longer needed, temporary/test corrections, real-time runtime state, available resources, and meaningful environment feedback still require ICL injection. | `SystemPromptProvider` — prefix + suffix (`$INVENTORY` template). `string.Template` placeholders. Per-turn engine replacement. | Multi-agent prompt composition; role-based template dispatch |
| 2 | **[Resource Discovery & Registration](docs/resource-registry.md)** | Discover and hot-reload Tools and Skills. Tools and Skills are cross-model — the tool ecosystem needs the framework regardless of model evolution. | Convention over configuration: `tool.yaml`+`function.py`. `FileWatcher` hot reload. MCP Server subprocess (stdio JSON-RPC) aggregates local + external resources. | Hierarchical override merging; MCP multi-source Provider |
| 3 | **[Action Execution](docs/tool-sandbox.md)** | Tool calls, error recovery, reconnection retry, permission gating (deny→ask→allow), human approval, sandbox isolation, security audit. Mechanical execution should not occupy the LLM — rule-based judgments, zero cognition. | `SessionModeManager` + `PermissionRegistry`. `PathCheckToolGuard` + `ContentGuard`. `SandboxManager` per-session isolation. `FunctionBackend` + `rollback()`. | Container-based sandbox; OAuth-scoped permissions; content-aware scanning |
| 4 | **[Hook Surface](docs/agent-execution.md)** | Lifecycle-event-driven extensible mount points. Framework capabilities expand with experimental needs — new Plugins never modify core code. | 9 lifecycle Hook points (`session_start` ~ `error`). `pre_action`/`post_action` wrap every model call and tool execution. | Dynamic Hook registration; priority ordering |

**Non-Control Items (Enterprise Infrastructure — not affecting execution, enabling deployment)**

| # | Capability | Responsibility | ARF Implementation |
|---|-----------|----------------|-------------------|
| 5 | **Trace** | End-to-end tracing: every prompt, every action, every model call input/output. Observability is cognition-independent — execution records must be complete and replayable regardless of model strength. | `FileTraceStore` + `TracePlugin` |
| 6 | **Evaluation** | Regression benchmarking: A/B comparison, multi-dimensional metrics, session replay. Evaluation infrastructure doesn't care whether the subject is ICL or LoRA — only comparability matters. | `EvalRunner` + `BenchmarkBuilder` + `EvalComparator` |

### The Control Plane — Structured State & Lifecycle

The **[Control Plane](docs/agent-execution.md)** is the shared orchestration surface for the four control skeletons — unified `_execute` path, Plugin mount coordination, session lifecycle management.

| Aspect | Implementation |
|--------|---------------|
| **Execution engine** | `ControlPlane` single `_execute` path. `LoopStrategy` ReAct pattern + TODO tracking |
| **Structured State** | Fixed-schema State Packets assembled at each turn. Checkpoint/restore via `RoundManager` (3 rolling snapshots). Session lifecycle: create → resume → archive |
| **Hook surface** | 9 lifecycle points (`session_start`, `round_start`, `turn_start`, `pre_action`, `post_action`, `turn_end`, `round_end`, `session_end`, `error`) — the 9 points where Plugins mount. `pre_action`/`post_action` wrap every model call and tool execution |

### Plugin System — Reflex Arcs, Not Cognitive Modules

**Plugin ≠ Tool.** Tools are MCP-managed function resources the Agent calls. Plugins are behaviors mounted on Hook points — they fire automatically at framework lifecycle events, like biological reflex arcs. The framework runs without plugins; plugins add preset or customizable capabilities. Critically: when a plugin needs intelligence (memory extraction, context summarization), it calls a model through the standard `_call_model` interface — **the intelligence comes from the model, not from the plugin**. See [Plugin Overview](docs/plugins/overview.md) for the full architecture. See [Plan-Solve](docs/plan-solve.md) for the DAG-driven multi-step execution plugin.



<br/>

---

## Evolution

### Short-term: Literature Review & Theory Validation

Compute-constrained. Focus on literature survey across the five soft-line dimensions:

- **Behavior Strategy**: ReAct, PS Prompting, AutoGen, MetaGPT — can strategy selection be model-endogenous?
- **Identity**: Character-LLM, Neeko, RoleLLM — adversarial robustness of LoRA-frozen personas
- **Memory**: PEAM, TMEM — extend to Memorizing Transformer, Unlimiformer, MemGPT
- **Knowledge**: P-RAG, MEGa — RAG vs. Fine-tuning systematic comparison; dynamic ICL retention
- **Communication**: TFlow — weight-space perturbation stability at scale
- **Ablation design**: Finalize E1/E2/E3 — variable control, benchmarks, metrics

See [reading notes](docs/paper/reading_summary/).

### Medium/Long-term: LoRA MOE + Online SFT Pipeline

When compute is available:

- LoRA MOE router — per-domain adapters (strategy/identity/knowledge/memory/comm), hot-swap at runtime
- Online SFT pipeline — dual-buffer LoRA B-matrix, async non-blocking updates
- E5 culmination — HOT LoRA MOE Harness vs. COLD ICL Harness, six-dimensional scoring

---

<p align="center">
  <sub>MIT — see <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
