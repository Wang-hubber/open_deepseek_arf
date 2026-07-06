# Sandbox / Shell Execution — DeepAgents vs ARFV1

> Phase 10 / Doc 13. Atomic-level comparison of how each framework defines,
> dispatches, gates, and secures code execution environments.

---

## 1. Sandbox backend protocol

### ARFV1
- File(s): `crates/arf-mcp/src/runtime.rs:19-50`
- Implementation: `RuntimeModule` trait exposes `capabilities() -> Value`, `execute(call_set, tools)`, `run_single(call_id, tool, params)`. Default `execute()` delegates to the DAG executor at `executor.rs:15`. Three implementations: `LocalRuntime`, `RemoteRuntime`, plus a forward-compatibility slot for `SandboxRuntime` (commented at `runtime.rs:18`).
- Strengths: Decoupled from discovery (`DiscoveryBackend`) so sandbox can be swapped without changing tool registry; default `execute()` removes 80 lines of boilerplate per backend.
- Weaknesses: No `id` property, no async helper — only sync via trait. `execute_accepts_timeout`-style introspection does not exist; capability negotiation is a one-shot JSON blob.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/protocol.py:882`, `:940`, `:843`, `:863`
- Implementation: `SandboxBackendProtocol` extends `BackendProtocol` with `id` property, `execute(command, **kwargs) -> ExecuteResponse`, and `aexecute()` async variant. `ExecuteResponse` and `ExecuteOffloadResult` dataclasses carry typed outputs (`output`, `exit_code`, `offload`). Cached helper `execute_accepts_timeout` introspects the bound method signature once per backend.
- Strengths: First-class `id`, async path, offload-result envelope, signature introspection — production-grade.
- Weaknesses: Python-only protocol; no DAG awareness.

### Gap Analysis
- Parity: Partial
- Severity: Important
- Recommendation: Add `id(&self) -> &str` and `aexecute()` to `RuntimeModule`; carry an offload envelope in `ToolResultItem` (`offloaded: Option<Value>`). Keep DAG semantics — DeepAgents lacks them.

---

## 2. Local shell access

### ARFV1
- File(s): `crates/arf-mcp/src/runtime.rs:55-62`, `crates/arf-mcp/src/script.rs:148-251`
- Implementation: `LocalRuntime` advertises `{"runtime":"local","concurrency":"layer-parallel"}` and delegates to the default executor. Actual shell execution happens inside `ScriptTool::execute` — every tool runs as a host subprocess (`tokio::process::Command`), Python/Bash/Rust. Params flow stdin → JSON, stdout → JSON. `kill_on_drop(true)` ensures cleanup.
- Strengths: Strong isolation per call (fresh process); Rust on-demand compilation cached by mtime (`script.rs:89-126`); timeout + cancel via `tokio::select!`.
- Weaknesses: No syscall filter, no seccomp, no namespace — host shell access is unrestricted.

### DeepAgents
- File(s): `libs/deepagents/deepends/local_shell.py:387`, `:23`, `:34-86`
- Implementation: `LocalShellBackend` runs `subprocess.run(...)` directly on the host with `DEFAULT_EXECUTE_TIMEOUT = 120s`. Module docstring at `:34-86` is a long security warning about prompt-injection and arbitrary FS access.
- Strengths: Simple, no IPC overhead, low latency.
- Weaknesses: Same unrestricted-host risk; warning text indicates the maintainers know this is dangerous.

### Gap Analysis
- Parity: Yes
- Severity: Critical
- Recommendation: Both leak host. ARFV1 needs a `SandboxRuntime` impl that forwards to a hardened backend (Firecracker / nsjail / Docker); mirror DeepAgents' explicit warning docstring at `runtime.rs` top.

---

## 3. Remote sandbox

### ARFV1
- File(s): `crates/arf-mcp/src/remote.rs:60-114`, `crates/arf-mcp/src/runtime.rs:66-75`
- Implementation: `RemoteRuntime` advertises `{"runtime":"remote"}` and reuses the default executor. `HttpProxyTool` posts JSON-RPC `tools/call` to a remote MCP server. Per-tool timeout from `RemoteConfig.timeout_secs`.
- Strengths: Standards-based MCP transport; per-call timeout honoured.
- Weaknesses: "Remote sandbox" semantics are bolted onto "remote MCP server" — no first-class sandbox abstraction; no offload; no per-sandbox ID.

### DeepAgents
- File(s): `libs/deepagents/deepends/sandbox.py:1345`, `libs/deepagents/deepends/langsmith.py:279`
- Implementation: `BaseSandbox` ABC exposes `ls/read/write/edit/upload_files` via `execute`+`upload_files`. `LangSmithSandbox` wraps `langsmith.sandbox.Sandbox` with `enable_capture_offload = True` (`:53`).
- Strengths: Explicit sandbox primitive; file ops and exec are unified; offload is opt-in per backend.
- Weaknesses: LangSmith coupling; vendor-specific upload API.

### Gap Analysis
- Parity: Partial
- Severity: Important
- Recommendation: Introduce `SandboxBackend` trait in `arf-mcp` that exposes file ops + exec + id, then build a `LangSmithSandboxAdapter` or `E2BSandboxAdapter` on top. RemoteRuntime becomes a degenerate case.

---

## 4. Execute tool visibility

### ARFV1
- File(s): `crates/arf-mcp/src/discovery.rs:33-34`, `crates/arf-engine/src/engine.rs:972-1025`
- Implementation: All tools discovered by `FsDiscovery.scan`/`HttpDiscovery.connect` are advertised to the model in `node_info.capabilities.tools` (`node.rs:110-116`). There is no notion of "execute" being a special tool — it's whatever the script runtime exposes. Engine gates every call via `ToolPermission` Allow/Ask/Deny.
- Strengths: Uniform surface; permission policy is centralized.
- Weaknesses: No way to expose a transient `execute` only when sandbox is present; FS/exec are coupled.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/filesystem.py:3102`
- Implementation: `FilesystemMiddleware` builds the `execute` tool dynamically; suppressed entirely when the bound backend cannot execute. Capability is data-driven from the backend protocol.
- Strengths: Clean conditional surfacing; model never sees a tool it cannot use.
- Weaknesses: None significant.

