# Phase 10 / 08 — Extensibility & Plugin Model: ARFV1 vs DeepAgents

> Atomic-level comparison covering the extension surface — Protocol-based DI vs
> Middleware-based DI, trait implementations, entry-point plugins, third-party
> registries. Every claim cites `path:line`.

---

## 1. Extension Model Philosophy

### ARFV1
- File(s): `crates/arf-core/src/node.rs:225-268`, `crates/arf-core/src/message.rs:43-57`, `crates/arf-core/src/route.rs:1-49`, `crates/arf-core/src/processor.rs:21-30`, `crates/arf-core/src/checkpoint.rs:7-103`.
- Implementation: Rust-native Protocol DI. Each capability dimension — `Node`, `ActionMessage`, `Route`, `Capability`, `ResponseProcessor`, `CheckpointRule`, `MessageHandler` — is a separate trait consumed through `Arc<dyn Trait>`. `EngineBuilder` (`crates/arf-engine/src/builder.rs`) and `Engine::add_handler` wire them at runtime. Closure fields like `CheckpointRule.{when, build}` use HRTB `for<'a>` so users can borrow `&'a State` freely (`checkpoint.rs:30`).
- Strengths: Type-safety per dimension (msg_type dispatch is `&'static str`, not dynamic). Zero-cost abstraction: monomorphized generics where possible, `Box<dyn Fn>` only for the closure trio. Compatibility surface is obvious — adding a new trait dimension doesn't ripple into existing call sites.
- Weaknesses: Requires `Send + Sync` everywhere (`node.rs:225`, `message.rs:44`, `processor.rs:21`); users must manually gate thread-safety. No first-class entry-point auto-discovery (no `pyproject.toml` equivalent in Cargo that wires plugins; users wire crates in via `Cargo.toml`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/__init__.py:1-104`, `libs/deepagents/deepagents/_state.py:11`, `libs/deepagents/libs/ARCHITECTURE.md:60-61`.
- Implementation: Middleware DI layered on `langchain.agents.middleware.AgentMiddleware`. Every ReAct seam — before/after model, before/after tool, wrap-tool, wrap-model-call — is a hook on `AgentMiddleware`. Custom state rides on `PrivateStateAttr` markers (`_state.py:11`) so middleware private state is isolated from public schema. Two plug surfaces: Backend (ABC at `backends/protocol.py:356`) and Middleware.
- Strengths: Single import-point mental model — "everything is a middleware." `PrivateStateAttr` keeps extension-internal state out of the user's reduced schema. File-friendly YAML/JSON via `HarnessProfileConfig.from_dict`.
- Weaknesses: Middleware ordering has subtle precedence traps (`ARCHITECTURE.md:60-61` "middleware vs plain tools" call-out). Hook granularity is fixed by LangChain's `AgentMiddleware` — adding a new ReAct phase means forking upstream or building a custom middleware that wraps the model call.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Document a 1-page "pick-the-trait" cheat sheet in `docs/v1.x/`; first-class entry-point discovery via a `plugin_manifest.toml` parsed at startup.

---

## 2. Custom Node Types (`Node` trait)

### ARFV1
- File(s): `crates/arf-core/src/node.rs:225-268`.
- Implementation: Four method slots — `id()`, `snapshot() -> Result<Value, SnapshotError>`, `async restore(Value)`, `async on_message(Message, BusId)`. Bus identifies nodes by `NodeId` convention `"{type}/{name}"` (`node.rs:228-231`). Snapshot is sync; restore is async because reconnect/re-spawn may need I/O.
- Strengths: Snapshot errors are NOT panics — they return `SnapshotError`, the barrier protocol adds the Node to `BarrierReceipt.missing` (`node.rs:241-242`). Restore refuses partial state via `VersionMismatch` / `InconsistentState`.
- Weaknesses: No declarative capability registration — `capabilities` is implicit from `Node::id()` returning a `NodeId` whose prefix encodes type.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py:737`, `libs/deepagents/deepagents/graph.py:353-1025`.
- Implementation: No `Node` equivalent. The graph layer is fixed; "subagent" is a tool (`task`) that compiles a separate agent and dispatches via `ToolMessage`. Async subagents are external (Agent Protocol server).
- Strengths: Simpler model — a Node is just a subagent factory plus a tool wrapper.
- Weaknesses: No snapshot/restore seam per subagent; cannot persist in-flight subagent state.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: Keep ARFV1's snapshot/restore seam.

---

## 3. Custom `ActionMessage` Types

### ARFV1
- File(s): `crates/arf-core/src/message.rs:43-57`.
- Implementation: `ActionMessage` carries `msg_type() -> &'static str`, `correlation_id() -> Uuid`, `payload() -> serde_json::Value`, and `intent() -> MessageIntent` (`Query | Command`). `async_trait`-based; `Send + Sync`.
- Strengths: Wire-stable `&'static str` for routing. Intent split (`Query` parks ReAct, `Command` is fire-and-forget — `message.rs:54-56`) keeps Engine's blocking semantics explicit.
- Weaknesses: No automatic JSON-deserialization on inbound — Engine reads `payload` as opaque JSON; type extraction is the receiver's responsibility.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/subagents.py`, `libs/deepagents/deepagents/graph.py:353-1025`.
- Implementation: Custom messages ride on LangChain's `ToolMessage` / `AIMessage` / `HumanMessage`. New "message shapes" are modeled by middleware that transforms one to another.
- Strengths: Reuses LangChain's serializer.
- Weaknesses: No first-class `correlation_id`; multi-step tool→tool chains rely on position in `messages[]`.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: None.

---

## 4. Custom Route Strategies

### ARFV1
- File(s): `crates/arf-core/src/route.rs:1-49`.
- Implementation: `Route::Strict(Vec<NodeId>)` for point-to-point, `Route::Discovery(Capability)` for capability-based fan-out (`route.rs:36-39`). `Capability::one(key, value)` single-pair constructor at `route.rs:25-29`. AND-match constraint: top-level string fields only; no array/nested-object matching (`route.rs:12`).
- Strengths: Two-variant enum is closed, exhaustive — adding a new strategy means a code change, but type-safety is total.
- Weaknesses: Closed enum — users cannot declare a third strategy without modifying `Route`.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:353-1025`, `libs/deepagents/libs/ARCHITECTURE.md`.
- Implementation: No route strategy — internal routing is fixed by the compiled graph.
- Strengths: N/A.
- Weaknesses: Cannot add new fan-out patterns; subagents are siblings under a parent, never discovered by capability.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Optional `Route::Custom(Box<dyn Router>)` for users who want predicate-based routing.

---

## 5. Custom ResponseProcessors

### ARFV1
- File(s): `crates/arf-core/src/processor.rs:21-30`.
- Implementation: Two methods: `handles(&self, msg_type: &str) -> bool`, `process(&self, msg: &Message) -> Result<Response, String>`. Engine resolves a processor via `HashMap<String, Arc<dyn ResponseProcessor>>` in `AgentConfig.engine.processors` (`config.rs:125`). Static dispatch — no dynamic dispatch for everyday dispatch.
- Strengths: Engines can fall through to the next processor on error (`processor.rs:27`). Compile-time guarantees: `Send + Sync`.
- Weaknesses: Each msg_type maps to exactly one processor in practice — there is no aggregator/fan-in.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/__init__.py:1-104`.
- Implementation: Equivalents are middlewares that read `AIMessage` / `ToolMessage` and rewrite state. Multiple middlewares stack in declared order.
- Strengths: Aggregation is natural — many middlewares can read each message.
- Weaknesses: No return-typed `Response`; middleware side-effects vary in shape.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Document a "first match wins" idiom in the trait doc.

---

## 6. Custom CheckpointRules

### ARFV1
- File(s): `crates/arf-core/src/checkpoint.rs:7-103`.
- Implementation: Five `Checkpoint` variants (`BeforeModelCall`, `AfterModelCall`, `BeforeToolExec`, `AfterToolExec`, `RoundEnd` — `checkpoint.rs:9-20`). Rule is a 4-tuple `{name, trigger, when, build}` with `Box<dyn for<'a> Fn(&'a State) -> ...>`. Helpers: `every_n_rounds`, `when_context_over`.
- Strengths: HRTB closure shape (`checkpoint.rs:30, 46`) lets `build` borrow any lifetime of `&State`. Single source-of-truth for declarative side-effects.
- Weaknesses: Closures are `!Clone` (trait objects can't clone) — users wrap in `Rc<CheckpointRule>` if needed (`checkpoint.rs:31`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/__init__.py:1-104`.
- Implementation: Hooks (`before_model`, `after_model`, `before_tool`, `after_tool`, `wrap_tool_call`, `wrap_model_call`). No round-end equivalent; round is implicit from `messages[]` boundary.
- Strengths: Hook names match LangChain docs.
- Weaknesses: No "build a side-effect message" pattern — all hooks return transformed state, never a new outbound message.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: None.

---

## 7. Custom MessageHandlers

### ARFV1
- File(s): `crates/arf-engine/src/dispatcher.rs:21-65`.
- Implementation: `MessageHandler: Send + Sync` with `msg_type() -> &'static str`. Handlers register via `engine.add_handler(Arc<dyn MessageHandler>)`. Outcomes: `HandlerOutcome::Handled` or `Deferred` (`dispatcher.rs:21-26`).
- Strengths: Handler registry is open (Hash-map keyed on `msg_type`). Default handlers provided in `crate::handlers`.
- Weaknesses: Deferred semantics are fall-through (next handler tries), so handler ordering matters implicitly.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/__init__.py:1-104`.
- Implementation: Equivalent — middleware stack processed in declared order; each one is "always fires."
- Strengths: Predictable ordering.
- Weaknesses: No opt-out per-message — must inspect msg type in middleware body.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Document handler-ordering precedence in dispatcher doc comment.

---

## 8. Custom LLM Providers

### ARFV1
- File(s): `crates/arf-model-adapter/src/provider.rs:22-69`.
- Implementation: `Provider: Send + Sync` with `name()`, `supported_models()`, `async chat()`, `chat_stream()` (default falls back to `chat()` — `provider.rs:60-67`). Adding a provider means one `impl Provider` struct ("nothing else in the codebase changes" — `provider.rs:18-20`).
- Strengths: Opt-in streaming override: providers without SSE just `chat()`.
- Weaknesses: No provider-version field — backwards-compat must be handled inside the impl.

### DeepAgents
- File(s): `libs/deepagents/deepagents/profiles/provider/provider_profiles.py:195`.
- Implementation: `register_provider_profile(name, config)` — registry accepts (provider, model_name, base_url, api_key) tuples. Discovery via `importlib.metadata` entry points (`_builtin_profiles.py:84-100`).
- Strengths: File-friendly YAML/JSON via `HarnessProfileConfig.from_dict` (`profiles/harness/harness_profiles.py:192`). 3rd-party providers ship as separate pip packages via entry-point groups.
- Weaknesses: `register_provider_profile` is "public beta" (`profiles/__init__.py:1-61`); behavior can change.

### Gap Analysis
- Parity: ✅ · Severity: 🟠 Important · Recommendation: Provide a `register_provider` crate macro / entry-point loader that reads a `providers.toml` at startup, mirroring DeepAgents' registry ergonomics.

---

## 9. Custom Pool Resources

### ARFV1
- File(s): `crates/arf-pool/src/lib.rs:1-50`, `crates/arf-pool/src/overflow.rs:1-20`.
- Implementation: `Resource` trait + `Pool<R: Resource>` with `Overflow::{Queue(n), Reject, Block(Duration)}` strategies (`overflow.rs:7-14`). Lifecycle: `Nil → Idle → Busy → Draining` (`lib.rs:13-19`).
- Strengths: Three overflow strategies cover queue/reject/timeout. `Lease<R>` auto-releases on drop.
- Weaknesses: No entry-point registration — pools are user-constructed.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/async_subagents.py:34-70`.
- Implementation: No equivalent pool — async subagents are external Agent Protocol servers.
- Strengths: N/A.
- Weaknesses: Cannot bound local concurrency for non-subagent async work.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: None.

---

## 10. Custom RuntimeModules (MCP)

### ARFV1
- File(s): `crates/arf-mcp/src/runtime.rs:15-50`.
- Implementation: `RuntimeModule: Send + Sync` with `capabilities() -> Value`, `async execute(&ToolCallSet, &HashMap<String, Arc<dyn Tool>>) -> ToolResultSet` (default delegates to `executor::execute`), `async run_single(...)`. `LocalRuntime` is the framework default (`runtime.rs:53`).
- Strengths: Default `execute()` lets users override only `run_single` for simple tools.
- Weaknesses: Custom runtimes must `impl RuntimeModule`; no plugin manifest path.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/sandbox*.py`, `libs/deepagents/deepagents/backends/protocol.py:356`.
- Implementation: `BackendProtocol` ABC (`backends/protocol.py:356`) — separate plug surface for filesystem/state, not runtime.
- Strengths: ABC is the canonical Python DI pattern.
- Weaknesses: Runtime-equivalent responsibilities are split across `BackendProtocol` (filesystem) and middleware (turn gating).

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: A `runtime_registry` similar to `Route` would let sandbox providers plug in via Cargo feature.

---

## 11. Custom Tools

### ARFV1
- File(s): `crates/arf-core/src/tool.rs:11-41`, `crates/arf-mcp/src/tool.rs` (via reference), `crates/arf-core/src/tool.rs:11-21`.
- Implementation: `ToolSpec` declares `name`, `description`, JSON-Schema `parameters`, `permission` (`tool.rs:11-21`); `permission ∈ {Allow, Ask, Deny}` gates Engine runtime behavior. `ToolNode` (in engine) consumes a `HashMap<String, Arc<dyn Tool>>`.
- Strengths: Three-tier permission (`Ask` = round-trip `permission_request` → `permission_response`) is a real safety mechanism.
- Weaknesses: `permission_request` blocking semantics tie into a single user-bus node.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/__init__.py:1-104`, `libs/deepagents/libs/THREATMODEL.md`.
- Implementation: Tools are plain callables or `BaseTool` subclasses; middleware wraps them for `before/after_tool` and `wrap_tool_call`. Threat model in `THREATMODEL.md` documents what extensions can and cannot do (e.g., cannot mutate past messages).
- Strengths: Threat model is explicit.
- Weaknesses: No three-tier permission — interrupts happen only at middleware boundary.

### Gap Analysis
- Parity: ✅ · Severity: 🟠 Important · Recommendation: Port a `THREATMODEL.md`-style security spec for ARFV1 tool permissions.

---

## 12. Custom Summarizers

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:1-50`.
- Implementation: `Summarizer` async-trait; triggered by `CheckpointRule::when_context_over` (`checkpoint.rs:77-92`). Caller provides the summary LLM call closure — Compactor stays decoupled from `ModelCall` routing.
- Strengths: Compactor = pure strategy; no hidden coupling to model adapters.
- Weaknesses: Closure-injection makes testing harder — need a mock closure.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/__init__.py`.
- Implementation: Equivalent is a `before_model` or `wrap_model_call` middleware that trims `state["messages"]`. No formal `Summarizer` trait.
- Strengths: Simple.
- Weaknesses: Token accounting is loose — middleware counts tokens differently from the LLM's own count.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: None.

---

## 13. Custom SessionStores

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:236-437`.
- Implementation: `SessionStore: Send + Sync` with `list`, `load`, `exists` (default impl via `load` at `lib.rs:249-251`), `save`, `delete`. Async on every method.
- Strengths: Default `exists()` lets simple impls inherit a working existence probe.
- Weaknesses: Trait does not require atomicity — concurrent save-then-load race is the impl's problem.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/protocol.py:356`, `libs/deepagents/deepagents/_state.py:11`.
- Implementation: `BackendProtocol` covers state persistence; `PrivateStateAttr` (`_state.py:11`) keeps middleware-private keys out of public schema.
- Strengths: 0.6.6 schema extension pattern is clean.
- Weaknesses: No atomic-batch save semantics for a full session.

### Gap Analysis
- Parity: ✅ · Severity: 🟠 Important · Recommendation: Adopt the `PrivateStateAttr` analog — `RedisSessionStore` keys should be partitionable into `public_*` and `private_*` so user-injected middleware cannot leak.

---

## 14. Custom OnMemberFailedHandlers

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:55-74`.
- Implementation: `OnMemberFailedHandler` returns `MemberFailedAction::{FailSession, Retry{delay_ms}, SwitchTo{alternative: NodeId}}` (`config.rs:55-57`). Default: `FailSession` (`config.rs:60`).
- Strengths: Three well-typed strategies; closure impls auto-satisfy via `impl<F> OnMemberFailedHandler for F where F: Fn(...) + Send + Sync` (`config.rs:67-74`).
- Weaknesses: Variant set is closed.

### DeepAgents
- File(s): N/A.
- Implementation: No formal handler; failure surfaces via middleware that inspects `state["messages"]` and reacts.
- Strengths: N/A.
- Weaknesses: No scoped retry/switch/fail decision types.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: None.

---

## 15. Entry-Point Plugin System

### DeepAgents
- File(s): `libs/deepagents/deepagents/profiles/_builtin_profiles.py:84-100`, `libs/deepagents/libs/ARCHITECTURE.md`.
- Implementation: `importlib.metadata.entry_points(group="deepagents.provider_profiles")` and `("deepagents.harness_profiles")` at startup. Plugins ship as separate pip packages and self-register via `pyproject.toml`.
- Strengths: Standard Python packaging; no in-process loader code in each provider.
- Weaknesses: Bootstrap must complete before any agent is constructed.

### ARFV1
- File(s): N/A.
- Implementation: No first-class entry-point loader. Providers are compiled into the binary or wired via user code.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important · Recommendation: Add a `arf_plugins::discover_providers()` that reads `[plugin.providers]` from a manifest file at startup; ship a sample `[lib].crate-type = "cdylib"` so plugins can be `.dylib`-loaded.

---

## 16. Plugin Registry

### DeepAgents
- File(s): `libs/deepagents/deepagents/profiles/__init__.py:1-61`, `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:980`, `libs/deepagents/deepagents/profiles/provider/provider_profiles.py:195`.
- Implementation: Public APIs `register_harness_profile(profile)` and `register_provider_profile(provider, model_name, base_url, api_key)`. Marked "public beta" (`__init__.py:1-61`).
- Strengths: Single registration entry; file-friendly via `from_dict`.
- Weaknesses: Beta status — API may shift.

### ARFV1
- File(s): `crates/arf-engine/src/registry.rs` (ResourceRegistry), `crates/arf-engine/src/dispatcher.rs:21-65` (HandlerRegistry).
- Implementation: Two registries: `ResourceRegistry` for model/tool/MCP nodes, `HandlerRegistry` for `msg_type → MessageHandler`.
- Strengths: Per-dimension registries keep wiring debuggable.
- Weaknesses: No unified plugin registry spanning Provider / Pool / Runtime / Tool.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important · Recommendation: Add `EngineBuilder::register_plugin(PluginMetadata)` that wires Provider + Pool + Runtime declarations into the right registries at once.

---

## 17. Cross-Thread Bootstrap Safety

### DeepAgents
- File(s): `libs/deepagents/deepagents/profiles/_builtin_profiles.py` (references `threading.Condition`).
- Implementation: `threading.Condition` guards bootstrap-state. The first call (per thread-id) registers defaults; concurrent threads see the same registry. Waiters are released when the registry is fully populated.
- Strengths: Race-free across `threading.Thread` boundaries; standard CPython pattern.
- Weaknesses: Async-loop threads (e.g., `asyncio.run`) need careful coordination with `Condition`.

### ARFV1
- File(s): All trait registries constructed under `tokio::sync::Mutex` (`crates/arf-engine/src/registry.rs`, implicit from `Arc<...>` use).
- Implementation: Async-aware — `tokio::sync::Mutex` and `OnceCell` patterns. No explicit "bootstrap first, then run" gate; the `EngineBuilder` chain is the implicit gate.
- Strengths: Native async; no thread-id bookkeeping.
- Weaknesses: No documented guarantee — concurrent `add_handler` from two tasks is allowed but not stress-tested.

### Gap Analysis
- Parity: ✅ · Severity: 🟡 Useful · Recommendation: Document the bootstrap contract: "all `add_*` calls must complete before `engine.run()`."

---

## 18. File-Friendly Profile Config

### DeepAgents
- File(s): `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:192`.
- Implementation: `HarnessProfileConfig.from_dict(d)` / `from_yaml(path)` / `from_json(path)` — schema is bidirectional: dict ↔ dataclass.
- Strengths: YAML/JSON ergonomics for ops teams.
- Weaknesses: Schema drift between dataclass and dict is unchecked at compile time.

### ARFV1
- File(s): `crates/arf-agent/src/resource.rs` (ResourceSpec — file-friendly via serde derives).
- Implementation: `ResourceSpec` derives `Serialize/Deserialize`; can be loaded from TOML/JSON. Lacks equivalent `from_dict` per-agent.
- Strengths: Compile-time field names via `serde`.
- Weaknesses: No `AgentConfig::from_yaml` because `AgentConfig` cannot derive `Deserialize` (CheckpointRule holds `Box<dyn Fn>` — `config.rs:79-83`).

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful · Recommendation: Add a thin YAML/JSON loader that builds `AgentConfig` then wires closure-based fields from a separate `overrides` section.

---

## 19. `state_schema` Extension (0.6.6)

### DeepAgents
- File(s): `libs/deepagents/deepagents/_state.py:11`, `libs/deepagents/libs/ARCHITECTURE.md`.
- Implementation: `state_schema` declared as a TypedDict; middleware declare `PrivateStateAttr` slots that get merged into runtime state but hidden from public schema (`_state.py:11`).
- Strengths: User-facing schema stays clean.
- Weaknesses: Migration cost when schema bumps.

### ARFV1
- File(s): `crates/arf-core/src/state.rs` (State struct).
- Implementation: State is a concrete struct; engines merge checkpoint-published messages into it. No formal extension mark.
- Strengths: Static shape.
- Weaknesses: No first-class "private state vs public state" distinction.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Add a `PrivateStateField` marker attribute for fields hidden from external inspectors.

---

## 20. `PrivateStateAttr` Marker

### DeepAgents
- File(s): `libs/deepagents/deepagents/_state.py:11`.
- Implementation: Marker on TypedDict keys; introspection code skips marked keys when building public state projections.
- Strengths: Permission scoping is structural — declarative.
- Weaknesses: Only TypedDict compatible.

### ARFV1
- File(s): N/A.
- Implementation: All State fields are public.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: A `#[arf(private)]` proc-macro attribute on `State` fields would replicate this.

---

## 21. Plugin Entry-Point Groups

### DeepAgents
- File(s): `libs/deepagents/deepagents/profiles/_builtin_profiles.py:84-100`.
- Implementation: Two groups: `deepagents.provider_profiles` and `deepagents.harness_profiles`. Third-party pip packages register into these.
- Strengths: Convention is published; ecosystem can grow.
- Weaknesses: Group naming rigidity — once shipped, hard to rename.

### ARFV1
- File(s): N/A.
- Implementation: No formal group convention.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful · Recommendation: Publish a convention for `[package.metadata.arf.providers]` in `pyproject.toml` (Python bindings) and `[package.metadata.arf]` in `Cargo.toml`.

---

## 22. Capability-Based Discovery (`Route::Discovery`)

### ARFV1
- File(s): `crates/arf-core/src/route.rs:10-49`.
- Implementation: `Capability::new(reqs: Vec<(String, String)>)` — AND-matched top-level string fields (`route.rs:13`). `Route::Discovery(Capability)` fans out to Nodes whose advertised capabilities satisfy the requirements. Single-pair shortcut at `route.rs:25-29`.
- Strengths: Closed enum + AND-only matching keeps semantics crisp.
- Weaknesses: Nested keys and array membership are explicitly out of scope (`route.rs:13`).

### DeepAgents
- File(s): N/A.
- Implementation: No capability discovery; subagent routing is fixed by the parent's tool wrapper.

### Gap Analysis
- Parity: ✅ · Severity: 🟢 N/A · Recommendation: Optional `Route::Discovery` extension for OR-membership and nested-key matching.

---

## Summary Table

| Capability | ARFV1 | DeepAgents | Parity | Severity |
|---|---|---|---|---|
| Custom `Node` types | ✅ | ⚠️ (subagent-factory only) | ✅ | 🟢 |
| Custom `ActionMessage` types | ✅ | ✅ | ✅ | 🟢 |
| Custom `Route` strategies | ✅ (closed) | ❌ | ✅ | 🟢 |
| Custom `ResponseProcessor` | ✅ | ✅ (via middleware) | ✅ | 🟢 |
| Custom `CheckpointRule` | ✅ | ✅ | ✅ | 🟢 |
| Custom `MessageHandler` | ✅ | ✅ | ✅ | 🟢 |
| Custom `Provider` | ✅ | ✅ (registry) | ✅ | 🟠 manifest |
| Custom `Resource` (Pool) | ✅ | ❌ | ✅ | 🟢 |
| Custom `RuntimeModule` | ✅ | ⚠️ (via Backend + middleware split) | ✅ | 🟡 runtime_registry |
| Custom `Tool` + permission | ✅ (3-tier) | ⚠️ (no permission tiers) | ✅ | 🟠 threat model |
| Custom `Summarizer` | ✅ | ⚠️ (ad-hoc middleware) | ✅ | 🟢 |
| Custom `SessionStore` | ✅ | ✅ (BackendProtocol) | ✅ | 🟠 PrivateStateAttr |
| Custom `OnMemberFailedHandler` | ✅ (3 strategies) | ❌ | ✅ | 🟢 |
| Entry-point plugin system | ❌ | ✅ (`importlib.metadata`) | ❌ | 🟠 |
| Plugin registry | ⚠️ (per-dim only) | ✅ (`register_*_profile`) | ⚠️ | 🟠 |
| Cross-thread bootstrap | ✅ (implicit via Builder) | ✅ (`threading.Condition`) | ✅ | 🟡 |
| File-friendly profile config | ⚠️ (ResourceSpec only) | ✅ (`from_dict`/`from_yaml`) | ⚠️ | 🟡 |
| `state_schema` extension | ❌ | ✅ (TypedDict + 0.6.6) | ❌ | 🟡 |
| `PrivateStateAttr` marker | ❌ | ✅ | ❌ | 🟡 |
| Entry-point groups | ❌ | ✅ (2 groups) | ❌ | 🟡 |
| Capability discovery | ✅ | ❌ | ✅ | 🟢 |

## Top Recommendations (priority-ordered)

1. **Entry-point plugin loader** (severity 🟠) — add `arf_plugins::discover()` reading `[plugin.*]` sections, mirroring `importlib.metadata`-style discovery.
2. **Unified `PluginRegistry`** (severity 🟠) — wire Provider / Pool / Runtime declarations through one `EngineBuilder::register_plugin()`.
3. **`HarnessProfileConfig.from_dict` analog** (severity 🟡) — YAML/JSON loader for `AgentConfig` with closure-fields separated into `overrides`.
4. **`PrivateStateAttr` analog** (severity 🟡) — `#[arf(private)]` proc-macro field marker.
5. **`THREATMODEL.md`** (severity 🟠) — explicit threat model for tool permissions and middleware extensions.
