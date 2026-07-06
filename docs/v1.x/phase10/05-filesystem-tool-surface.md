# Filesystem / Tool Surface — ARFV1 vs DeepAgents

> Atomic comparison of how each framework exposes filesystem-style tools,
> routes tool calls, enforces permissions, and composes execution graphs.

## 1. Backend protocol abstraction

### ARFV1
- Files: `crates/arf-mcp/src/discovery.rs:31-65`, `crates/arf-mcp/src/runtime.rs:19-50`
- Implementation: ARFV1 does NOT define a `BackendProtocol` per se. The closest analog is the `DiscoveryBackend` trait (`list_tools` / `tool_map` / `resolve_tool`) plus the `RuntimeModule` trait (`capabilities` / `execute` / `run_single`). Together they form two orthogonal axes — discovery (where tool manifests come from) and runtime (how calls are executed). Both are bound once at `McpNode` construction (`crates/arf-mcp/src/node.rs:74-87`, `with_discovery`).
- Strengths: Cleanly separates *what tools exist* from *how they run*, enabling e.g. HTTP discovery + local execution.
- Weaknesses: No "backend factory" type alias like DeepAgents' `BackendFactory`; users must construct nodes manually and pass them to the Bus.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/protocol.py:356`, `libs/deepagents/deepagents/backends/protocol.py:882`, `libs/deepagents/deepagents/backends/protocol.py:982-983`
- Implementation: `BackendProtocol` is a single ABC with all 8 filesystem operations (read/write/edit/ls/glob/grep/execute/upload/download). `SandboxBackendProtocol` extends it for sandbox semantics. `BackendFactory = Callable[[Runtime], BackendProtocol]` is a TypeAlias and `_resolve_backend` does runtime dispatch by `isinstance` checks (state / sandbox / composite / etc.).
- Strengths: One canonical interface, factory pattern, type-alias ergonomics.
- Weaknesses: ABC inheritance (not Protocol), so backends must import the base class — a tighter coupling than ARFV1's two-trait split.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add a `BackendFactory = Arc<dyn Fn(&Runtime) -> Arc<dyn BackendProtocol>>` alias in arf-mcp; allow `McpNode::with_factory(...)` so apps can defer backend choice to first tool call (mirroring DeepAgents' late binding).

## 2. Backend implementations (count & variety)

### ARFV1
- Files: `crates/arf-mcp/src/node.rs:16-22`
- Implementation: One `McpNode` struct. Local vs remote differs only in `DiscoveryBackend` (`FsDiscovery` vs `HttpDiscovery`) and `RuntimeModule` (`LocalRuntime` vs `RemoteRuntime`). Plus `LocalRuntime` (subprocess), `RemoteRuntime` (HTTP proxy), and a user-injectable `with_discovery`/`local_with_runtime` slot.
- Strengths: Trivial type count; backend is *swap* of two fields, not subclass hierarchy.
- Weaknesses: No built-in thread-scoped state, no LangSmith-Hub adapter, no LangGraph-Store adapter, no per-thread composite router.

### DeepAgents
- Files: `state.py:408`, `filesystem.py:1209`, `local_shell.py:387`, `sandbox.py:1345`, `langsmith.py:279`, `composite.py:820`, `store.py:882`, `context_hub.py:374`
- Implementation: 8 first-class backends — `StateBackend` (thread-scoped), `FilesystemBackend` (disk), `LocalShellBackend` (host shell), `BaseSandbox` (sandbox abstract), `LangSmithSandbox`, `CompositeBackend` (path-prefix router), `StoreBackend` (LangGraph BaseStore), `ContextHubBackend` (LangSmith Hub).
- Strengths: Each backend = one concrete use case; composable via `CompositeBackend`.
- Weaknesses: Some duplication of `ls`/`read` semantics across backends; backend-agnostic ops like `delete` are feature-gated via `_supports_delete` helper.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Add (a) `StateBackend`-equivalent (in-memory, session-scoped via Bus context), (b) `CompositeBackend`-equivalent (path-prefix → node routing in `McpNode`), (c) `ContextHubBackend`-equivalent (remote skill/tool hub as just another `DiscoveryBackend` impl).

