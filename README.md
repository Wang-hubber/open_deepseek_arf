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

Rather than reinvent the wheel per project, abstract once. This repository is the **framework + infrastructure** layer. The companion tutorial app (a 14-unit progressive walk-through built on top of this framework) is developed in a separate repository; an early Python-version prototype lives at [arf_app](https://github.com/Wang-hubber/arf_app) as historical reference.

---

## Design intent: a three-layer model of Agent runtime

ARF V1.x aspires to split Agent runtime into three layers — **Agent** holds state, **Engine** drives the ReAct main loop, **Bus Actors** provide capability. All three communicate **exclusively through Bus messages** — no direct calls:

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
│  EngineBuilder — assemble from AgentConfig            │
│  ReAct main loop (turn / round / session)             │
│  CheckpointRule — subscribed lifecycle rules          │
│                (replaces hardcoded hooks)             │
│  Route — Strict (Capability → NodeId)                 │
│        / Discovery (broadcast by Capability match)    │
│  Park/Resume — Query auto-parks; reply auto-wakes     │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: Node online / heartbeat / Capability broadcast
┌────────────────────────┴─────────────────────────────┐
│              Bus Actors (capability providers)        │
│  ModelAdapter · MCP · Pool · ...                      │
│  Each actor declares its Capabilities on connect,     │
│  subscribes to the message types it can serve         │
└──────────────────────────────────────────────────────┘
```

**Core principle: the Engine never calls any component directly — every interaction is a Bus message.**

### What's actually implemented (Phase 6 as of 2026-07-01)

The diagram above is **design intent**. Reality is one step behind — the implementation has converged toward this model but not all promises are kept yet:

| Design promise | Implementation status |
|---|---|
| Engine is a Bus node; never calls components directly | ✅ All `model_call` / `tool_exec` go through Bus |
| Protocol interfaces + DI assembly | ✅ `ActionMessage`, `Resource`, `Provider`, `CheckpointRule` traits |
| `AgentConfig` is the single declarative source of truth | ✅ Rust struct, default impl, no Python class |
| `CheckpointRule` replaces hardcoded hooks | ✅ 5 triggers: `BeforeModelCall`, `AfterModelCall`, `BeforeToolExec`, `AfterToolExec`, `RoundEnd` |
| `Route::Strict(Vec<NodeId>)` | ✅ |
| `Route::Discovery(Capability)` — broadcast to all matching nodes | ✅ Resolved via `DiscoveryCache` (Capability → Vec\<NodeId\>), invalidated on `node_online` / `node_offline` |
| `MessageIntent` — Query auto-parks, Command fire-and-forget | ✅ Both intents exist; `WaitStrategy::Any`/`All`/`Count(n)` controls park behavior |
| Park/Resume driven by Query, no explicit wait state | ✅ `publish_and_await_query` is the single park/resume path |
| Multi-Bus coordination via `Bus::barrier(...) → BarrierReceipt` | ✅ `barrier(participants: Vec<NodeId>, timeout: Duration) → BarrierReceipt` |
| `OnMemberFailedHandler` invoked on node failure | ⚠️ Trait + `AgentConfig.on_member_failed` field exist, but `Engine.run()` does **not** yet call it. `MemberFailedAction::Retry` / `SwitchTo` are declared but only `FailSession` is fully implemented. |
| 10 lifecycle hooks (v0.x style: session_start, before_round, on_error, …) | ❌ Replaced by 5 Checkpoint triggers above. The V0.x 10-hook system is fully gone — `docs/architecture/overview.md` still has a stale table that needs updating. |

### Design Highlights (as currently implemented)

**Protocol interfaces + EngineBuilder assembly.** All capabilities declare contracts via Rust traits (`ActionMessage`, `Resource`, `Provider`, `OnMemberFailedHandler`, …), assembled by `EngineBuilder` from an `AgentConfig`. `EngineBuilder::new(buses=[bus]).build(config).await?` returns a runnable Engine. Applications describe the system through `AgentConfig` (`agent_id`, `model_config`, `routes`, `checkpoint_rules`, `on_member_failed`, `tools_include` / `tools_exclude`, …); the framework knows only configuration, never concrete classes.

**Engine = an intelligent node on the Bus.** The Engine itself is a node on the Bus. It holds no references to MCP / ModelAdapter / other Actors — it **subscribes to `CheckpointRule`s, observes State changes, produces `ActionMessage`s, and routes them via `Route` to target actors**. This is V1.x's central shift: no direct method calls between Engine and other components; every collaboration is an asynchronous message.

**CheckpointRule replaces hardcoded hooks.** V1.x ships 5 fixed injection points — `BeforeModelCall`, `AfterModelCall`, `BeforeToolExec`, `AfterToolExec`, `RoundEnd` — instead of v0.x's 10 hardcoded lifecycle hooks. A rule is a 4-tuple:

```rust
pub struct CheckpointRule {
    pub name: String,
    pub trigger: Checkpoint,                              // one of the 5 above
    pub when:    Box<dyn Fn(&State) -> bool + Send + Sync>,
    pub build:   Box<dyn Fn(&State) -> Box<dyn ActionMessage> + Send + Sync>,
}
```

**No `route` field** — routes are single-sourced in `AgentConfig.routes: HashMap<String, Route>` (msg_type → Route). The Engine dispatches each rule's `build(state)` output via the route registered for that message's `msg_type`. On State change, the Engine evaluates matching rules, attaches each emitted message to its route, and dispatches.

**Route: Strict and Discovery.** `Route` decides the recipients of an `ActionMessage`:
- **Strict** — Pre-resolved list of `NodeId`s. Caller knows exactly which nodes should receive.
- **Discovery** — Engine looks up `DiscoveryCache(capability)` and fans out to all matching online nodes. The cache is invalidated on `node_online` / `node_offline`. Discovery resolves by **Capability**, not by msg_type broadcast — nodes still subscribe via `MessageFilter`, but Engine's view of recipients is capability-based.

**MessageIntent: Query and Command.** `ActionMessage::intent()` is `Query` or `Command`, deciding Engine behavior after dispatch:
- **Query** — Engine parks the current ReAct turn and waits (subject to `WaitStrategy`); on reply, auto-resumes. This is the natural shape for `model_call`.
- **Command** — Fire-and-forget. The Engine does not await a response and proceeds immediately. Fits side-effecting messages (trace writes, state broadcasts). Built-in `MemoryOp::extract` is one such example.

**Park/Resume driven by Query.** Unlike v0.x's `WaitItem` + manual `wait()` / `finish_wait()`, V1.x's park/resume is the **natural consequence of message intent** — Engine emits a Query, automatically parks; the first matching reply (subject to `WaitStrategy`) arrives, automatically resumes. There is no explicit "waiting" state variable in the Engine state machine.

**Bus is the registry.** Actors come online via `bus.connect(node_online_with_capabilities)`, broadcasting their `Capability` list; the Engine maintains the `DiscoveryCache` (Capability → NodeId). On `node_offline`, the cache `invalidate()`s. No file-system scanning, no YAML loading, no hot-reload — the Bus node lifecycle **is** the registry update mechanism.

**Multi-Bus coordination: BarrierReceipt.** Cross-Bus scenarios (top Bus ↔ MCP sub-Bus) synchronize via `Bus::barrier(participants: Vec<NodeId>, timeout: Duration)`, returning a `BarrierReceipt { correlation_id, acked, missing, timed_out }`. Used by the `domain_controller` example for facade forwarding across sub-Bus boundaries.

---

## Engineering: Rust rewrite of V1.x

> The v0.x Python prototype proved what was right; v1.x's Rust type system and ownership model make it impossible to be wrong.

After v0.x shipped as a Python framework, V1.x rewrites the performance- and correctness-critical core in Rust and exposes a Python binding. The framework is now driven by **a single message bus** — everything is a message, everything is observable.

### Repository Layout

```
crates/             # Rust workspace (framework core)
  arf-core/         #   Protocol traits + core types (Message, NodeId, Route, Checkpoint, …)
  arf-bus/          #   Message bus (broadcast + filter + heartbeat + barrier)
  arf-state/        #   messages + tasks lifecycle, two-way locks
  arf-model-adapter/#   OpenAI / Anthropic / DeepSeek / MiniMax Provider + Bus node
  arf-mcp/          #   MCP tool bridge (Local + Remote + Script)
  arf-engine/       #   ReAct main loop + Checkpoint evaluation + Route resolution
  arf-pool/         #   Generic Resource pooling + PoolNode integration
  arf-agent/        #   DI assembly of all Protocol implementations (scaffold)
  arf-e2e/          #   Rust end-to-end tests

py-arf/             # Python binding (PyO3 + maturin, zero runtime deps)
  src/              #   PyO3 module source
  python/arf/       #   Python package (re-exports arf._arf)
  tests/            #   Python binding unit + integration tests

examples/
  rust/             # Cargo workspace members
    domain_controller/   # McpFacade example (top Bus ↔ MCP sub-Bus forwarding)
    recovery/            # App-level recovery (AppCheckpoint + Bus::barrier + file persistence)
  python/           # py-arf usage demos (ex01-ex08 + phase0/1/4/6_overview)

docs/
  api/              # User API reference (PyTorch/LangGraph style)
  dev/              # Developer workflow + phase designs (Phase 0-6)
  architecture/     # High-level design notes (session data layout, A2A, park-resume, eval)
```

**`arf-agent` is currently a scaffold** — the actual Engine assembly lives in `arf-engine` via `EngineBuilder`. `AgentConfig` exists in two places (`arf-agent` and `arf-engine`); the engine-level one is the canonical runtime config.

### V1.x Six Elements

| Element | Responsibility | Crate |
|---|---|---|
| **Bus** | J-RPC broadcast, maintains online node graph, heartbeat / online / offline messages, `barrier()` cross-Bus sync | `arf-bus` |
| **Engine** | Receive → call model → derive action → send. Never directly calls any component | `arf-engine` |
| **Agent** | State machine skeleton (`State`, `OverView`). Unaware of Bus / MCP / other Agents | `arf-state` + `arf-core` |
| **State** | `messages` + `tasks`; task has two-way locks (`blocked_by` / `blocking`), cascades release along the dependency chain | `arf-state` |
| **MCP** | Listens for `tool_exec` messages, executes, sends `tool_result`; broadcasts tool list on online | `arf-mcp` |
| **ModelAdapter** | Framework messages ↔ external API formats. Listens for `model_call` messages | `arf-model-adapter` |

### Architectural Constraint: zero black box

For developers and debuggers, **everything is transparent and traceable**. Node online/offline, task create/block/wake/fail, model call send/return, tool request/result — all flow through Bus as messages, naturally traceable, debuggable, replayable. No implicit state transitions; no silent failures.

### Why Rust

Bus / Engine / State / ModelAdapter — the four modules where performance and correctness both matter — are implemented in Rust. Python binds via PyO3. **Python prototypes proved what's right; Rust's type system makes it impossible to be wrong.**

Full design draft: **[docs/dev/v1.x-design.md](docs/dev/v1.x-design.md)** (marked as 设计草案 — design draft; reflects the original vision, not the current implementation verbatim).

---

## Quickstart

### Rust

```bash
cargo test --workspace                       # all Rust crates + examples
cargo run --bin domain_controller           # McpFacade multi-Bus example
cargo run --bin recovery                    # App-level recovery example
```

### Python

```bash
pip install -e ".[dev]"                     # install Python package + dev deps
. "$HOME/.cargo/env" && maturin develop --release   # build PyO3 binding
pytest py-arf/tests/ -q                     # run Python binding tests
python examples/python/ex01_minimal_mock.py # minimal Engine example
make test                                   # or: runs both Rust and Python tests
```

> Note: `pytest tests/ -q` from the repo root runs against the placeholder `tests/` directory (intended for future cross-language integration tests, currently empty). The actual Python binding tests live in `py-arf/tests/`.

---

## Documentation

| Document | Content |
|----------|---------|
| [`docs/api/`](docs/api/) | **User API reference** — Bus, ModelAdapter, MCP, Engine, AgentConfig, State |
| [`docs/dev/`](docs/dev/) | Developer workflow + phase designs (Phase 0-6) |
| [`docs/architecture/`](docs/architecture/) | Architectural concepts — session data layout, A2A, park/resume, eval benchmark |

> ⚠️ **`docs/architecture/overview.md` is partly stale.** It still describes V0.x's 10-checkpoint Hook lifecycle (`session_start`, `before_round`, `on_error`, …), TracePlugin / CompactionPlugin / MemoryIndex — none of which exist in V1.x. The V1.x implementation has 5 `Checkpoint` triggers (see Design Highlights above). Treat `overview.md` as historical reference until it's rewritten to match the Rust code.

---

## Lessons learned: pitfalls from v0.x

Roughly 30% of v0.x's commit history is bug fix. These are **v0.x Python-framework lessons** that shaped V1.x's Rust design — not V1.x implementation bugs:

**Engine — highest fix density (v0.x).** ReAct's correctness is harder than expected. A `break` statement made the turn loop unreachable — all tests passed, but specific message sequences silently skipped the entire turn. After unifying park/resume, three regressions followed: message injection re-triggering park into a deadlock, partial wakeup losing messages, `cancel_event` not cleared across rounds. **The lesson:** state-machine correctness doesn't depend on happy-path test coverage — it depends on exhaustive modeling of implicit side effects (break / cancel / park / message injection). V1.x encodes this by making park/resume a function of message intent, not an explicit state variable.

**A2A + Teammates — most-patched area (v0.x).** Deadlocks, race conditions, message consumer attribution mixed up. The park position migrated between `before_model`, `after_round`, `before_round` repeatedly — each fix introduced a new bug. Root cause: **the Agent / Engine boundary wasn't clean.** Park scattered across engine, plugin, and Agent layers; multi-Agent concurrency tangled, global state unrepresentable. V1.x's response: collapse everything to one Bus, eliminate v0.x's separate subagent/teammate modes — peer message = a directed Bus message.

**Path handling (v0.x).** Double-join (`abspath` + `join`) silently produces wrong paths; relative paths fail to match sandbox whitelist. File paths differ from API calls — failure isn't an exception, it's "works here, crashes in another directory." V1.x delegates path handling to MCP nodes (each owns its own root), eliminating the cross-cutting concern.

**Memory silent failure (v0.x).** LLM memory extraction never triggered — parameter renamed `model=` to `model_name=`, exception swallowed by async task. Missing `mkdir` crashed on first run. **Background tasks need explicit error propagation; silent failure is the most dangerous failure mode.** V1.x's `Result`-based returns make this an unrepresentable state in safe Rust.

**ModelAdapter error swallowing (v0.x).** API exceptions swallowed silently; empty string `api_key` rejected by SDK; `"false"` (string) evaluated truthy by Python, enabling thinking mode. **Python's dynamic typing + third-party SDK implicit behavior = errors the type system can't catch.** The fix is forcing explicitness — V1.x's `Provider` trait requires explicit `Result` returns and explicit field defaults at the type level.

**Core lesson: framework correctness isn't tested into existence — it's enumerated into existence.** Test coverage proves "known scenarios passed", not "no scenarios missed". True quality comes from exhaustive review of every conditional branch — what state combinations can occur? Is every side effect correctly cleaned and reset? Rust's type system narrows the search space (no `except: pass`, no string-typed booleans) but doesn't eliminate it.

---

## Real-world validation: tutorial app (under development)

> **Status (2026-07-01):** The companion tutorial app for V1.x is **under development**. The framework core (Rust crates + py-arf binding) is implemented and tested through Phase 6 (`docs/dev/phase6/`); tutorial units will be authored once development starts.

The old Python-version tutorial ([arf_app](https://github.com/Wang-hubber/arf_app), 14 units, v0.x lineage) remains available as historical reference. Unit outline for the V1.x tutorial is held back until development begins, to avoid presenting draft content as a roadmap.

When the new tutorial ships, every framework module will have a real-world scenario validating it — **not "implemented the feature", but "someone used it to ship complete business"**, same standard as the v0.x tutorial.

---

## License

MIT — see [LICENSE](LICENSE)

<p align="center">
  <sub>ARF framework · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; Companion tutorial · under development (V1.x)</sub>
  <br/>
  <sub>Built with Rust · Python · DeepSeek · MiniMax</sub>
</p>
