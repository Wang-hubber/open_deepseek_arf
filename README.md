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

Rather than reinvent the wheel per project, abstract once. Two things ship together — **[ARF](https://github.com/Wang-hubber/open_deepseek_arf) (this repo)** provides the engine and infrastructure, and a companion tutorial app provides progressive walk-throughs. The old Python-version tutorial (`arf_app`, 14 units) shipped alongside v0.x and remains as historical reference; **a Rust-version tutorial is now being developed** to match V1.x's architecture.

---

## Abstract: a three-layer model of Agent runtime

ARF V1.x splits Agent runtime into three layers — **Agent** holds state, **Engine** drives the ReAct main loop, **Bus Actors** provide capability. All three communicate **exclusively through Bus messages** — no direct calls:

```
┌──────────────────────────────────────────────────────┐
│                       Agent                           │
│  State — messages + tasks (with two-way locks        │
│          blocked_by / blocking)                      │
│  passive state machine, driven by Engine via Bus msgs │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: ActionMessage (Query / Command)
┌────────────────────────┴─────────────────────────────┐
│                       Engine                          │
│  GraphEngine — ReAct main loop (turn / round / session) │
│  CheckpointRule — subscribed lifecycle rules          │
│                (replaces hardcoded hooks)             │
│  Route — Strict (Capability → NodeId)                 │
│        / Discovery (broadcast by message type)        │
│  Park/Resume — Query auto-parks; reply auto-wakes     │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: Node online / heartbeat / Capability broadcast
┌────────────────────────┴─────────────────────────────┐
│              Bus Actors (capability providers)        │
│  ModelAdapter · MCP · Pool · Memory · Eval · ...     │
│  Each actor declares its Capabilities on connect,     │
│  subscribes to the message types it can serve         │
└──────────────────────────────────────────────────────┘
```

**Core principle: the Engine never calls any component directly — every interaction is a Bus message.**

### Design Highlights

**Protocol interfaces + EngineBuilder assembly.** All capabilities declare contracts via `Protocol` / Rust traits (`ActionMessage`, `Resource`, `OnMemberFailedHandler`, ...), assembled by `EngineBuilder` from an `AgentConfig`. `EngineBuilder::new(bus).build(config).await?` returns a runnable Engine. Applications describe the system through declarative `AgentConfig` (models / tools / checkpoint_rules / on_member_failed / ...); the framework knows only configuration, never concrete classes.

**Engine = an intelligent node on the Bus.** The Engine itself is a node on the Bus. It holds no references to MCP / ModelAdapter / other Actors — it **subscribes to `CheckpointRule`s, observes State changes, produces `ActionMessage`s, and routes them via `Route` to target actors**. This is V1.x's central shift: no direct method calls between Engine and other components; every collaboration is an asynchronous message.

**CheckpointRule replaces hardcoded hooks.** v0.x's 10 checkpoints were hardcoded event-trigger points in the engine; V1.x converts them to **subscribable rules** — a `CheckpointRule { trigger, when, build, route }` quadruple, all declared in `AgentConfig`. On State change, the Engine `evaluate(rules, routes, graph, cache)` — a pure function returning a list of `(ActionMessage, recipients)` — then dispatches each. `trigger` keeps familiar values: `SessionStart` / `BeforeRound` / `BeforeModel` / `AfterModel` / `BeforeTools` / `AfterTools` / `AfterRound` / `BeforeBreak` / `OnError` / `SessionEnd` — but the semantics shift from "framework fires a callback" to "rule subscribed by the application", freely composable.

**Route: Strict and Discovery.** `Route` decides the recipients of an `ActionMessage`:
- **Strict** — Resolve a `Capability` to concrete `NodeId`s (e.g. `model_call` → a specific `MiniMaxProvider` node). The Engine maintains a `DiscoveryCache` (Capability → Vec\<NodeId\>), invalidated on `node_online` / `node_offline`.
- **Discovery** — Broadcast to all nodes that declared subscription to a message type.

**MessageIntent: Query and Command.** `ActionMessage::intent()` is `Query` or `Command`, deciding Engine behavior after dispatch:
- **Query** — Engine parks the current ReAct turn, awaits a response; on reply, auto-resumes and continues. This is the natural shape for `model_call` — must wait for the model.
- **Command** — Fire-and-forget. The Engine does not await a response and proceeds immediately. Fits side-effecting messages (trace writes, state broadcasts).

**Park/Resume driven by Query.** Unlike v0.x's `WaitItem` + manual `wait()` / `finish_wait()`, V1.x's park/resume is the **natural consequence of message intent** — Engine emits a Query, automatically parks; the first matching reply arrives, automatically resumes. There is no "explicit waiting" state variable in the Engine state machine.

**Bus is the registry.** Actors come online via `bus.connect(node_online_with_capabilities)`, broadcasting their `Capability` list; the Engine maintains the `DiscoveryCache` (Capability → NodeId). On `node_offline`, the cache `invalidate()`s. No file-system scanning, no YAML loading, no hot-reload — the Bus node lifecycle **is** the registry update mechanism.

**Multi-Bus coordination: BarrierReceipt.** Cross-Bus scenarios (top Bus ↔ MCP sub-Bus) synchronize via `Bus::barrier(messages)`, returning a `BarrierReceipt` confirming all deliveries; used by the `domain_controller` example for facade forwarding.

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

## Real-world validation: tutorial app (under development)

> **Status (2026-07-01):** The companion tutorial app for V1.x is **under development**. The framework core (Rust crates + py-arf binding) is complete and tested; tutorial units are being authored to match.

The old Python-version tutorial ([arf_app](https://github.com/Wang-hubber/arf_app), 14 units, v0.x lineage) remains available as historical reference. A new Rust-version tutorial will progressively cover:

| Unit (planned) | Topic | Framework capability verified |
|----------------|-------|-------------------------------|
| 01 | Hello ARF (V1.x) | Engine assembly, ReAct boot |
| 02 | Bus basics | Message send/receive, online graph |
| 03 | State lifecycle | messages + tasks, two-way locks |
| 04 | ModelAdapter | OpenAI / Anthropic / DeepSeek / MiniMax providers |
| 05 | MCP integration | Local + Remote + Script tools |
| 06 | Hook system | 10 checkpoints, blocking vs side modes |
| 07 | Park/Resume | WaitItem, cross-session recovery |
| 08 | Checkpoint + Route | ActionMessage, Strict vs Discovery routing |
| 09 | Pool | Node pooling, resource leasing |
| 10 | Eval | Rule + LLM-judge metrics |

> Unit list above is provisional and will be refined as V1.x tutorial development progresses.

When the new tutorial ships, every framework module will have a real-world scenario validating it — **not "implemented the feature", but "someone used it to ship complete business"**, same standard as the v0.x tutorial.

---

## License

MIT — see [LICENSE](LICENSE)

<p align="center">
  <sub>ARF framework · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; Companion tutorial · under development (V1.x)</sub>
  <br/>
  <sub>Built with Rust · Python · DeepSeek · MiniMax</sub>
</p>