## 3. Filesystem operations (ls / read / write / edit / glob / grep / delete)

### ARFV1
- Files: `crates/arf-mcp/src/tool.rs:11-41`, `crates/arf-mcp/src/discovery.rs:69-132`
- Implementation: `Tool` trait only mandates `execute(params) → Result<Value, ToolError>`. ARFV1 ships no built-in ls/read/write/edit/glob/grep/delete — they must be authored as `ScriptTool` instances via `tool.toml` and live under `{root}/tools/<name>/` (`discovery.rs:96-127`).
- Strengths: Total flexibility — any tool is just a script + manifest.
- Weaknesses: No canonical FS tool set; apps must hand-roll 7+ tools to be feature-equivalent with DeepAgents' middleware.

### DeepAgents
- Files: `libs/deepagents/deepagents/middleware/filesystem.py:3102`
- Implementation: `FilesystemMiddleware` auto-supplies `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, `delete` to the agent. Each delegates to whichever `BackendProtocol` is in scope.
- Strengths: Zero-config; out-of-the-box FS capability.
- Weaknesses: Tightly coupled to `BackendProtocol` ABC — adding a non-protocol backend is painful.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🔴 Critical
- Recommendation: Ship an `arf-fs-tools` crate (or in-tree `crates/arf-tools/src/fs/`) exposing 7 canonical tools as `ScriptTool`/`NativeTool` impls, plus a `default_fs_tool_set()` helper that registers all 7 with the engine. Borrow DeepAgents' middleware auto-injection pattern.

## 4. Path-level permissions

### ARFV1
- Files: `crates/arf-core/src/tool.rs:11-27`, `crates/arf-agent/src/tool.rs:9-46`
- Implementation: `ToolPermission::{Allow, Ask, Deny}` is a *whole-tool* gate. `AgentToolSpec.parameter_filter` (`tool.rs:35`) is a free-form `serde_json::Value` (e.g. `{"paths": ["/workspace/*"]}`) that downstream code is expected to consult.
- Strengths: Coarse Allow/Ask/Deny matches the engine's gating model (`engine.rs:972-1024`).
- Weaknesses: `parameter_filter` is *untyped* — no schema, no engine enforcement, no per-path allow/deny. Path-level rules are entirely app responsibility.

### DeepAgents
- Files: `libs/deepagents/deepagents/middleware/filesystem.py:252-285`, `libs/deepagents/deepagents/middleware/filesystem.py:288-298`
- Implementation: `FilesystemPermission` dataclass holds a `matcher` (e.g. `["write", "/workspace/*"]`) plus a `mode` ∈ {`"allow"`, `"deny"`}. The middleware evaluates these before dispatching to the backend.
- Strengths: Path-glob matching, declarative allow/deny list per operation.
- Weaknesses: Matcher semantics are bespoke (not the standard fnmatch or glob crate's).

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🔴 Critical
- Recommendation: Define a typed `PathPermission { tool: String, glob: String, mode: Allow|Deny }` in `arf-core::tool`, and enforce it inside `do_tool_turn` *after* the coarse Allow/Ask/Deny gate but *before* the bus message. Use the `glob` crate for matching.

## 5. Visibility vs enforcement ("model may still see a tool whose call can later be denied or interrupted")

### ARFV1
- Files: `crates/arf-engine/src/engine.rs:972-1024`
- Implementation: The tool spec is always advertised to the LLM; `Deny` results in a runtime `tool_result` error injected into the message stream. `Ask` blocks via `permission_request`/`permission_response` round-trip.
- Strengths: Model can see all tools and learn from denied attempts.
- Weaknesses: No way to *hide* a tool from the LLM's view based on permission (Deny still exposes the function-calling surface).

### DeepAgents
- Files: `libs/deepagents/deepagents/middleware/_fs_interrupt.py:38`, `libs/deepagents/deepagents/middleware/_fs_interrupt.py:183`
- Implementation: `mode="interrupt"` builds an `InterruptOn` config; the model can still emit the tool call, but the runtime raises an interrupt and human must approve. Visibility is unchanged; behavior is gated at call time.
- Strengths: Single semantics — interrupt is structurally similar to deny-with-recovery.
- Weaknesses: No "hide tool" mode — once a tool is in the spec list, the model can attempt it.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Document the parity; consider adding an `opt-in` visibility flag so apps that want strict tool hiding can drop a denied tool from `ModelCall.tools` before LLM invocation.

## 6. Composite backend routing (path prefix → backend)

### ARFV1
- Files: `crates/arf-mcp/src/node.rs:74-87`
- Implementation: `McpNode` does not have built-in path-prefix routing. Apps needing it must construct multiple `McpNode` instances and rely on the engine's `tc.target > owner_of_tool > broadcast` precedence.
- Strengths: Bus-level routing decoupled from per-path routing.
- Weaknesses: No per-path policy; a tool name like `read_file` can't be routed to a different backend based on the *argument* path.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/composite.py:820`
- Implementation: `CompositeBackend` holds a list of `(prefix, BackendProtocol)` pairs. Each op (e.g. `read`) matches the arg path against the longest prefix and delegates.
- Strengths: Path-based routing independent of tool name.
- Weaknesses: Linear scan per call (acceptable for <100 prefixes).

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Add a `PathRouter` middleware in `arf-mcp` that maps argument-path-prefixes to backend IDs; engine consults it before `owner_of_tool`.

