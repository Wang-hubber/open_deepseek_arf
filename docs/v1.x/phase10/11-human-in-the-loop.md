# Human-in-the-Loop — Atomic Comparison (ARFV1 vs DeepAgents)

> Scope: how ARFV1 and DeepAgents (langgraph-backed) gate tool execution on
> human approval, expose an explicit "ask the human" API, and reflect
> suspended state in the session.

---

## 1. HITL mechanism — pause the agent before a risky tool

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:995-1023` (`do_tool_turn`,
  `Ask` branch); `crates/arf-engine/src/engine.rs:1131-1174`
  (`request_permission`).
- Implementation: When `ToolPermission::Ask` is configured, the Engine
  constructs a `permission_request` bus message and **parks the ReAct
  turn** in `wait_for_strategy` until a `permission_response` returns.
  The original `CancellationToken` is forwarded so the wait can be
  cancelled alongside the round.
- Strengths: Clean separation — Engine drives the gate, UI/operator is
  just another bus subscriber; the round stays in `Active` state.
- Weaknesses: Synchronous wait ties up the ReAct turn for the entire
  response latency; no soft timeout (caller-controlled cancellation
  only).

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:946-951`
  (`HumanInTheLoopMiddleware(install if interrupt_on non-None)`).
- Implementation: When `interrupt_on` is non-`None`,
  `HumanInTheLoopMiddleware` wraps each tool node in LangGraph's
  `interrupt(...)` primitive. The graph checkpoints state, suspends
  execution, and resumes when the caller invokes the graph again with a
  Command carrying the human decision.
- Strengths: Built on LangGraph's durable checkpoint model — the
  interrupt survives process restart.
- Weaknesses: Requires caller to drive resume via
  `graph.invoke(Command(resume=...))`; no in-graph timeout.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: ARFV1 already has the bus primitives. Adding a
  `wait_for_strategy(..., timeout=...)` shortcut and recording the
  interrupt in `SessionStore` as `Interrupted` would close the durability
  gap.

---

## 2. Permission enum — the policy vocabulary

### ARFV1
- File(s): `crates/arf-core/src/tool.rs:9-27` (`ToolPermission`).
- Implementation: Three-variant enum `Allow | Ask | Deny`, serialized
  lowercase. Default is `Allow` so legacy `ToolSpec::new(...)` callers
  preserve prior behaviour.
- Strengths: Explicit `Deny` produces a deterministic `tool_result`
  error without bus traffic (`engine.rs:974-993`); `Ask` semantics are
  obvious.
- Weaknesses: Only a static flag per tool — cannot express "ask only on
  first call" or "ask only if path is sensitive".

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/filesystem.py:252-285`
  (`FilesystemPermission` with `mode` field);
  `libs/deepagents/libs/CHANGELOG.md:0.6.8` (`interrupt` mode added).
- Implementation: `mode: Literal["allow", "deny", "interrupt"]`. `allow`
  and `deny` short-circuit; `interrupt` installs the LangGraph interrupt.
- Strengths: Same three-way vocabulary as ARFV1 plus interrupt is
  composable with FS permission rules.
- Weaknesses: `interrupt` is a single mode flag — cannot combine with
  approve-always-this-session.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Consider a `ApproveOnce | ApproveSession` extension
  for the `Ask` variant in a future task.

---

## 3. Interrupt mode on permissions — pause before risky op

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:995-1023` (Ask gating).
- Implementation: `Ask` is the only "pause" mode. The Engine serializes a
  payload containing `correlation_id`, `tool_name`, `arguments`, and
  `tool_call_id`, then awaits `permission_response` (`engine.rs:1131-1174`).
- Strengths: Bidirectional — `tool_call_id` lets the UI render the exact
  LLM-intended call.
