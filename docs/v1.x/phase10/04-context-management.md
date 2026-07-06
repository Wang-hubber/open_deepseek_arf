# Context Management — ARFV1 vs DeepAgents

> Atomic-level comparison of conversation compaction, offload-to-file, overflow
> clip, trigger heuristics, and summarizer injection. ARFV1 side references
> `crates/arf-compactor/`; DeepAgents side references the `libs/deepagents`
> tree at `/tmp/deepagents`.

---

## 1. Summarization Strategy — split → summarize oldest → keep tail

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:99-156` (`Compactor::compact`), `:104` (skip), `:115-121` (split), `:133-142` (rebuild)
- Implementation: `split = messages_before - keep_tail`; the prefix `[..split]` is handed to the `Summarizer`, the suffix `[split..]` is preserved verbatim, and the new `state.messages` becomes `[summary_system, ...tail]`. No offload; the prefix is **discarded** after summarization.
- Strengths: deterministic, zero-ambiguity ordering; F-015 cleanly separates `instruction` from raw `messages` (`CompactionRequest`, `:58-63`).
- Weaknesses: prefix is **not persisted** anywhere — original transcripts are lost on compaction; loss of verbatim history is invisible to the agent at later turns.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:770-801` (`_build_new_messages_with_path`), `:1225-1298` (`_offload_to_backend`), `:770-801` (insert summary `HumanMessage`)
- Implementation: same split (LangChain's `LCSummarizationMiddleware._partition_messages`, `:661`); the prefix is **first appended to a per-thread markdown file on the backend** at `/conversation_history/{thread_id}.md` (`:736`), then summarized. The summary `HumanMessage` embeds the file path so the agent can `read_file` it back.
- Strengths: full recovery path; the suffix is preserved exactly as in ARFV1, but old content is never destroyed.
- Weaknesses: backend write is best-effort — a `None` from `_offload_to_backend` is logged but summary still proceeds (`:1466`).

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: introduce a `BackendProtocol` in ARFV1 (arfer has no equivalent abstraction today) and persist the compacted prefix as `{session_id}/compaction_history/{turn}.md` before discarding it. Reuse the existing `state_store` boundary once a backend trait is added.

---

## 2. Trigger Mechanism — utilization ratio vs fraction of model window

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:167-179` (`when_context_over`), `crates/arf-core/src/state.rs:31-35` (`context_utilization`)
- Implementation: `CheckpointRule::when_context_over(ratio, keep_tail)` fires when `state.over_view.context_utilization() >= ratio`, where utilization is `context_tokens / model_context_window` (`state.rs:35`).
- Strengths: threshold expressed as a single tunable ratio (e.g. 0.7); consumes the engine's own telemetry (`set_context_tokens`, `state.rs:77`; updated at `engine.rs:885-888`).
- Weaknesses: `model_context_window` must be populated **externally**; if the field is zero, `context_utilization()` returns 0.0 and the rule never fires (`state.rs:31-33`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:160-170` (`TriggerClause`), `:261-298` (`compute_summarization_defaults`), `_should_summarize` via `LCSummarizationMiddleware` (delegated at `:647-649`)
- Implementation: `trigger: ContextSize` accepts `("fraction", 0.85)`, `("tokens", N)`, or `("messages", N)`. Fraction is `total_tokens / model.profile["max_input_tokens"]` derived from the **model itself**, not the caller.
- Strengths: model-aware — fraction default `0.85` (`:281`); `tokens 170000` and `messages 6/20` fallback (`:289-298`) for profiles lacking `max_input_tokens`.
- Weaknesses: relies on LangChain profile metadata; defaults vary per model family.

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: extend ARFV1's `when_context_over` to read `model_context_window` from a future `ModelProfile` (or from ARF's `core_model_params`) rather than expecting an externally-written field; propagate fraction/tokens/messages tuple like DeepAgents.

---