## 7. StateBackend (thread-scoped ephemeral)

### ARFV1
- Files: *no equivalent*
- Implementation: ARFV1 has no thread-scoped in-memory backend. Closest analog is `McpResource` (`pool_resource.rs`) which is *process-global*.
- Weaknesses: Agents that want scratch space must either allocate on the real filesystem or roll their own map.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/state.py:408`
- Implementation: `StateBackend` keeps a `dict[str, dict]` in a LangGraph config key (`config["configurable"]["__pregel_checkpoint_runner"]`-style threading). Each thread gets its own sandbox; cross-thread writes are invisible.
- Strengths: Built-in ephemeral scratch; integrates with LangGraph checkpointing.
- Weaknesses: Tied to LangGraph config keys — coupling to LangGraph runtime.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Add `MemoryBackend` in `arf-mcp` keyed by `session_id` from `Message` context; provide it as a `DiscoveryBackend` that returns the 7 FS tools pointing at an in-memory map.

## 8. FilesystemBackend (disk-backed with virtual_mode)

### ARFV1
- Files: *no first-class equivalent; `FsDiscovery` is discovery, not execution*
- Implementation: `FsDiscovery::scan` reads a directory tree at startup, but it doesn't actually perform file ops at runtime — that's the job of script tools.
- Weaknesses: No canonical disk-backed tool backend; security boundaries are entirely up to the script.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/filesystem.py:1209`
- Implementation: `FilesystemBackend` operates on a configurable `root_dir`. With `virtual_mode=True`, all paths in tool args are rebased onto `root_dir` and `..` is rejected, preventing escape.
- Strengths: `virtual_mode` is a single bool that sandboxes the entire backend.
- Weaknesses: Symlink handling is platform-specific.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Ship `DiskBackend` in `arf-mcp` with a `sandbox_root: Option<PathBuf>` option; implement `canonicalize() == root || starts_with(root+"/")` check before each op.

## 9. SandboxBackend (execute + upload_files only)