### Gap Analysis
- Parity: Partial
- Severity: Useful
- Recommendation: Add `RuntimeModule.supports_execute() -> bool`; have `McpNode::build_node_info` filter out execute-like tools when false.

---

## 5. Execute timeout

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:28`, `:44`
- Implementation: `EngineConfig.tool_timeout_ms: Option<u64>` defaults to `Some(30_000)` (30s). Propagated into `ToolCallSet.timeout_ms` (`types.rs:80`) and enforced in `executor.rs:60`. Per-tool overrides via `ScriptTool::timeout_ms` (`script.rs:38`, `:197-217`).
- Strengths: Two-tier (engine-wide + per-tool), wired to actual `tokio::time::sleep`.
- Weaknesses: 30s default is too short for `pip install` / `cargo build`; no soft-then-hard escalation.

### DeepAgents
- File(s): `libs/deepagents/deepends/local_shell.py:23`
- Implementation: `DEFAULT_EXECUTE_TIMEOUT = 120s`; passed to `subprocess.run(timeout=...)`. Single global knob.
- Strengths: 4× more headroom.
- Weaknesses: No per-command override; no signal escalation (SIGTERM → SIGKILL).

### Gap Analysis
- Parity: Partial
- Severity: Important
- Recommendation: Bump ARFV1 default to 120s; add per-tool override path; document in `ToolConfig`.

---

## 6. Sandbox file ops

### ARFV1
- File(s): `crates/arf-mcp/src/script.rs:148-251`, `crates/arf-mcp/src/discovery.rs:87-131`
- Implementation: File ops are just tools (`read_file`, `write_file`) discovered from `tools/*/tool.toml`. They are external subprocesses — no notion of "sandbox FS". `FsDiscovery` scans the host filesystem.
- Strengths: Trivial to add tools; tools can be anything (Rust, Python, Bash).
- Weaknesses: Host FS only — no sandboxed FS view.

### DeepAgents
- File(s): `libs/deepagents/deepends/sandbox.py:1345`
- Implementation: `BaseSandbox` implements `ls/read/write/edit/upload_files` **by calling `execute` under the hood** plus `upload_files`. Files live in the sandbox, not on the host.
- Strengths: Unified abstraction; isolation by construction.
- Weaknesses: Implementation goes through shell — slower than VFS; race-prone.

### Gap Analysis
- Parity: Missing
- Severity: Important
- Recommendation: Define `SandboxFS` trait alongside `RuntimeModule`; default impl uses host FS gated by chroot/Docker.

---

## 7. Execute offload

### ARFV1
- File(s): `crates/arf-mcp/src/types.rs:80-100`
- Implementation: `ToolCallSet.timeout_ms: Option<u64>`. `ToolResultItem` carries `result: Value` only — no offload envelope.
- Strengths: Simple.
- Weaknesses: No mechanism to redirect a 10MB stdout to a file the model can reference later. Context window gets bloated.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/protocol.py:863`
- Implementation: `ExecuteOffloadResult` dataclass returns `{output, exit_code, offload}`. Backend writes to its storage; model receives a reference.
- Strengths: Bounded context usage; large logs survive.
- Weaknesses: Backend-specific offload targets — not portable.

### Gap Analysis
- Parity: Missing
- Severity: Important
- Recommendation: Add `offloaded: Option<OffloadRef>` to `ToolResultItem`; bus message type `tool_offload_ref` for follow-up reads.

---

## 8. Capture offload (LangSmith)

### ARFV1
- File(s): N/A
- Implementation: Concept does not exist.

### DeepAgents
- File(s): `libs/deepagents/deepends/langsmith.py:53`
- Implementation: `enable_capture_offload = True` on `LangSmithSandbox`. Auto-offloads terminal capture from interactive commands.
- Strengths: Solves a real UX problem (TUI commands never return).
- Weaknesses: LangSmith-only; no equivalent for other sandboxes.

### Gap Analysis
- Parity: Missing
- Severity: Useful
- Recommendation: Add a generic `enable_capture_offload: bool` on a future `SandboxBackend` trait; ARFV1 has no interactive-shell primitive yet.

---

## 9. Async execute

### ARFV1
- File(s): `crates/arf-mcp/src/script.rs:148-251`
- Implementation: All execution is async via `tokio::process::Command`. `ScriptTool::execute` is `async fn`. `LocalRuntime` uses `executor::execute` which calls `tokio::spawn` per layer.
- Strengths: Fully async; no thread-per-call.
- Weaknesses: No `aexecute` distinction — async is the only path.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.11`
- Implementation: `aexecute` added in 0.6.11, routed through `BaseSandbox` async helpers. Sync `execute` is retained for backwards compat.
- Strengths: Both paths; async is preferred.
- Weaknesses: Two API surfaces to maintain.

### Gap Analysis
- Parity: Yes
- Severity: Useful
- Recommendation: None. ARFV1's async-only design is cleaner.

---

## 10. Capability introspection

### ARFV1
- File(s): `crates/arf-mcp/src/runtime.rs:25`
- Implementation: `capabilities() -> Value` returns a JSON blob. No caching. No signature introspection.
- Strengths: Opaque blob — flexible.
- Weaknesses: Consumers must string-match keys; no type safety.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/protocol.py:940`
- Implementation: `@cached_property execute_accepts_timeout` checks `inspect.signature(...).parameters` once per backend.
- Strengths: Type-safe; cached; zero-cost after first call.
- Weaknesses: Python introspection is dynamic — easy to break.

### Gap Analysis
- Parity: Partial
- Severity: Useful
- Recommendation: Add `#[non_exhaustive] Capability` enum in `arf-mcp` with `HasTimeout`, `HasOffload`, `HasSandboxedFS` variants. Replace string blob.

---

## 11. Sandbox ID

### ARFV1
- File(s): `crates/arf-mcp/src/node.rs:18`
- Implementation: `McpNode` has `node_id: NodeId` (e.g. `mcp/<namespace>`). Multiple sandboxes live behind one node.
- Strengths: Stable bus address.
- Weaknesses: Cannot distinguish two sandboxes under one namespace.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/protocol.py:882`
- Implementation: `id` property is required on `SandboxBackendProtocol`. Each sandbox instance has a unique id.
- Strengths: Multi-sandbox routing possible.
- Weaknesses: None.

### Gap Analysis
- Parity: Partial
- Severity: Useful
- Recommendation: `RuntimeModule::id() -> &str`; allow multiple `McpNode`s per namespace, distinguished by sandbox id.

---

## 12. LocalRuntime subprocess model

### ARFV1
- File(s): `crates/arf-mcp/src/script.rs:148-251`
- Implementation: One subprocess per tool call. stdin=params JSON, stdout=result JSON, stderr=captured for error. `kill_on_drop(true)` + `tokio::select!` race between `wait_with_output`, cancel, and timeout.
- Strengths: Strong per-call isolation; clean cancellation; mtime-based Rust compile cache.
- Weaknesses: Cold-start per call; no stdout streaming.

### DeepAgents
- File(s): `libs/deepagents/deepends/local_shell.py:387`
- Implementation: `subprocess.run(...)` — blocking, one call at a time per backend. Streams captured into `ExecuteResponse.output`.
- Strengths: Streams available via `capture_output=True`.
- Weaknesses: Blocking call kills async benefits unless wrapped in `asyncio.to_thread`.

### Gap Analysis
- Parity: Partial (better isolation, worse streaming)
- Severity: Useful
- Recommendation: Add optional `stream: bool` to `ScriptTool::execute` and forward stdout chunks via `tool_partial` bus message.

---

## 13. HttpProxyTool MCP isError

### ARFV1
- File(s): `crates/arf-mcp/src/remote.rs:116-136`
- Implementation: `call_result_to_output` honours MCP `isError: true` — converts into `ToolError` rather than wrapping as success. Phase 9 F-011.
- Strengths: Correct error semantics; unit-testable.
- Weaknesses: None.

### DeepAgents
- File(s): `libs/deepagents/deepends/sandbox.py` (subclass impls)
- Implementation: Errors propagate via `exit_code` + non-empty stderr in `ExecuteResponse`.
- Strengths: Lower-level — caller decides error policy.
- Weaknesses: Two conventions (MCP `isError` vs shell exit codes) — confusing cross-protocol.

### Gap Analysis
- Parity: Yes
- Severity: Useful
- Recommendation: None. ARFV1's `isError`-as-error is correct.

---

## 14. Threat modeling

### ARFV1
- File(s): N/A
- Implementation: No `THREAT_MODEL.md` exists. `ToolPermission::Ask` (`engine.rs:995`) provides some protection but only at the user-approval layer.
- Strengths: Default deny via Allow-list required.
- Weaknesses: No documented threat model; prompt injection → unrestricted host shell possible.

### DeepAgents
- File(s): `libs/deepagents/libs/THREAT_MODEL.md`
- Implementation: Maintained threat model document covering prompt injection, sandbox escape, data exfiltration. `LocalShellBackend` docstring repeats the warning.
- Strengths: Explicit adversary enumeration; users know what they're deploying.
- Weaknesses: Document only — no built-in enforcement beyond `Backend` choice.

### Gap Analysis
- Parity: Missing
- Severity: Critical
- Recommendation: Author `docs/v1.x/threat-model.md` for ARFV1 before shipping a sandbox runtime. Treat `LocalRuntime` as high-risk in the doc.

---

## 15. Sandbox + FS composability

### ARFV1
- File(s): `crates/arf-mcp/src/runtime.rs:19-50`
- Implementation: A `McpNode` has exactly one `RuntimeModule` and one `DiscoveryBackend`. No built-in way to say "FS ops go to backend A, exec goes to backend B".
- Strengths: Simple topology.
- Weaknesses: Cannot route a `read_file` call to one backend and `execute` to another.

### DeepAgents
- File(s): `libs/deepagents/deepends/protocol.py:982-983`
- Implementation: `CompositeBackend` + `BackendFactory._resolve_backend` route per command to the appropriate backend.
- Strengths: Production flexibility — LocalShell + LangSmith coexist.
- Weaknesses: Routing rules are implicit.

### Gap Analysis
- Parity: Missing
- Severity: Important
- Recommendation: Introduce `CompositeRuntimeModule` that dispatches per-tool to one of N `RuntimeModule` impls. Reuse `BackendFactory` pattern.

---

## 16. Sandbox persistence

### ARFV1
- File(s): `crates/arf-mcp/src/types.rs:80-100`, `crates/arf-engine/src/engine.rs:1108-1111`
- Implementation: `ToolResultItem.result` flows back to engine, pushed into `state.messages` as a `tool` role message (`engine.rs:1108-1111`). Lives in the conversation history until context-managed.
- Strengths: Uniform persistence path.
- Weaknesses: No offload — large outputs bloat context forever.

### DeepAgents
- File(s): `libs/deepagents/libs/CHANGELOG.md:0.6.11`
- Implementation: Offload results stored in backend; only a reference reaches the conversation.
- Strengths: Bounded context.
- Weaknesses: Reference lifetime tied to backend lifetime.

### Gap Analysis
- Parity: Partial
- Severity: Important
- Recommendation: Pair offload with an `OffloadStore` trait (`read`/`write`/`list`) in `arf-mcp` so engines can dedupe refs across sessions.

---

## 17. Remote sandbox abstraction (BaseSandbox)

### ARFV1
- File(s): `crates/arf-mcp/src/runtime.rs:66-75`
- Implementation: No sandbox ABC. `RemoteRuntime` is the closest thing — a marker that says "tools are remote".
- Strengths: Zero ceremony.
- Weaknesses: No `upload_files`, no per-sandbox state.

### DeepAgents
- File(s): `libs/deepagents/deepends/sandbox.py:1345`
- Implementation: `BaseSandbox` ABC: file ops via `execute`+`upload_files`. Subclasses (`LocalSandbox`, `DockerSandbox`, `LangSmithSandbox`) implement.
- Strengths: Portable contract; users pick sandbox by class.
- Weaknesses: ABC coupling between file ops and `execute` is rigid.

### Gap Analysis
- Parity: Missing
- Severity: Important
- Recommendation: Add `SandboxBackend` trait in `arf-mcp` with `execute`, `read_file`, `write_file`, `upload_files`, `id`.

---

## 18. Default sandbox

### ARFV1
- File(s): `crates/arf-mcp/src/node.rs:28-38`
- Implementation: `McpNode::local(...)` constructs `LocalRuntime` by default. No env-var override.
- Strengths: Predictable default.
- Weaknesses: Unsafe default — no sandbox. Apps must opt-in.

### DeepAgents
- File(s): `libs/deepagents/libs/THREAT_MODEL.md` + middleware
- Implementation: Default backend is `StateBackend` (in-memory) when none supplied — `execute` tool is suppressed because the backend cannot execute.
- Strengths: Safe default — opt-in to local shell.
- Weaknesses: Power users must import explicitly.

### Gap Analysis
- Parity: Partial (different defaults)
- Severity: Important
- Recommendation: Make `LocalRuntime` opt-in; default `McpNode::local` to a `StateRuntime` (no FS, no exec). Flip in v2.0 — same migration cost as DeepAgents 0.6.x.

---

## 19. Sandbox timeout vs session timeout

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:28`, `crates/arf-mcp/src/script.rs:197-217`
- Implementation: Engine timeout (`tool_timeout_ms`, default 30s) wraps the call. Script timeout (`ScriptTool::timeout_ms`) is per-tool. They are independent — both fire `tokio::select!` and both kill the subprocess.
- Strengths: Two independent guards.
- Weaknesses: Race when both fire — engine kills after 30s while script kills earlier.

### DeepAgents
- File(s): `libs/deepagents/deepends/local_shell.py:23`
- Implementation: Single timeout from `DEFAULT_EXECUTE_TIMEOUT`; no session-level wrapper.
- Strengths: Simpler.
- Weaknesses: Long-running agents have no outer guard.

### Gap Analysis
- Parity: Partial
- Severity: Useful
- Recommendation: Document precedence — engine timeout > script timeout. Add warning when both are set.

---

## 20. Script runtime

### ARFV1
- File(s): `crates/arf-mcp/src/script.rs:1-265`, `crates/arf-mcp/src/config.rs` (`ScriptRuntime::Python|Bash|Rust`)
- Implementation: `ScriptTool` is the universal tool wrapper. Config selects runtime. Stdin=params JSON, stdout=result JSON, stderr=error text. Rust runtime auto-compiles on mtime change and caches the binary.
- Strengths: First-class script runtime with auto-compile + cache; per-tool cancel via `oneshot`; `kill_on_drop`.
- Weaknesses: No streaming; no env-var isolation; no seccomp.

### DeepAgents
- File(s): `libs/deepagents/deepends/local_shell.py:387`
- Implementation: Single shell primitive. No first-class Rust/Python wrapper.
- Strengths: Fewer abstractions.
- Weaknesses: Python SDK users reinvent ScriptTool for each tool.

### Gap Analysis
- Parity: Better in ARFV1
- Severity: Useful
- Recommendation: Document `ScriptTool` as ARFV1's differentiator. Add `--streaming` and `--env-isolated` flags in v1.2.

---

## Summary Table

| # | Capability | ARFV1 | DeepAgents | Winner |
|---|------------|-------|------------|--------|
| 1 | Backend protocol | `RuntimeModule` trait | `SandboxBackendProtocol` (+ `id`, `aexecute`) | DeepAgents |
| 2 | Local shell | `ScriptTool` subprocess | `LocalShellBackend` subprocess | Tie (both unsafe) |
| 3 | Remote sandbox | `HttpProxyTool` (MCP) | `LangSmithSandbox` / `BaseSandbox` | DeepAgents |
| 4 | Execute tool visibility | Always exposed | Conditional on backend | DeepAgents |
| 5 | Execute timeout | 30s default, per-tool override | 120s default | DeepAgents |
| 6 | Sandbox file ops | External tools | Built into `BaseSandbox` | DeepAgents |
| 7 | Execute offload | Missing | `ExecuteOffloadResult` | DeepAgents |
| 8 | Capture offload | Missing | LangSmith-only | DeepAgents |
| 9 | Async execute | Async-only | `aexecute` (0.6.11) | ARFV1 |
| 10 | Capability introspection | JSON blob | Cached `@property` | DeepAgents |
| 11 | Sandbox ID | `node_id` only | `id` property | DeepAgents |
| 12 | LocalRuntime subprocess | Async + per-tool cancel | Blocking `subprocess.run` | ARFV1 |
| 13 | MCP isError | Honoured (`remote.rs:120`) | Shell exit code | ARFV1 |
| 14 | Threat modeling | Missing | `THREAT_MODEL.md` | DeepAgents |
| 15 | Sandbox + FS composability | Missing | `CompositeBackend` | DeepAgents |
| 16 | Persistence | Conversation history | Offload store | DeepAgents |
| 17 | Remote sandbox ABC | Missing | `BaseSandbox` | DeepAgents |
| 18 | Default backend | `LocalRuntime` (unsafe) | `StateBackend` (safe) | DeepAgents |
| 19 | Timeout interaction | Two-tier (engine + tool) | Single | ARFV1 |
| 20 | Script runtime | `ScriptTool` (Rust/Py/Bash) | None | ARFV1 |

**Verdict**: DeepAgents is ahead 14:6 on capability breadth. ARFV1 wins on async-first execution and the `ScriptTool` runtime. The most important gap is the absence of an explicit sandbox primitive, a threat model document, and safe defaults — all critical before exposing `LocalRuntime` to user-supplied prompts.