## 3. Trigger Placement — BeforeModelCall vs `wrap_model_call`

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:170` (trigger set to `Checkpoint::BeforeModelCall`)
- Implementation: compact decision and `CompactRequest` are emitted inside the engine's pre-model hook; the App's Engine handler runs `Compactor::compact` before any LLM call.
- Strengths: in-band with engine lifecycle; no double-call path; works for both streaming and non-streaming.
- Weaknesses: the rule fires **once** — on `ContextOverflowError` there is no fallback retry because ARFV1 has no provider-overflow path.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:1377-1509` (`wrap_model_call` sync), `:1511-1644` (`awrap_model_call` async), `:1432-1436` (overflow fallback catch)
- Implementation: `wrap_model_call` first runs the model with truncated messages; on `ContextOverflowError` (`:1435`) it falls back to the summarization path and retries the model call once.
- Strengths: defensive double-attempt: proactive truncation + reactive fallback covers both threshold-based and provider-rejected overflow.
- Weaknesses: requires three separate passes (count → truncate → summarize) per `wrap_model_call`; the async path uses `asyncio.gather` for offload vs summary (`:1600`).

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: add a `RetryOnOverflow` checkpoint in ARFV1 engine (`engine.rs`) so that when `model_call` returns a `ContextOverflowError`-equivalent, the engine schedules a `CompactRequest` and retries the same `turn` once.

---

## 4. Offload Strategy — ToolMessage content too large

### ARFV1
- File(s): _none (feature missing)_
- Implementation: not implemented; ARFV1 has no concept of "tool output externalization" — every `ToolMessage` byte stays in the message list.
- Strengths: n/a
- Weaknesses: a single 30 MB tool result bloats `state.messages` and `context_tokens` until the next compaction; no per-TM relief.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_message_eviction.py:119-142` (`_offload_tool_message_content`), `:145-162` (`_aoffload_tool_message_content`), called from `_overflow_clip.py:162-173` (`_clip_overflow_tail`)
- Implementation: oversized `ToolMessage.content` is written to `large_tool_results/{tool_call_id}` on the backend; the original message is replaced with a `TOO_LARGE_TOOL_MSG` stub carrying a head/tail preview (`:25-34`).
- Strengths: per-TM relief without forcing a full conversation compaction; preserves `tool_call_id` so the agent can `read_file` it back.
- Weaknesses: requires `BackendProtocol`; not engaged proactively — only fires in the `ContextOverflowError` fallback path (`summarization.py:1449-1458`, `:1583-1592`) or via `FilesystemMiddleware` if a tool exceeds its per-call size threshold.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: add a `tool_output_externalization` step inside ARFV1's `ModelResponse` handling — when a `ToolMessage.content` exceeds e.g. 8 KB, write it to `state_store` under `sessions/{sid}/large_tool_results/{tcid}` and rewrite `state.messages` to a stub. DeepAgents' `_offload_tool_message_content` is a clean template to port.

---

## 5. Overflow Clip — sync/async clip when message list exceeds window

### ARFV1
- File(s): _none (feature missing)_
- Implementation: not implemented.
- Strengths: n/a
- Weaknesses: ARFV1's only relief valve is full compaction.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/_overflow_clip.py:131-173` (`_clip_overflow_tail`), `:176-206` (`_aclip_overflow_tail`); `:36-50` (`_derive_overflow_clip_threshold_tokens`); `:105-115` (`_clip_one_tail_message`)
- Implementation: after the `ContextOverflowError` branch, finds the trailing `ToolMessage` batch, sums its tokens, and — if it exceeds a threshold derived from `keep` (`:36-50`) — offloads each one concurrently via `asyncio.gather` (`:193`). `read_file` results use a head-slice + path-pointer note instead of a backend write (`:76-93`).
- Strengths: targeted at the actual bloat source (large tail `ToolMessage`s); preserves original `id` so the `add_messages` reducer overwrites in place.
- Weaknesses: requires a tool-call index (`:64-73`) and a `BackendProtocol`.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: once a backend trait lands in ARFV1, port `_overflow_clip.py` verbatim: it's pure data-shuffling and has no LangChain dependency beyond the `ToolMessage` shape. Wire it into the engine's `AfterTool` checkpoint.

---

## 6. Summarization Middleware Variants — 4 factories

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:77-93` (`Compactor::new`, `Compactor::with_instruction`)
- Implementation: a single `Compactor` + a single `Summarizer` trait; no factory variants, no tool-mode, no auto-mode distinction.
- Strengths: minimal surface; one way to do it.
- Weaknesses: no equivalent of `SummarizationToolMiddleware` — the system has no "let the agent compact itself" mode.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:499` (`_DeepAgentsSummarizationMiddleware`), `:1822` (`SummarizationToolMiddleware`), `:1647` (`SummarizationMiddleware` alias), `:1654` (`create_summarization_middleware`), `:1731` (`create_summarization_tool_middleware`)
- Implementation: 4 variants; two classes (`_lc_helper`-wrapped auto-mode + tool-mode) and two model-aware factories. The factories consume `compute_summarization_defaults(model)` (`:261-298`).
- Strengths: clear separation of concerns; factories resolve model profiles once at construction.
- Weaknesses: 4 entry points complicate the API surface; `SummarizationToolMiddleware` requires a parent `SummarizationMiddleware`.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: in Phase 11, split `Compactor` into a `CompactorEngine` (does the work) and a `CompactTool` (`ModelTool` whose execution calls the engine), so apps can register either or both. Mirror the factory pattern.