### ARFV1
- Files: `crates/arf-mcp/src/runtime.rs:19-50`
- Implementation: ARFV1's `RuntimeModule` is the *execution* split, not the *filesystem* split. No `SandboxBackend` analog — sandboxes would be a user-supplied `RuntimeModule` impl.
- Strengths: Runtime abstraction cleanly captures "spawn elsewhere".
- Weaknesses: No built-in sandbox backend; users must write their own `RuntimeModule` (e.g. `SandboxRuntime` referenced in the comment at `runtime.rs:18` but not yet shipped).

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/sandbox.py:1345`
- Implementation: `BaseSandbox` exposes only `execute(command)` and `upload_files(files)`. All FS ops (`read`/`write`/etc.) are funneled through `execute` (e.g. `cat`, `sed`). This is the "no real FS API" sandbox pattern.
- Strengths: Works with any sandbox that supports `exec`; minimal API surface.
- Weaknesses: No streaming, no structured FS metadata, slow for large files.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Ship a `SandboxRuntime` example in `arf-mcp::runtime` that wraps a subprocess (e.g. `firejail`, `bubblewrap`, or a Docker sidecar via HTTP) and exposes `capabilities() → {"runtime": "sandbox", "backend": "firejail"}`.

## 10. StoreBackend (cross-thread persistent via LangGraph BaseStore)

### ARFV1
- Files: `crates/arf-mcp/src/pool_resource.rs`
- Implementation: `McpResource` is the closest analog — a process-wide, Bus-addressable resource. It is not a *backend* per se, and there is no `NamespaceFactory` or per-namespace routing.
- Strengths: Bus-native; agents can address it by node ID.
- Weaknesses: No path → namespace mapping; no built-in cross-thread persistence semantics.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/store.py:882`
- Implementation: `StoreBackend` adapts a LangGraph `BaseStore` (key-value) into the `BackendProtocol` shape, with `NamespaceFactory` to derive per-thread namespaces from runtime config.
- Strengths: Decouples persistence from filesystem; works with any KV store.
- Weaknesses: LangGraph-specific.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Generalize `McpResource` to expose a `BackendProtocol`-shaped facade; add a `NamespaceFactory` trait that takes a session/round ID and returns a backend instance.

## 11. Execute tool (shell access)

### ARFV1
- Files: `crates/arf-mcp/src/runtime.rs:55-75` (LocalRuntime)
- Implementation: Any tool that calls `std::process::Command` is "execute". ARFV1 ships no canonical `execute` tool — apps must add it. `LocalRuntime` itself runs the tool *executor* (the script), not arbitrary shell commands.
- Strengths: Maximum flexibility; the user controls what `execute` means.
- Weaknesses: Inconsistency — every app ships its own `execute` with different permission semantics.

### DeepAgents
- Files: `libs/deepagents/deepagents/middleware/filesystem.py:3102` (FilesystemMiddleware)
- Implementation: `execute` is supplied by the middleware and delegates to the backend's `execute(command)`. Backends that don't support shell (e.g. `StoreBackend`) raise `NotImplementedError`.
- Strengths: One canonical `execute` tool; backend capability check at registration time.
- Weaknesses: Capability mismatch is runtime, not registration-time — model may try `execute` against a non-shell backend.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Ship a canonical `execute` tool gated by `ToolPermission::Ask` by default; in the engine, check `runtime.capabilities()["shell"] == true` before including the tool in `ModelCall.tools`.

## 12. Tool DAG (Kahn topological sort)

### ARFV1
- Files: `crates/arf-mcp/src/executor.rs:15-114`, `crates/arf-mcp/src/executor.rs:298-353`
- Implementation: `executor::execute` validates `blocked_by`/`blocking` bidirectional consistency (`executor.rs:205-222`), runs cycle detection via DFS (`executor.rs:239-296`), then Kahn-sorts into layers (`executor.rs:304-353`). Each layer's calls run via `tokio::spawn` + `join_all` (`executor.rs:55-71`).
- Strengths: True DAG with cycle detection, cascade cancel, panic safety (`catch_unwind`), and per-call timeout.
- Weaknesses: DAG lives in arf-mcp only. The engine (`arf-engine`) processes tool calls *one at a time* per turn (`engine.rs:959-1126`) — it does not pass a `ToolCallSet` with `blocked_by` to MCP. So DAG is dormant unless the model emits multiple parallel tool calls with explicit dependencies, which most model APIs don't support.

