<p align="center">
  <h1 align="center">ARF — AI Resources & Runtime Framework</h1>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./Cargo.toml"><img src="https://img.shields.io/badge/rust-1.81+-dea584?style=flat-square&labelColor=161b22&logo=rust&logoColor=white" alt="Rust 1.81+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<p align="center"><a href="./resume.md"><em>What I cannot create, I do not understand — author's notes</em></a></p>

<br/>

<p align="center">
  ▎ Two years of building AI applications, every project solving the same problems.<br/>
  ▎ This repository is one complete loop — from concrete to abstract and back.
</p>

<br/>

---

## What I cannot create, I do not understand.

For two years, every AI application I built asked the same questions — How does an Agent get scheduled? Where does multi-turn state live? When context overflows, how do we compress it? When I change one line of prompt, did it get better or worse? How do sub-agents delegate, communicate, and wait for results?

These questions have nothing to do with any specific business — but no project escapes them.

Rather than reinvent the wheel per project, abstract once. Two things shipped together — **[ARF](https://github.com/Wang-hubber/open_deepseek_arf) (this repo)** provides the engine and infrastructure, **[arf_app](https://github.com/Wang-hubber/arf_app)** provides a 14-unit progressive tutorial. One abstracts, one verifies; one builds wheels, one teaches people to use them.

---

## Abstract: a three-layer model of Agent runtime

ARF splits Agent runtime into three layers — **Agent** holds state, **Engine** drives execution, **Resources** provides capability:

```
┌──────────────────────────────────────────────────────┐
│                       Agent                           │
│  name + system_prompt + models                       │
│  passive message-state machine, driven by the engine  │
│  input() / model_call() / wait() / finish_wait()     │
└────────────────────────┬─────────────────────────────┘
                         │ dependency injection
┌────────────────────────┴─────────────────────────────┐
│                       Engine                          │
│  GraphEngine — ReAct main loop                       │
│  Hook scheduling — 10 lifecycle checkpoints           │
│  Park/Resume — wait / wake                            │
│  Trace — JSONL event stream                           │
└────────────────────────┬─────────────────────────────┘
                         │ file system + resource discovery
┌────────────────────────┴─────────────────────────────┐
│                    Resources                          │
│  tools/ · skills/ · models/ · hooks/                 │
│  FileWatcher hot-reload · ResourceResolver override  │
│  Guardrails path safety · path_check permission       │
└──────────────────────────────────────────────────────┘
```

**Core principle: the framework provides mechanism (how), applications decide what to do through configuration + instantiation.**

### Design Highlights

**Dependency Injection + Protocol interface segregation.** All capabilities declare contracts via `Protocol`, assembled via DI. `BaseAgent.__init__(**override_protocols)` can replace any implementation — the framework imports no concrete classes, only interfaces.

**Hooks: 10 checkpoints × 2 modes.** Plugins don't modify engine code. The engine fires events at 10 checkpoints; each supports two modes:

| Checkpoint | When |
|------------|------|
| `session_start` | Session begins / resume |
| `before_round` | Each `chat()` entry — park happens here |
| `before_model` | Before model call |
| `after_model` | After model response |
| `before_tools` | Before tool execution |
| `after_tools` | After tool execution, before commit (externalization here) |
| `after_round` | Round ends |
| `before_break` | Before engine break (`task_complete` validation here) |
| `on_error` | On exception |
| `session_end` | Session ends, cleanup |

- **blocking** — sequential; can mutate context or interrupt flow
- **side** — `asyncio.create_task` concurrent, fire-and-forget

**The file system is the registry.** Tools, skills, and models are directories + YAML config. `FileWatcher` hot-reloads — no restart needed. `git push` shares config.

**Two A2A communication modes.** Subagents (parent-child delegation, fire-and-forget) + Teammates (peer collaboration, full session park/wake loop).

**Park/Resume — wait and wake.** When an Agent waits for external input (user approval, peer reply, child agent completion), it suspends; events wake it. `WaitItem` records the reason and `resume_key`, supporting cross-session recovery.

---

## Engineering: Rust rewrite of V1.x

> The v0.x Python prototype proved what was right; v1.x's Rust type system and ownership model make it impossible to be wrong.

After v0.x shipped as a Python framework, V1.x rewrites the performance- and correctness-critical core in Rust and exposes a Python binding. The framework is now driven by **a single message bus** — everything is a message, everything is observable.

### Repository Layout

```
crates/          # Rust workspace (core framework)
  arf-core/      #   Protocol definitions + core types
  arf-bus/       #   Message bus (broadcast + filter + heartbeat)
  arf-state/     #   messages + tasks lifecycle, two-way locks
  arf-model-adapter/  # OpenAI / Anthropic / DeepSeek / MiniMax
  arf-mcp/       #   MCP tool bridge (Local + Remote + Script)
  arf-engine/    #   ReAct main loop + Checkpoint + Pool
  arf-agent/     #   DI assembly of all Protocol implementations
  arf-pool/      #   Node pooling
  arf-e2e/       #   Rust end-to-end tests

py-arf/          # Python binding (PyO3 + maturin, zero runtime deps)
examples/
  rust/          # Cargo workspace members (domain_controller, recovery)
  python/        # py-arf usage demos

docs/
  api/           # User API reference (PyTorch/LangGraph style)
  dev/           # Developer workflow, phase designs
  architecture/  # High-level design (session/round/turn, hooks, eval)
```

### V1.x Six Elements

| Element | Responsibility |
|---------|----------------|
| **Bus** | J-RPC broadcast, maintains online node graph, heartbeat / online / offline messages |
| **Engine** | Receive → call model → derive action → send. Never directly calls any component |
| **Agent** | State machine skeleton. Unaware of Bus / MCP / other Agents |
| **State** | `messages` + `tasks`; task has two-way locks (`blocked_by` / `blocking`), cascades release along the dependency chain |
| **MCP** | Listens for `tool_call` messages, executes, sends result; broadcasts tool list on online |
| **ModelAdapter** | Framework messages ↔ external API formats. Listens for `model_call` messages |

### Architectural Constraint: zero black box

For developers and debuggers, **everything is transparent and traceable**. Node online/offline, task create/block/wake/fail, model call send/return, tool request/result — all flow through Bus as messages, naturally traceable, debuggable, replayable. No implicit state transitions; no silent failures.

### Why Rust

Bus / Engine / State / AgentBus — four modules where performance and correctness both matter — are implemented in Rust. Python binds via PyO3. **Python prototypes proved what's right; Rust's type system makes it impossible to be wrong.**

Full design: **[docs/dev/v1.x-design.md](docs/dev/v1.x-design.md)**

---

## Quickstart

### Rust

```bash
cargo test --workspace
cargo run --bin domain_controller
cargo run --bin recovery
```

### Python

```bash
pip install -e ".[dev]"
. "$HOME/.cargo/env" && maturin develop --release
pytest tests/ -q
python examples/python/ex01_minimal_mock.py
```

---

## Documentation

| Document | Content |
|----------|---------|
| [`docs/api/`](docs/api/) | **User API reference** — Bus, ModelAdapter, MCP, Engine, AgentConfig, State |
| [`docs/dev/`](docs/dev/) | Developer workflow + phase designs (Phase 0-6) |
| [`docs/architecture/`](docs/architecture/) | Architectural concepts — session/round/turn, hooks, eval |

---

## Lessons learned: pitfalls from v0.x

Roughly 30% of v0.x's commit history is bug fix. That ratio *is* the story — **building, hitting edges, patching.** The deepest lessons:

**Engine — highest fix density.** ReAct's correctness is harder than expected. A `break` statement made the turn loop unreachable — all tests passed, but specific message sequences silently skipped the entire turn. After unifying park/resume, three regressions followed: message injection re-triggering park into a deadlock, partial wakeup losing messages, `cancel_event` not cleared across rounds. **State machine correctness doesn't depend on happy-path test coverage — it depends on exhaustive modeling of implicit side effects (break / cancel / park / message injection).**

**A2A + Teammates — most-patched area.** Deadlocks, race conditions, message consumer attribution mixed up. The park position migrated between `before_model`, `after_round`, `before_round` repeatedly — each fix introduced a new bug. Root cause: **the Agent / Engine boundary isn't clean.** Park scattered across engine, plugin, and Agent layers; multi-Agent concurrency tangled, global state unrepresentable.

**Path handling.** Double-join (`abspath` + `join`) silently produces wrong paths; relative paths fail to match sandbox whitelist. File paths differ from API calls — failure isn't an exception, it's "works here, crashes in another directory."

**Memory silent failure.** LLM memory extraction never triggered — parameter renamed `model=` to `model_name=`, exception swallowed by async task. Missing `mkdir` crashed on first run. **Background tasks need explicit error propagation; silent failure is the most dangerous failure mode.**

**ModelAdapter error swallowing.** API exceptions swallowed silently; empty string `api_key` rejected by SDK; `"false"` (string) evaluated truthy by Python, enabling thinking mode. **Python's dynamic typing + third-party SDK implicit behavior = errors the type system can't catch.** The fix is forcing explicitness — explicit error propagation, explicit falsy checks, explicit field defaults.

**Core lesson: framework correctness isn't tested into existence — it's enumerated into existence.** Test coverage proves "known scenarios passed", not "no scenarios missed". True quality comes from exhaustive review of every conditional branch — what state combinations can occur? Is every side effect correctly cleaned and reset?

---

## Real-world validation: arf_app

Framework correctness can't be self-asserted. **[arf_app](https://github.com/Wang-hubber/arf_app)** uses 14 progressive units to build, from scratch, an evaluable, evolvable Agent — each unit closes the loop "teaching goal → docs → code → verification":

| Unit | Topic | Framework capability verified |
|------|-------|-------------------------------|
| 01 | Hello ARF | Agent assembly, system prompt injection, session creation |
| 02 | Session management | Multi-session lifecycle, LLM auto-title |
| 03 | Tool introduction | File read/write, ReAct think→call→execute→respond |
| 04 | Tool approval | session_mode, approval event handling, runtime policy switch |
| 05 | Safety system | deny blacklist, regex interception, PathSandbox |
| 06 | Long-term memory | Memory plugin, cross-session identity persistence |
| 07 | Convergence Agent | Temperature, prompt and runtime optimization |
| 08 | Trace trajectory | JSONL output, trace command, foundation for Eval |
| 09 | Eval evaluation | Rule-based metrics, golden session → annotate → build → compare |
| 10 | LLM Judge | Model evaluating model, auto-annotate semi-pipeline |
| 11 | Version persistence | Version archive, auto-regression detection |
| 12 | Sub Agent | `delegate_task` dispatch ephemeral child Agents, parallel speedup |
| 13 | Agent Team | PM + Data + Viz trio, AgentBus peer collaboration |
| 14 | Skill | Single Agent + Skill + Subagents vs multi-person team comparison |

All 14 units run end-to-end. Every framework module has a real-world scenario validating it — **not "implemented the feature", but "someone used it to ship complete business".**

---

## License

MIT — see [LICENSE](LICENSE)

<p align="center">
  <sub>ARF framework · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; Companion tutorial · <a href="https://github.com/Wang-hubber/arf_app">arf_app</a></sub>
  <br/>
  <sub>Built with Rust · Python · DeepSeek · MiniMax</sub>
</p>