- Weaknesses: No concept of "auto-approve on resume" — every Ask
  triggers a fresh bus round-trip.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_fs_interrupt.py:183`
  (`_build_interrupt_on_from_permissions(mode="interrupt")`).
- Implementation: For each `FilesystemPermission(mode="interrupt")` a
  LangGraph `interrupt` is installed at the middleware layer so the
  check happens **before** the tool runs, with full context.
- Strengths: Synchronous with LangGraph checkpoint — exactly once even
  on retry.
- Weaknesses: Tightly coupled to FS tool path-arg mapping.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: None — capability equivalent.

---

## 4. Per-tool interrupt configuration

### ARFV1
- File(s): `crates/arf-core/src/tool.rs:29-41` (`ToolSpec.permission`).
- Implementation: A `ToolSpec` carries one `permission` field; the
  Engine looks it up by tool name in `do_tool_turn`
  (`engine.rs:972-954`). Resolution is per-tool, static, configured at
  spec-build time.
- Strengths: Static config = zero runtime cost; clear ownership.
- Weaknesses: Cannot register a *runtime* per-callable approver — there
  is no way for the agent to say "for this call, also ask the user
  even though the spec is Allow".

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py` (`interrupt_on`
  parameter — per-tool or per-callable config).
- Implementation: `interrupt_on` is a mapping of `{tool_name: bool |
  Callable}`. A `True` enables interrupt unconditionally; a callable
  receives the call and returns bool (e.g., "ask only when path is
  outside `/tmp`").
- Strengths: Callable approver is a powerful escape hatch.
- Weaknesses: Callable must be sync-callable from inside the LangGraph
  node; no async support shown.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add `ToolPermission::AskIf(Box<dyn Fn(&ToolCall) ->
  bool>)` variant in `arf-core::tool` so apps can express contextual
  gating without giving up the static `Ask` path.

---

## 5. FS-tool path-arg mapping (interrupt synthesis)

### ARFV1
- File(s): Not present. ARFV1 has no `FilesystemMiddleware`-equivalent.
- Implementation: ARFV1 routes all FS ops through generic `tool_exec`
  (`engine.rs:1036-1053`) with permission looked up by name. There is no
  path-aware interrupt synthesis.
- Strengths: Simpler — no FS-specific knowledge baked into Engine.
- Weaknesses: An app that wants "ask before any write to `/etc`" must
  write its own permission callable; no built-in helper.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_fs_interrupt.py:38`
  (`_FS_TOOL_PATH_ARGS`).
- Implementation: A static map of `(tool_name -> (op_kind, path_arg_name,
  scope))` so the middleware can derive an interrupt-on entry from a
  `FilesystemPermission(mode="interrupt", path=...)` rule.
- Strengths: DRY — declare intent in one place, the helper installs
  interrupts for every matching tool.
- Weaknesses: Mapping is hand-maintained; new FS ops need a code change.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Provide a `ToolPermissionRule` helper module in
  `arf-core` so apps can declare path-scoped permissions without
  hand-rolling FS-tool matching.

---

## 6. Permission-driven interrupt synthesis

### ARFV1
- File(s): Implicit in `lookup_tool_permission`
  (`engine.rs:946-955`) — straight lookup, no synthesis.
- Implementation: 1:1 map from `ToolSpec.permission` to gating
  behaviour. No derivation step.
- Strengths: Predictable.
- Weaknesses: No way to compose multiple permission sources (e.g.,
  global rule + per-tool override).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_fs_interrupt.py:183`
  (`_build_interrupt_on_from_permissions`).
- Implementation: Walks `FilesystemPermission` rules and synthesises an
  `interrupt_on` dict, merging with caller-supplied entries.
- Strengths: Composition — declarative permission rules + imperative
  interrupt overrides coexist.
- Weaknesses: Resolution order is implicit.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: A `permission_resolver` trait in `arf-core` that
  composes static `ToolSpec.permission` with runtime rule lists would
  match DeepAgents' flexibility.

---

## 7. Handoff API — explicit "ask the human"

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:661-733`
  (`Engine::handoff_to_human`).
- Implementation: Public method that records an `OutboundSent` event
  via `maybe_record_outbound` (Task 19 unified outbox), then sends a
  `HumanHandoff` message to `["ui"]` and parks on a `WaitEvent` for
  the matching `HumanHandoffReply`. Owns a `CancellationToken` for
  timeout. Returns the decoded `HumanHandoffReply`.
- Strengths: First-class method on Engine, with timeout-cancel, dedup
  via inbound LRU, and trace integration in one place.
- Weaknesses: Locked to the `"ui"` recipient — apps wanting escalation
  to multiple channels must re-implement.

### DeepAgents
- File(s): Implicit — DeepAgents expresses "ask the human" by
  configuring an `interrupt_on` for a synthetic tool and letting the
  caller resume with a Command. There is no equivalent named API.
- Implementation: There is no dedicated `handoff_to_human` —
  human-in-the-loop is purely "interrupt a tool".
- Strengths: Composable with any tool.
- Weaknesses: No first-class concept of "ask a free-form question" —
  every human touchpoint must be modelled as a tool.

### Gap Analysis
- Parity: ✅ (ARFV1 ahead on named API)
- Severity: 🟢 (ARFV1 has the better primitive)
- Recommendation: None — ARFV1's `handoff_to_human` is a cleaner
  primitive than DeepAgents' tool-only model.

---

## 8. `HumanHandoff` ActionMessage wire (outbound)

### ARFV1
- File(s): `crates/arf-core/src/message.rs:499-539`
  (`HumanHandoff`); `crates/arf-core/src/msg_type.rs:11-34`
  (`HUMAN_HANDOFF`).
- Implementation: `ActionMessage` impl with `msg_type =
  "human_handoff"`, `MessageIntent::Query`, fields `correlation_id`,
  `question`, `context`, `options`.
- Strengths: `options: Vec<String>` lets the UI render a button row
  instead of an open text prompt.
- Weaknesses: No streaming / partial reply model.

### DeepAgents
- Not applicable — DeepAgents has no equivalent typed wire message;
  interrupts carry only the tool-call payload.

### Gap Analysis
- Parity: ✅ (ARFV1 has typed wire; DeepAgents has untyped interrupt
  payload)
- Severity: 🟡 Useful
- Recommendation: None.

---

## 9. `HumanHandoffReply` response wire (inbound)

### ARFV1
- File(s): `crates/arf-core/src/message.rs:541-573`
  (`HumanHandoffReply`).
- Implementation: `ActionMessage` impl, `msg_type =
  "human_handoff_reply"`, `MessageIntent::Command`, fields
  `correlation_id`, `answer: String`, `selected_option: Option<usize>`.
  The Engine dedups replies via `InboundDedupCache`
  (`engine.rs:743-769`) keyed on `correlation_id`.
- Strengths: LRU dedup prevents double-process on restart; `answer` and
  `selected_option` are both available so the model sees structured
  feedback.
- Weaknesses: No multi-value reply — the model gets one answer.

### DeepAgents
- Not applicable.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: None.

---

## 10. Session status reflection — `SessionStatus::Interrupted`

### ARFV1
- File(s): `crates/arf-session/src/lib.rs:27-65`
  (`SessionStatus::Interrupted`).
- Implementation: `snapshot()` forces `status = 'interrupted'` on every
  checkpoint write (`session/src/lib.rs:726-789`), with the
  Cancelling-vs-Interrupted carve-out in `R7-L2`. The status survives
  save/load round-trips.
- Strengths: A session that crashed mid-interrupt is recognisable on
  next launch — UI can offer "Resume waiting for approval".
- Weaknesses: `Interrupted` is also used for panic/OOM — the same
  status covers "asked permission and got no reply" and "process died".

### DeepAgents
- File(s): Implicit in LangGraph — interrupt state is captured in the
  thread/checkpoint, not a separate status enum.
- Implementation: LangGraph's checkpoint carries `next` nodes including
  the interrupt; load_session returns the suspended node.
- Strengths: More granular — can distinguish "paused at interrupt X"
  from "errored at node Y".
- Weaknesses: Application code must interpret the checkpoint state
  itself.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Add a `SessionStatus::AwaitingPermission { tool_name,
  correlation_id }` variant so the UI can render a precise resume flow.

---

## 11. Permission request broadcast

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1146-1158`
  (`Message::new_broadcast(PERMISSION_REQUEST, ...)`).
- Implementation: The Engine broadcasts a `permission_request` to the
  whole bus, then registers a `WaitEvent` and awaits
  `permission_response`. Any UI/operator node can answer.
- Strengths: Decoupled responder — no Engine refactor needed to add a
  second approver (e.g., a Slack bot alongside the UI).
- Weaknesses: Broadcast traffic; possible spurious replies if two
  responders both reply (LRU dedup mitigates after first).

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:946-951`
  (`HumanInTheLoopMiddleware`).
- Implementation: Interrupt is captured inside the graph node — no
  external broadcast.
- Strengths: Tighter scope, no extra traffic.
- Weaknesses: Cannot attach an out-of-process approver without
  customising the graph.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: None.

---

## 12. Permission response timeout handling

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1131-1174`
  (`request_permission`); `crates/arf-engine/src/engine.rs:661-733`
  (`handoff_to_human` uses `tokio::time::sleep(timeout).await; cancel`).
- Implementation: For `request_permission`, timeout is via the passed
  `CancellationToken` (no in-method timeout). For `handoff_to_human`, a
  timeout-cancel `CancellationToken` is spawned via `tokio::time::sleep`
  (`engine.rs:703-709`). On timeout, the wait returns empty and the
  tool call is treated as denied (`engine.rs:1001-1020` — user-denied
  error path).
- Strengths: Consistent — both Ask and Handoff treat "no response" as
  deny.
- Weaknesses: No `None`-vs-`Partial` distinction — caller cannot tell
  "no responder" from "responder explicitly denied".

### DeepAgents
- File(s): Implicit in LangGraph `interrupt` — no built-in timeout;
  caller decides how long to wait before giving up.
- Implementation: No timeout primitive in the middleware.
- Strengths: Caller-controlled.
- Weaknesses: Inconsistent — apps must re-implement per use.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: ARFV1's "treat timeout as deny" is sensible; a
  `PermissionOutcome { Granted, Denied, Timeout }` enum would let
  callers distinguish.

---

## 13. Re-approval persistence — does approval survive resume?

### ARFV1
- File(s): Not implemented. `request_permission` runs fresh on every
  call; no "approved earlier in this session" cache.
- Implementation: Each `Ask` tool call always triggers a fresh
  `permission_request` broadcast.
- Strengths: Conservative — security wins over UX.
- Weaknesses: User must re-approve every `write_file`, even if they
  approved the same args a turn earlier.

### DeepAgents
- File(s): Not directly stored. LangGraph's checkpoint restores the
  graph state, but if the graph re-enters an `interrupt` node the
  interrupt fires again.
- Implementation: Same — every re-entry re-prompts.
- Strengths: Symmetric with ARFV1.
- Weaknesses: Same.

### Gap Analysis
- Parity: ✅ (both lack)
- Severity: 🟡 Useful
- Recommendation: Add `SessionStore::record_permission_grant(session_id,
  tool_name, args_hash, expires_at)` and consult it in
  `request_permission` before broadcasting.

---

## 14. Caller-supplied approver function

### ARFV1
- File(s): Not implemented. `ToolPermission` is an enum, not a callable.
- Implementation: No path for apps to plug in custom logic at
  permission-check time.
- Strengths: None over DeepAgents here.
- Weaknesses: Cannot express "ask only on writes outside `/tmp`".

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py`
  (`interrupt_on` accepts a `Callable` value).
- Implementation: A user-supplied function receives the `ToolCall` and
  returns `True` (interrupt) or `False` (pass).
- Strengths: Full expressive power.
- Weaknesses: Callable is sync; no async helpers shown.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Extend `ToolPermission` with a
  `AskIf(Arc<dyn Fn(&ToolCall, &State) -> bool + Send + Sync>)`
  variant; resolve in `lookup_tool_permission` before static `Ask`.

---

## 15. Tool result after interrupt — accepted / edited / rejected

### ARFV1
- File(s): Not implemented. The reply is binary allow/deny.
- Implementation: `permission_response` carries only `allow: bool`
  (`engine.rs:1168-1172`); a denied call produces a hard error result.
- Strengths: Simple.
- Weaknesses: The human cannot edit the tool arguments before approval
  (e.g., "change the path to `/tmp/x` and approve").

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/human_in_the_loop.py`
  (3-state `HITLResponse` of `accept | edit | reject`).
- Implementation: Returning `edit` lets the human modify the tool
  arguments before the tool runs.
- Strengths: Most user-friendly.
- Weaknesses: Editing is mid-tool — must be carefully composed.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Extend `permission_response` schema with
  `{allow, edited_args?}`; if `edited_args` present, the Engine
  replaces `tc.arguments` before dispatching `tool_exec`.

---

## 16. Filesystem permission interrupt coverage

### ARFV1
- File(s): None — no FS-specific permission layer.
- Implementation: Apps configure per-tool permission via `ToolSpec`.
- Strengths: General.
- Weaknesses: Apps must hand-roll FS coverage.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/filesystem.py:288-298`
  (permission check); `_FS_TOOL_PATH_ARGS:38` (tool-to-path-arg map).
- Implementation: Coverage spans `ls`, `read_file`, `write_file`,
  `edit_file`, `glob`, `grep`, `execute` — interrupt is applied per
  tool via the path-arg map.
- Strengths: Turn-key.
- Weaknesses: Static list.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Ship a `filesystem-permission` helper crate that
  maps `ls/read/write/edit/glob/grep/exec` to `ToolPermission` rules.

---

## 17. Multi-step approval — compound tool calls

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:959-1126` (`do_tool_turn`
  invoked per `ToolCall`).
- Implementation: Each tool call is gated individually — a model
  response with 3 `Ask` tool calls triggers 3 separate
  `permission_request`s.
- Strengths: Granular — partial approval is natural.
- Weaknesses: Three round-trips when one might suffice; the user sees
  the calls in the order the LLM emitted them.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py` (per-node interrupt).
- Implementation: Each tool node has its own interrupt; same as
  ARFV1 — N interrupts for N `Ask` tool calls.
- Strengths: Same granularity.
- Weaknesses: Same.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Both could batch — collect all pending Ask calls
  into a single permission card with per-call checkboxes.

---

## 18. Cancel during interrupt wait

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:963` (cancel token
  forwarded); `engine.rs:1159-1166` (`wait_for_strategy(...,
  cancel)`).
- Implementation: The same `CancellationToken` driving the round also
  cancels the permission wait. On cancel, `wait_for_strategy` returns
  and the ReAct loop surfaces `RunError::Stopped`.
- Strengths: Uniform cancel path; no zombie waits.
- Weaknesses: The Engine still pushes a `tool_result` error sentinel
  (engine.rs:1058-1074) — model sees a "cancelled mid-execution" tool
  message.

### DeepAgents
- File(s): Implicit in LangGraph — `Command(resume=...)` resumes; no
  resume means the interrupt hangs.
- Implementation: No built-in cancel — apps must wrap their invoke in
  a cancellation primitive themselves.
- Strengths: Graph model is decoupled from any process.
- Weaknesses: Apps must add cancel; LangGraph does not.

### Gap Analysis
- Parity: ✅ (ARFV1 has the better primitive)
- Severity: 🟡 Useful
- Recommendation: None.

---

## 19. Handoff semantics — explicit suspend vs implicit interrupt_on

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:661-733`
  (`handoff_to_human`).
- Implementation: Explicit API: model asks a free-form question and the
  turn is suspended until reply or timeout. Distinct from
  `permission_request` (which gates tool calls).
- Strengths: Models the "I need human judgement" intent directly.
- Weaknesses: Caller (model or app) must invoke the method explicitly.

### DeepAgents
- File(s): Implicit — uses an `interrupt_on` rule on a synthetic
  "ask_user" tool.
- Implementation: The model emits a tool call; the middleware
  interrupts; the caller resumes with a Command carrying the answer.
- Strengths: Uniform with other tool calls.
- Weaknesses: No first-class concept — must invent the synthetic tool.

### Gap Analysis
- Parity: ✅ (ARFV1 more explicit; DeepAgents more uniform)
- Severity: 🟡 Useful
- Recommendation: None — ARFV1's explicit API is preferable for
  observability.

---

## 20. Permission record persistence

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1131-1174`
  (`request_permission`); `crates/arf-session/src/lib.rs:294-303`
  (`record_event` trait default).
- Implementation: Permission requests and responses are recorded as
  generic `OutboundSent` / `InboundReply` events in the unified event
  log (`session/src/lib.rs:809-836`) keyed by `correlation_id`. No
  first-class `permission_granted` row.
- Strengths: Audit trail exists via the event log.
- Weaknesses: Querying "what permissions did this session grant?" is
  a custom SQL join on `events`.

### DeepAgents
- File(s): Not implemented.
- Implementation: No separate permission history.
- Strengths: None.
- Weaknesses: No audit trail beyond graph checkpoints.

### Gap Analysis
- Parity: ✅ (ARFV1 has it; DeepAgents doesn't)
- Severity: 🟡 Useful
- Recommendation: Add a typed `Event::PermissionGranted { tool_name,
  args_hash, expires_at }` variant for faster querying.

---

## Summary table

| # | Capability | ARFV1 | DeepAgents | Parity |
|---|---|---|---|---|
| 1 | HITL mechanism | broadcast + wait | langgraph interrupt | ⚠️ |
| 2 | Permission enum | Allow/Ask/Deny | allow/deny/interrupt | ✅ |
| 3 | Interrupt mode | `Ask` | `mode="interrupt"` | ✅ |
| 4 | Per-tool config | static `ToolSpec.permission` | `interrupt_on` dict | ⚠️ |
| 5 | FS path-arg map | n/a | `_FS_TOOL_PATH_ARGS` | ❌ |
| 6 | Permission→interrupt synthesis | n/a | `_build_interrupt_on_from_permissions` | ❌ |
| 7 | Handoff API | `handoff_to_human` | implicit (interrupt_on tool) | ✅ |
| 8 | `HumanHandoff` wire | typed `ActionMessage` | n/a | ✅ |
| 9 | `HumanHandoffReply` wire | typed + LRU dedup | n/a | ✅ |
| 10 | `SessionStatus::Interrupted` | explicit enum | implicit in checkpoint | ⚠️ |
| 11 | Permission broadcast | bus `permission_request` | in-graph interrupt | ✅ |
| 12 | Timeout handling | cancel-token | caller-controlled | ⚠️ |
| 13 | Re-approval persistence | not stored | not stored | ✅ |
| 14 | Caller-supplied approver | not supported | `interrupt_on` callable | ❌ |
| 15 | Edit on approval | not supported | accept/edit/reject | ❌ |
| 16 | FS interrupt coverage | not bundled | per-FS-tool | ❌ |
| 17 | Multi-step approval | per-call | per-node | ✅ |
| 18 | Cancel during wait | cancel-token forwarded | caller-side | ✅ |
| 19 | Handoff semantics | explicit `handoff_to_human` | implicit tool | ✅ |
| 20 | Permission record persistence | event log only | none | ✅ |

**Net assessment.** ARFV1 owns the more explicit primitives
(`handoff_to_human`, typed `HumanHandoff*` wires, durable
`SessionStatus::Interrupted`, bus broadcast). DeepAgents owns the more
expressive policy layer (callable approver, edit-on-approval, FS-aware
synthesis). Closing the policy gap — items 4, 5, 6, 14, 15 — is the
next high-value task; the wire and durability gaps are already
ARFV1-positive.