### DeepAgents
- Files: *not implemented in DeepAgents*
- Implementation: DeepAgents' tools run one-at-a-time through middleware; there's no `blocked_by` / `blocking` concept.
- Strengths: Simpler.
- Weaknesses: Cannot express data dependencies (e.g. "use the output of search to drive the next read").

### Gap Analysis
- Parity: ✅ (DAG exists in ARFV1, absent in DeepAgents)
- Severity: 🟠 Important
- Recommendation: ARFV1 should expose the DAG to the model: (a) let the LLM emit a `ToolCallSet` with dependencies, (b) have engine pass the full set to MCP, (c) add a system prompt hint describing the dependency syntax. Currently the executor is built but unused by the engine's main loop.

## 13. Tool routing precedence (tc.target > owner_of_tool > broadcast)

### ARFV1
- Files: `crates/arf-engine/src/engine.rs:1032-1053`
- Implementation: For each tool call, target = `tc.target` (model-supplied, explicit) ?? `registry.owner_of_tool(name)` (BusGraph) ?? broadcast (legacy). If targeted, send unicast `tool_exec`; otherwise broadcast and let the owner respond (`crates/arf-mcp/src/node.rs:204-206` filters non-owners into `None`).
- Strengths: Three-tier precedence is explicit and traceable; ownership is a first-class concept.
- Weaknesses: `registry.owner_of_tool` is not shown here; relies on `BusGraph` registry to track tool → node mapping.

### DeepAgents
- Files: *not applicable — single backend per agent*
- Implementation: DeepAgents doesn't have a "routing" step; each tool is bound to the single backend in scope.
- Strengths: Trivially simple.
- Weaknesses: Multi-agent / multi-MCP topologies require app code.

### Gap Analysis
- Parity: ✅ (ARFV1 strictly ahead)
- Severity: 🟡 Useful
- Recommendation: None — ARFV1's three-tier routing is strictly more expressive. Document it prominently in the engine guide.

## 14. ToolPermission enum (Allow / Ask / Deny)

### ARFV1
- Files: `crates/arf-core/src/tool.rs:11-27`, `crates/arf-engine/src/engine.rs:972-1024`
- Implementation: Three-variant enum serialized lowercase. Engine dispatches: `Deny` → return error result; `Ask` → send `permission_request`, await `permission_response`; `Allow` → proceed.
- Strengths: Cleanly typed, default = `Allow` for backward compat, request/response is async over the Bus.
- Weaknesses: No per-action Ask granularity (one tool = one permission level for all its calls).

### DeepAgents
- Files: `libs/deepagents/deepagents/middleware/filesystem.py:252-285`
- Implementation: Per-path dataclass with `mode ∈ {allow, deny}` + interrupt-on-confirm.
- Strengths: Path-level granularity.
- Weaknesses: No clean "ask once, remember" semantics; no engine-level gating — interrupts happen in middleware.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Keep ARFV1's three-variant engine-level enum. Add an *orthogonal* per-path `PathPermission` layer (see §4) so app authors get both coarse gating and fine-grained path rules.

## 15. parameter_filter (strip sensitive params)