---

## 7. User-Invoked Compaction — `compact_conversation` tool

### ARFV1
- File(s): _none (feature missing)_
- Implementation: no user/LLM-triggered compaction path; the only path is the `CheckpointRule` in the engine.
- Strengths: fewer attack surface; simpler bus-only trigger.
- Weaknesses: long sessions can't pivot when context is no longer relevant; no way for the agent to decide "we're moving on".

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:1900-1928` (`_create_compact_tool`), `:2078-2110` (`_run_compact`), `:2058-2076` (`_is_eligible_for_compaction`)
- Implementation: a `compact_conversation` `StructuredTool` (no args; schema = `CompactConversationSchema` at `:133`) is registered with the agent. The system-prompt nudge (`SUMMARIZATION_SYSTEM_PROMPT`, `:137-144`) describes when to use it. Eligibility gate at 50% of the auto-trigger (`:2026-2029`, `:2040-2056`).
- Strengths: model can self-recover context on demand; eligibility gate prevents premature compaction; error path returns a `ToolMessage` instead of raising (`:2000-2024`).
- Weaknesses: depends on the model noticing the nudge; in benchmarks the model often ignores the hint.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: add a `compact_conversation` `ModelTool` in `crates/arf-tools/` that broadcasts a `CompactRequest{ratio=0.5, keep_tail=4}` `ActionMessage` through the existing bus. Reuse `Compactor::compact` as the executor; emit `CompactDone` on completion.

---

## 8. Summarizer Trait Injection — `Arc<dyn Summarizer>`

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:67-71` (`Summarizer` trait), `:78` (`Arc<dyn Summarizer>` field), `:125-131` (call site)
- Implementation: `#[async_trait] Summarizer: Send + Sync` with a single `async fn summarize(&self, req: CompactionRequest<'_>) -> Result<String, CompactError>`. The `Compactor` owns `Arc<dyn Summarizer>`, injected at construction; tests substitute `RecordingSummarizer` (`:252-268`) and `ConcatenateSummarizer` (`:234-249`).
- Strengths: clean DI seam; trait-object indirection; trivially mockable.
- Weaknesses: the default summarizer is a no-op in tests — apps must supply a real one (typically the App's bus-driven `ModelCall`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:592-600` (delegation to `LCSummarizationMiddleware`), `:663-669` (sync/async `_create_summary` / `_acreate_summary`)
- Implementation: summarizer is **not** an injection point; the middleware **owns** the `BaseChatModel` and calls `model.invoke` directly. There is no `Summarizer` trait.
- Strengths: tighter coupling — uses LangChain's model interface directly.
- Weaknesses: cannot plug a custom summary strategy without subclassing the whole middleware.

### Gap Analysis
- Parity: ✅ (ARFV1 has trait; DeepAgents does not — ARFV1 is **ahead** on injection)
- Severity: 🟢 none
- Recommendation: keep the trait; do not collapse it into a model-coupled struct like DeepAgents. Optionally add a constructor helper `Compactor::with_chat_model(Arc<dyn ModelCall>)` for the common case.

---

## 9. Default Summarization Instruction — preserved as constant

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:74`
- Implementation: `pub const DEFAULT_INSTRUCTION: &str = "You are a conversation summarizer. Produce a concise but information-dense summary that preserves task context, decisions made, files involved, and next steps..."`.
- Strengths: `const`, compile-time verified, no runtime allocation; overridable via `with_instruction` (`:90-93`).
- Weaknesses: short; lacks explicit anchors (no concrete output-format spec beyond "summary").

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:113-117` (`DEEPAGENTS_DEFAULT_SUMMARY_PROMPT`)
- Implementation: spliced from LangChain's `DEFAULT_SUMMARY_PROMPT` with an added `<media_reference_information>` block that explains XML media tags (`:100-107`).
- Strengths: extends the upstream prompt with concrete media-handling instructions; preserves a load-bearing `<messages>` marker from the parent prompt.
- Weaknesses: depends on the upstream template's structure; if LangChain renames the marker, deepagents breaks silently.

### Gap Analysis
- Parity: ✅
- Severity: 🟢 none
- Recommendation: extend ARFV1's `DEFAULT_INSTRUCTION` with a short reference to the bus-marker format (`[COMPACTED SUMMARY]`) and the recovery contract (`read_file` of the offload). Otherwise parity holds.

---

## 10. Heuristic for `context_tokens` — 0.15 multiplier vs model-derived

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:144-146`
- Implementation: `let after_tokens = ((before_tokens as f64) * 0.15) as usize;` — assumes the summary is roughly 15% of the original conversation in tokens; updates `state.over_view.context_tokens` accordingly.
- Strengths: cheap, no extra LLM call; meets the next compaction threshold only after significant new content.
- Weaknesses: 15% is a guess; under-estimates a verbose summary (e.g., code-heavy sessions) and may *over*-trigger the next `when_context_over`; over-estimates a tight summary and wastes the window.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:993-1031` (`_count_tokens`), re-counted after compaction via `wrap_model_call`
- Implementation: token count is **derived**, not estimated — `_count_tokens` runs over the post-summary message list (using `count_tokens_approximately` plus the counter's `tools=` acceptance probe at `:1017-1031`).
- Strengths: no guess; the next threshold check is exact modulo the counter's accuracy.
- Weaknesses: an extra `token_counter` invocation per `wrap_model_call`; needs introspection on C-level counters.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: replace the 0.15 heuristic with `state.set_context_tokens(token_estimate(&state.messages))` post-compaction, using the same tiktoken-ish estimator already used elsewhere in the engine (e.g., arf-core's `count_tokens_approximately` analogue).

---

## 11. CompactRequest / CompactDone as ActionMessage — bus-coordinated compaction

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:185-204` (`CompactRequest` impl `ActionMessage`), `:207-225` (`CompactDone` impl `ActionMessage`)
- Implementation: both types implement `arf_core::ActionMessage` with `msg_type "compact_request"` / `"compact_done"` and `MessageIntent::Command`. They traverse the actor bus, allowing broadcast and trace logging.
- Strengths: bus-coordinated; traceable; consistent with all other engine events (Phase 6 "broadcast over point-to-point").
- Weaknesses: the `correlation_id()` is `Uuid::new_v4()` per emission — lossy w.r.t. the originating checkpoint; improvements should thread a `rule_id` or `checkpoint_id`.

### DeepAgents
- File(s): _none — pure in-process middleware, no bus abstraction_
- Implementation: summary events live in private state (`_summarization_event`, `summarization.py:209`); no broadcast, no cross-actor visibility.
- Strengths: simple, no bus required.
- Weaknesses: cannot be observed by trace, eval, or sibling actors.

### Gap Analysis
- Parity: ✅ (ARFV1 is **ahead**)
- Severity: 🟢 none
- Recommendation: keep the bus path; carry `checkpoint_id` through `correlation_id` so eval and trace can correlate compaction to the rule instance that triggered it.

---

## 12. Backend Dependency — summarization middleware reads from backend

### ARFV1
- File(s): _none (no backend abstraction)_
- Implementation: ARFV1 has no `BackendProtocol`; `Compactor` only mutates `state`.
- Strengths: zero coupling to a filesystem abstraction.
- Weaknesses: every feature that DeepAgents gets for free (offload, large-TM eviction, media rewriting, recovery via `read_file`) is a from-scratch implementation in ARFV1.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:609-617` (`_backend`, `_history_path_prefix`), `:671-700` (`_get_backend`), `:1225-1375` (sync/async `_offload_to_backend`)
- Implementation: every summarization path requires a `BACKEND_TYPES` (Protocol instance or factory); offload resolves a `BackendProtocol` via runtime, then writes/reads MD files at configured prefixes.
- Strengths: every compaction artifact is recoverable; the agent can `read_file` its own history.
- Weaknesses: layering complexity — `_get_backend` handles runtime resolution (`:686-700`) and falls back to `artifacts_root = "/"` (`:611-613`).

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🔴 Critical
- Recommendation: introduce `arf_backend::BackendProtocol` (async `awrite`, `aread`, `aedit`, `aupload_files`) with at least two impls: `StateBackend` (in-process for tests) and `LocalFsBackend` (Path-based for apps). This is a precondition for items 4, 5, 7, and 15.

---

## 13. Two-Trigger Coexistence — auto vs on-demand

### ARFV1
- File(s): `crates/arf-compactor/src/lib.rs:167-179` (auto only)
- Implementation: `when_context_over` is the **only** trigger; no `compact_conversation` tool, no manual override.
- Strengths: predictable; one path to maintain.
- Weaknesses: the agent cannot ask to compact; failure modes (provider rejects at 100%) have no fallback.

### DeepAgents
- File(s): `summarization.py:1377-1509` (auto path in `wrap_model_call`), `:1900-1928` (`compact_conversation` tool), `:1837` (shared `_summarization_event`)
- Implementation: `SummarizationMiddleware` triggers auto; `SummarizationToolMiddleware` exposes the manual tool. Both middleware layers share the **same** `_summarization_event` state key (`:1837`), so manual compaction "takes over" future summarizations cleanly.
- Strengths: interoperable; auto-trigger respects a prior manual summary's `cutoff_index` via `_compute_state_cutoff` (`:858-884`).
- Weaknesses: two middlewares must both be registered correctly; if only the tool layer exists, no auto-compress happens.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: after Item 7, the auto + tool pair can coexist by sharing `_summarization_event` equivalent in `state.over_view`. ARFV1 already routes through the bus, so the natural unification is a `state.over_view.last_compact_event: Option<CompactDone>` field consulted by both the rule and the tool.

---

## 14. Per-Model Defaults — `compute_summarization_defaults(model)`

### ARFV1
- File(s): _none (no per-model defaults)_
- Implementation: callers wire `when_context_over(0.85, 20)` themselves; no built-in profile lookup.
- Strengths: simple.
- Weaknesses: each app reinvents the threshold; different model context windows require different magic numbers.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/summarization.py:261-298` (`compute_summarization_defaults`)
- Implementation: reads `model.profile["max_input_tokens"]`; if present, returns `trigger=("fraction", 0.85)`, `keep=("fraction", 0.10)`, and a similar `truncate_args_settings` shape. Without a profile, falls back to `("tokens", 170000)` / `("messages", 6)`.
- Strengths: plug-and-play; one line of code picks the right thresholds per model.
- Weaknesses: when the LangChain profile lies or is stale, defaults lie too.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: add `compute_compaction_defaults(model: &ModelProfile) -> (f64, usize)` in `arf-core` so apps stop hand-picking thresholds. Source from the same `model_context_window` already populated in `OverView` (`state.rs:22`).

---

## 15. Subagent Summarization Inheritance

### ARFV1
- File(s): _none (subagent compaction not yet wired)_
- Implementation: each `subagent` operates on its own `Engine`, but the engine doesn't currently ship a default `when_context_over` rule; compaction is opt-in per engine.
- Strengths: each engine decides its own policy.
- Weaknesses: no auto-default; subagents inherit nothing automatically.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:738-747` (subagent middleware stack), `:823-833` (default general-purpose subagent), `:745` & `:831` (`create_summarization_middleware(subagent_model, backend)`)
- Implementation: every declarative `SubAgent` gets `create_summarization_middleware(subagent_model, backend)` **injected** into its middleware list (`:745`), and the auto-created general-purpose subagent gets the same at `:831`. Each subagent resolves its **own** model and gets its **own** thresholds via `compute_summarization_defaults`.
- Strengths: zero-config inheritance; subagents get context protection automatically; the parent is unaware of subagent thresholds.
- Weaknesses: more memory per subagent; if 10 subagents are spawned, 10 summarizers exist.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: in `crates/arf-subagent-pool`, default-construct each subagent's `Engine` with `Compactor::new(default_summarizer) + when_context_over(compute_compaction_defaults(model))` so subagents inherit context protection for free. Plumb through `SubagentPoolSpec`'s `compaction` field.

---

## Cross-cutting Recommendation

Add `crates/arf-backend/` first (parallel to `[Pre: ARF-12]` in Phase 10). With a `BackendProtocol`, four of the largest gaps (#4 offload, #5 overflow clip, #7 user-invoked, #12 backend dep) collapse into ports of the DeepAgents modules identified above. Item #10's heuristic fix is independent and trivial; do it alongside. Item #13 needs the bus-field unification; track via `state.over_view.last_compact_event: Option<CompactDone>`.