### ARFV1
- Files: `crates/arf-agent/src/tool.rs:35`, `crates/arf-agent/src/tool.rs:118`
- Implementation: `AgentToolSpec.parameter_filter: Option<serde_json::Value>` — a free-form constraint (e.g. `{"paths": ["/workspace/*"]}`). Documented intent: "restrict file tool access" (`tool.rs:33-34`). There is no engine-level enforcement; downstream code (apps, MCP node filters) is responsible for consulting it.
- Strengths: Open-ended schema.
- Weaknesses: Untyped; no enforcement; no value-stripping semantics (it constrains, doesn't redact).

### DeepAgents
- Files: *no exact equivalent*
- Implementation: DeepAgents has path matchers on `FilesystemPermission` (§4) but no general "strip sensitive params" hook. Closest analog is middleware-level argument rewriting, which apps must implement.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Either (a) define a strict JSON-Schema for `parameter_filter` and enforce it in the engine, or (b) rename it to `parameter_constraint` and add a separate `parameter_redactor` for true redaction semantics. Document which existing tools honor it (currently: none — the field is purely advisory).

## 16. ToolExecutor with cascade cancel

### ARFV1
- Files: `crates/arf-mcp/src/executor.rs:355-396`
- Implementation: When any tool in a layer fails, `cascade_cancel` BFS-walks the `blocking` chain and marks all downstream calls as `cancelled` with a `"cancelled: upstream dependency '{id}' failed"` error. Cancelled calls also invoke `tool.cancel()` if a timeout was hit (`executor.rs:184-194`).
- Strengths: Fail-fast semantics; downstream resources are released.
- Weaknesses: BFS cancellation is synchronous in the current call, not async-cancel-aware (no AbortController analog for arbitrary tools).

### DeepAgents
- Files: *not applicable — no DAG*
- Implementation: N/A.

### Gap Analysis
- Parity: ✅ (ARFV1 strictly ahead)
- Severity: 🟡 Useful
- Recommendation: Add a `Tool::abort_handle()` method that returns a `tokio_util::sync::CancellationToken`; executor passes it down so tools can register background work for true abort (not just `cancel()` polling).

## 17. Layer-by-layer concurrent execution (tokio::spawn per layer)

### ARFV1
- Files: `crates/arf-mcp/src/executor.rs:54-92`
- Implementation: For each Kahn layer, one `tokio::spawn` per call; `join_all(futures).await` collects. Next layer starts only after previous layer fully resolves.
- Strengths: Predictable concurrency, true parallelism within a layer, sequential dependencies honored.
- Weaknesses: Head-of-line blocking — one slow tool in a layer stalls subsequent layers.

### DeepAgents
- Files: *not applicable*
- Implementation: N/A.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: None. Add metrics: log per-layer wait time and per-tool execution time so apps can spot stragglers.

## 18. Ripgrep integration (Python fallback)

### ARFV1
- Files: *no built-in grep tool*
- Implementation: Apps that want `grep` must implement it as a `ScriptTool` calling `rg` or `grep` from a shell script.
- Strengths: Flexible.
- Weaknesses: Inconsistency across apps; no shared default.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/filesystem.py` (grep impl within `FilesystemBackend`)
- Implementation: Tries `ripgrep` (`rg --json`) first; falls back to a pure-Python regex walker. Output is normalized to `GrepResult` dataclass (`protocol.py:71-353`).
- Strengths: Best-of-both — fast when available, portable fallback.
- Weaknesses: Pure-Python fallback is slow on large repos.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: When shipping the canonical `grep` tool (§3), use the same `rg` → Python fallback pattern. Add a `RipgrepBackend` probe at MCP node startup that reports `capabilities()["ripgrep"] = true|false`.

## 19. virtual_mode path sandboxing (prevent `..` escape)

### ARFV1
- Files: *no built-in sandboxing*
- Implementation: `LocalRuntime` runs scripts in whatever cwd the OS provides; no `..` rebase, no canonicalization check. Path safety is entirely up to script authors.
- Strengths: Maximum compatibility.
- Weaknesses: Easy to escape the working dir.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/filesystem.py:1209`
- Implementation: With `virtual_mode=True`, args are rebased onto `root_dir` and `..` segments are rejected before the OS call. Combined with `os.path.realpath` for symlink resolution, the path cannot escape the root.
- Strengths: Single-bool sandbox; defense in depth.
- Weaknesses: Doesn't catch TOCTOU races between check and use.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: In the proposed `DiskBackend` (§8), set `virtual_mode=true` by default; emit a warning when an app sets it to `false`.

## 20. Backend capability introspection (`_supports_delete`)

### ARFV1
- Files: *no equivalent*
- Implementation: ARFV1 has no capability introspection. `McpNode` exposes `capabilities()` (`node.rs:124`) for the *runtime* dimension (local/remote/sandbox), but not for per-operation support.
- Weaknesses: Engine cannot pre-filter tools whose backend doesn't support `delete` etc. The LLM may try and get a runtime error.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/protocol.py:71-353` (result dataclasses)
- Implementation: A cached helper `_supports_delete` (and similar for `execute`, `upload_files`, `download_files`) checks `hasattr(backend, "delete")` etc. and caches the result. Middleware uses this to omit unsupported tools from the model spec.
- Strengths: Auto-pruning of unsupported tools.
- Weaknesses: `hasattr` is a fragile signal (some backends may inherit the method but raise `NotImplementedError`).

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Add an explicit `supports: HashSet<FsOp>` field to the `ToolSpec` returned by `DiscoveryBackend`; engine prunes the model-call tools list accordingly.

## 21. LangSmith Hub as backend (shared persistent store)

### ARFV1
- Files: *no equivalent — closest is `McpResource`*
- Implementation: ARFV1 has no Hub integration; remote skills/tools come via `HttpDiscovery` over arbitrary HTTP, not a typed Hub protocol.
- Strengths: None over DeepAgents here.
- Weaknesses: Missing shared store/registry for reusable prompts and tools.

### DeepAgents
- Files: `libs/deepagents/deepagents/backends/context_hub.py:374`
- Implementation: `ContextHubBackend` proxies a LangSmith Hub instance — agents share a persistent, versioned prompt/tool catalog across sessions.
- Strengths: Cross-session reuse, version pinning.
- Weaknesses: Vendor-specific (LangSmith).

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Define a `HubBackend` trait in arf-mcp with two methods (`fetch(name, version) → ToolInfo` and `upload(name, body) → version`); ship a default `LocalFsHub` impl backed by a git repo, leaving remote Hub providers (LangSmith, ARF Cloud) as out-of-tree impls.

---

## Summary scorecard

| # | Capability | ARFV1 | DeepAgents | Gap |
|---|---|---|---|---|
| 1 | Backend protocol | Two-trait split | Single ABC | ⚠️ |
| 2 | Backend impls | 1 type, 2-3 runtimes | 8 first-class | ❌ |
| 3 | FS operations | None built-in | 7 auto-injected | ❌ |
| 4 | Path-level perms | Untyped filter | Typed dataclass | ❌ |
| 5 | Visibility vs enforcement | Engine-level | Middleware-level | ✅ |
| 6 | Composite routing | Bus-level | Path-level | ❌ |
| 7 | StateBackend | None | Yes | ❌ |
| 8 | FilesystemBackend | None | Yes + virtual_mode | ❌ |
| 9 | SandboxBackend | Runtime hook | Yes | ⚠️ |
| 10 | StoreBackend | McpResource | Yes + NamespaceFactory | ⚠️ |
| 11 | Execute tool | App-by-app | Canonical | ❌ |
| 12 | Tool DAG | Implemented, dormant | None | ✅ |
| 13 | Routing precedence | 3-tier | N/A | ✅ |
| 14 | ToolPermission | Allow/Ask/Deny | Allow/Deny/Interrupt | ⚠️ |
| 15 | parameter_filter | Untyped | N/A (path matchers) | ⚠️ |
| 16 | Cascade cancel | Yes | N/A | ✅ |
| 17 | Layer concurrency | tokio::spawn | N/A | ✅ |
| 18 | Ripgrep | App script | rg + Python fallback | ❌ |
| 19 | virtual_mode | None | Yes | ❌ |
| 20 | Capability introspection | Runtime-only | `_supports_*` cached | ❌ |
| 21 | LangSmith Hub | None | Yes | ❌ |

**Net assessment:** ARFV1 wins on **routing, DAG execution, and concurrent layer execution** (engine-level concerns that DeepAgents doesn't model). DeepAgents wins on **backend variety, file operation coverage, path-level permissions, and capability introspection** (middleware-level concerns that ARFV1 has delegated to "apps must implement"). The largest near-term gap is **§3 + §8 + §9 + §11**: ARFV1 should ship a canonical `arf-fs-tools` crate that provides the 7 FS tools backed by a `BackendProtocol`-shaped trait, with `virtual_mode` on by default and `execute` gated by `Ask`.
