# Phase 10 — Skills & Memory: ARFV1 vs DeepAgents

> Atomic-level comparison of skill definitions, skill injection, persistent memory, and prompt-cache alignment. ARFV1 is a Rust/PyO3 framework with bus-actor Engine; DeepAgents is a Python LangGraph middleware framework. The two diverge most sharply here — DeepAgents treats skills and memory as first-class file-backed middleware with Anthropic-spec YAML frontmatter and ephemeral cache breakpoints; ARFV1 treats them as static, always-loaded strings appended to the system prefix.

---

## Skills Definition Format

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:93-95`, `crates/arf-mcp/src/registry.rs:13-30,222-227`
- Implementation: ARFV1 declares skills *indirectly* via MCP node capabilities (`capabilities["skills"]: ["name1","name2"]` resolved at build time). `ResourceRegistry::skills_text()` (`registry.rs:169-210`) walks the live BusGraph, filters skills via `DeclaredFilter::accepts()`, then returns a flat string `Available skills:\n- skill1 (from mcp/x)\n- skill2 (from mcp/y)`. There is no skill file format definition in v1 — skills are exposed by MCP providers as opaque capability strings; the `SkillIndex` scanner in `crates/arf-mcp/src/registry.rs:60-122` *does* support `<root>/skills/<name>/SKILL.md` with YAML frontmatter (`SkillFrontmatter { name, description, compatibility }`) but does not feed it back into model_call — it only enables tool/resource loading from those directories.
- Strengths: Dynamic, runtime-resolved (live BusGraph); hash-cached on `(node_id, skill_name)` pairs; zero-cost in production when skills don't change.
- Weaknesses: Skills-as-strings, not skills-as-bundles. No description frontmatter travels with the skill at the model-call layer. No body content is ever injected — only names. ARFV1's template `{{skills}}` placeholder (`crates/arf-engine/src/tests.rs:219`) was the old design and the runtime path now uses `skills_text()` directly (`engine.rs:818`).

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/skills.py:366-454` (`_parse_skill_metadata`); `:786-832` (`SkillsMiddleware.__init__`)
- Implementation: Definitively specifies the Agent Skills format (`https://agentskills.io/specification`). Each `SKILL.md` has YAML frontmatter delimited by `^---\n...\n---\n` (`skills.py:390`) parsed via `yaml.safe_load` (`:401`). Required fields: `name` (kebab-case, must match parent directory — `:339-350`) and `description`. Optional: `allowed-tools`, `metadata`, `license`, `compatibility`. `SkillMetadata` is a `TypedDict` (`:232-280`). `MAX_SKILL_FILE_SIZE` and `MAX_SKILL_DESCRIPTION_LENGTH` constants cap file/description size.
- Strengths: Open spec (AgentSkills.io) — interop with Claude Skills, Cursor, Codex. Frontmatter carries discoverability metadata (`description`, `allowed-tools`) the model can use for relevance matching. Per-file validation with clear warnings.
- Weaknesses: Skill directory layout is rigid (`<source>/<skill-name>/SKILL.md`); cannot pack multiple skills per file.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: ARFV1's `SkillIndex` already parses YAML frontmatter in `crates/arf-mcp/src/registry.rs:222-227`. Promote it to the engine layer: have `ResourceRegistry::skills_text` enumerate `SkillIndex` frontmatter (name + description) and emit the Anthropic-style `- **{name}**: {description}` list. This gives ARFV1 the description-back-to-model feature without inventing a new file format.

---

## Skill On-Demand Loading

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:818-828`, `crates/arf-engine/src/registry.rs:169-210`
- Implementation: Every model turn unconditionally pushes the full skills list into the system prefix. `do_model_turn` builds `messages = [system(template), ...initial_memory, system(skills_text), ...state.messages]` and `skills_text()` returns *all* declared skills (`:202-207`). There is no relevance matching, no on-demand expansion, no lazy body load — the model only sees names + (node_id) provenance.
- Strengths: Predictable token cost; trivial reasoning.
- Weaknesses: Cannot surface skill *bodies* (only names); no way to inject the full instructions of a skill conditionally; model must `read_file` (when allowed) to get instructions, but the engine system prefix is always bloated by the full skill list.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/skills.py:913-939` (`modify_request`); `:941-985` (`before_agent`); `:1033-1048` (`wrap_model_call`)
- Implementation: `before_agent` (`:941`) loads skill metadata **once per session** into `state["skills_metadata"]` (a `TypedDict[SkillMetadata]`). `wrap_model_call` (`:1033`) injects only the `name + description + path` triple, not bodies: `Read \`{skill["path"]}\` for full instructions` (`:889`). The model reads the body itself (via backend `read_file`/`cat`) when it judges the description relevant.
- Strengths: Prompts the model to discover skills via description matching; bodies loaded on-demand keep the prefix small; cached for session lifetime (`:960`: `if "skills_metadata" in state: return None`).
- Weaknesses: Requires agent to have `read_file` capability, plus the backend must serve the path — coupling between middleware and filesystem tool.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Move skill listing from `ResourceRegistry::skills_text()` to a new `SkillsMiddleware` (Rust actor + Python binding) emitting Anthropic-style metadata. Implement `before_agent` to populate from `SkillIndex` frontmatter; implement `wrap_model_call` to inject the metadata fragment. Add a `skill_read` tool so the model can fetch body lazily.

---

## Skill Description Frontmatter for Relevance Matching

### ARFV1
- File(s): `crates/arf-mcp/src/registry.rs:13-19,222-227`
- Implementation: `SkillFrontmatter { name, description, compatibility }` is parsed during `SkillIndex::scan` (`:90-99`) but only `name` is exposed via `SkillEntry` capability advertisement, and only `name` (not `description`) reaches `ResourceRegistry::skills_text`. Description is currently dead data from the engine's perspective.
- Strengths: Frontmatter parsing infrastructure exists.
- Weaknesses: Description never reaches the model — relevance matching is impossible. `compatibility` is also unused.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/skills.py:411,427-433,874-891`
- Implementation: `_parse_skill_metadata` (`:410-414`) requires `description`, truncates to `MAX_SKILL_DESCRIPTION_LENGTH`, and `_format_skills_list` (`:874`) renders `- **{skill['name']}**: {skill['description']}` plus optional annotations and `allowed-tools`. This is the payload the model uses to decide which skill to read.
- Strengths: First-class relevance matching via natural-language description; explicit per-skill tool allow-listing.
- Weaknesses: Trust boundary: skill descriptions are file-content presented to the model — mitigated via `<skill_load_warnings>` (`:893-911`) that flags loading errors as untrusted diagnostics.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Pipe `description` through `SkillEntry → BusGraph node capabilities["skills"]` JSON, then render in `ResourceRegistry::skills_text` with the `- **{name}**: {description}` shape. Sanity-cap description length (mirror `MAX_SKILL_DESCRIPTION_LENGTH`).

---

## Skills Filesystem Convention

### ARFV1
- File(s): `crates/arf-mcp/src/registry.rs:69-122,138-145,147-179,300-327`
- Implementation: ARFV1 *does* enforce `<root>/skills/<name>/SKILL.md` via `SkillIndex::scan` (`:69-122`). It also supports subdirectories `tools/`, `references/`, `assets/` per skill with safe-path resolution (`resolve_safe_path`, `:300-327`: rejects `..`, absolute paths, and forces a `tools/|references/|assets/` prefix). Skill body and resource files are *read-on-demand* via `load_body` (`:132-136`), `load_resources` (`:138-145`), `load_resource_file` (`:147-179`). However the engine does **not** see these — they exist purely as the MCP node's tool/resource catalogue.
- Strengths: Strong security guardrails (path traversal blocked, prefix-allowlist). Subdirectory split between tools/references/assets — clean separation.
- Weaknesses: Skill format is implementation-detail of MCP, not first-class on the bus. Body content invisible to engine.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/skills.py:606-630` (`_list_skills_with_errors`)
- Implementation: Same convention `<source>/<skill-name>/SKILL.md` (`:649-651` comment block). Backend abstracts where the files live — filesystem, LangSmith Hub, or a Store (`:638-696`). Supports multiple source paths each with a human-readable label (`:830-832`). No subdirectory conventions like ARFV1's tools/references/assets.
- Strengths: Backend-pluggable (State, Filesystem, ContextHub, Store); multi-source with priority ordering (last source wins, `:949`).
- Weaknesses: All sub-files of a skill are read the same way (`SKILL.md` is the only metadata file) — no distinction between tools and reference docs.

### Gap Analysis
- Parity: ✅ (filesystem convention matches at the surface level)
- Severity: 🟡 Useful
- Recommendation: Make the ARFV1 layout (with `tools/`, `references/`, `assets/`) an installable *BackendProtocol* for DeepAgents compat. Expose skill body via the same `skill_read` tool.

---

## AGENTS.md Memory Loading

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:93-95`, `crates/arf-engine/src/engine.rs:53,822-825`
- Implementation: ARFV1 has no `AGENTS.md` equivalent. `initial_memory: Vec<String>` is a *static* `Vec<String>` set at config build time. Each entry is pushed as its own `system` message after the template (`:822-825`). There is no on-disk file backing, no filesystem load, no per-user namespace.
- Strengths: Zero runtime I/O; reproducible; serialized into `AgentConfig`.
- Weaknesses: Requires restart to change memory; no cross-session persistence; no per-user isolation; no edit/inject loop.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/memory.py:303-369` (`before_agent`, `abefore_agent`)
- Implementation: `before_agent` (`:303-335`) calls `backend.download_files(list(self.sources))` once per session, skipping if `memory_contents` already in state (`:318`). Each source (default: `AGENTS.md` paths) becomes an entry in `MemoryState.memory_contents: dict[str, str]` (`:96`). Sources can be multiple (per-user, per-project). `edit_file` tool is the standard write path.
- Strengths: Filesystem-backed; survives restarts; multi-source merge with deduplication; pluggable backend.
- Weaknesses: First-turn latency from disk read (though cached for session, `:317`).

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🔴 Critical
- Recommendation: Introduce a `MemoryMiddleware` actor that reads `<agent_root>/AGENTS.md` (and optionally `<agent_root>/memory/<topic>.md`) on session start. Wire into `do_model_turn` after template + before skills. Add `memory_write`/`memory_edit` tools gated on MCP `Ask` permission (per the Doc-before-code workflow).

---

## Memory Injection Point

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:813-829`
- Implementation: Memory is injected as **separate system messages** (`messages.push(ModelMessage::new("system", m))`) immediately after `system_prompt_template` and before `skills_text` (`:823-828`). Order: `[system template][system memory_0][system memory_1]...[system skills]...[user,assistant,...]`. Each memory entry occupies its own message slot, so list ordering is preserved.
- Strengths: Each memory entry is independently inspectable in the trace; order is stable across turns.
- Weaknesses: Bloats the message list (each entry = +1 message, +1 model_tokens header). No `cache_control` marker, so providers that support prompt-cache prefixes cannot pin the memory block.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/memory.py:371-407` (`modify_request`)
- Implementation: Uses `append_to_system_message` helper (`:83` import, `:385`) to concatenate the formatted `<agent_memory>` fragment onto the existing **single** system message. Order: existing template + memory fragment + (later: skills fragment appended by SkillsMiddleware). Single system message, multi-fragment.
- Strengths: One system message → better prompt-cache hit rate when memory is stable. Skills and memory are sandwiched predictably.
- Weaknesses: Fragment collisions can occur if multiple middleware append to system message without ordering — DeepAgents controls this by enforcing a fixed middleware tail order in `graph.py:889-945`.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: Replace per-entry `system` messages with a single `system` block assembled at engine layer: `template + "\n\n" + memory + "\n\n" + skills`. Test backward-compat with existing traces that count messages.

---

## Memory Backend Abstraction

### ARFV1
- File(s): N/A — no memory backend abstraction exists
- Implementation: Memory is a literal `Vec<String>` field on `AgentConfig`. There is no interface, no factory, no protocol. `MemoryOp` / `MemoryOpResult` messages exist (`crates/arf-core/src/message.rs:392-454`) as wire types for a *future* engine-as-actor memory CRUD design (engine → memory node), but no actor implements it in v1.
- Strengths: N/A
- Weaknesses: No way to swap filesystems (Local, S3, LangSmith Hub). No way to test memory logic without a real disk.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/protocol.py` (`BackendProtocol`), `context_hub.py:47`, `store.py:173`
- Implementation: `BackendProtocol` is a structural-typing Protocol with `download_files`, `upload_files`, `ls`, `read` etc. Four implementations: `StateBackend` (in-memory), `FilesystemBackend` (local disk), `ContextHubBackend` (`backends/context_hub.py:1,47` — LangSmith Hub), `StoreBackend` (`backends/store.py:173` — LangGraph Store). MemoryMiddleware accepts any `BACKEND_TYPES` (`:193`).
- Strengths: Production-grade abstraction; per-session/per-user selection; backend factory via `NamespaceFactory` (`store.py:125`).
- Weaknesses: None significant.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🔴 Critical
- Recommendation: Define `MemoryBackend` Trait in `arf-core` with `read`, `read_many`, `write`, `edit`, `list`. Ship two impls: `LocalDiskBackend` (1:1 with ARFV1's allowed_paths), `BusBackend` (wraps a memory MCP node via the existing `MemoryOp` wire). Plumb through `EngineConfig`.

---

## Cross-Thread Memory Persistence

### ARFV1
- File(s): `crates/arf-session/` (session store) — references memory *trace* persistence, not memory *content* persistence
- Implementation: ARFV1 persists `state_store` snapshots per session (Phase 8 F5, `crates/arf-engine/src/engine.rs:59-66,205-217`). This captures the conversation but not user-editable memory. To change memory content: edit `AgentConfig.initial_memory`, rebuild, redeploy.
- Strengths: Session-scoped state is durable across process restarts.
- Weaknesses: Memory is config-bound, not data-bound. No cross-session accumulation unless the app reads `session_store` on next run and rebuilds `initial_memory` from it (currently no API).

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/context_hub.py:47` (`ContextHubBackend`); `store.py:173` (`StoreBackend`)
- Implementation: Two persistent backends: `ContextHubBackend` (Hub agent repo — shareable across users via LangSmith Hub URL) and `StoreBackend` (LangGraph `BaseStore` — cross-thread, cross-process, namespaced via `NamespaceFactory`). Both survive restarts and threads transparently.
- Strengths: Memory is data, not config — updateable per-turn via `edit_file` or `store.put()`. Cross-thread by default.
- Weaknesses: Requires LangSmith/Store infra; not pure local.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🔴 Critical
- Recommendation: Reuse existing `session_store` for memory persistence. Add `MemoryBackend::write(path, content)` and a `BusMemoryBackend` that round-trips via `MemoryOp`. Surface via Python binding `BaseAgent.memory.append(...)`.

---

## Per-User Memory Isolation

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:95` (`initial_memory: Vec<String>`)
- Implementation: Memory is a process-global config field. Two agents running in the same process with `initial_memory` differing require two `Engine` instances with different `AgentConfig` (works, but the memory cannot *evolve per-user* without restart).
- Strengths: Simple.
- Weaknesses: No user identity in memory lifecycle. Multi-tenant deployments must manually fork.

### DeepAgents
- File(s): `libs/deepagents/deepagents/backends/store.py:125` (`NamespaceFactory = Callable[[Runtime[Any]], tuple[str, ...]]`), `:133` (`_validate_namespace`)
- Implementation: `StoreBackend` takes a `namespace: NamespaceFactory | None` parameter (`:187`). The factory receives the runtime and returns the namespace tuple (e.g., `("user", user_id, "agent", agent_name)`), used as the prefix on every key in `store.put/get`. Different users → different namespaces → no collision.
- Strengths: First-class per-user isolation; backend-agnostic (works for Store, Hub, even State).
- Weaknesses: Naming the namespace is the developer's responsibility — typo'd factories leak data.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Add `MemoryBackend::for_session(namespace)` factory and an `AgentContext.user_id` slot (already on the bus actor — `crates/arf-core/src/node.rs`). The `BusMemoryBackend` should derive its key from `(user_id, session_id, path)`.

---

## Memory Cache-Control Breakpoints (Prompt Caching)

### ARFV1
- File(s): Not implemented
- Implementation: ARFV1 ships no `cache_control` markers on messages. The `model_call` payload is plain string messages; the adapter (`crates/arf-model/src/lib.rs`, not inspected here) forwards them verbatim. Providers that support Anthropic-style ephemeral cache breakpoints (Anthropic, DeepSeek) do not get any hints from ARFV1's wiring.
- Strengths: N/A
- Weaknesses: Repeated identical system prefixes cost full input tokens on every model call. For an agent that loops many turns with static memory/skills, this is a 2-5× cost multiplier.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/memory.py:391-403` (`add_cache_control`), `:188-197` (`MemoryMiddleware.__init__`)
- Implementation: When `add_cache_control=True`, `modify_request` (`:391-403`) appends `cache_control: {"type": "ephemeral"}` to the *last* content block of the system message. Detection uses `isinstance(request.model, ChatAnthropic)` (`:393`) — only Anthropic model gets the breakpoint, others get the un-marked fragment. Runtime check uses `request.model` not an init-time flag so middleware-level model overrides work (`:387-390`). The breakpoint always runs (even with `system_prompt=None`) so users who suppress the fragment still get the cache benefit (`:389-390`).
- Strengths: Provider-aware, runtime-aware, opt-in. Falls through cleanly when `add_cache_control=False`.
- Weaknesses: Currently only `ChatAnthropic` explicitly handled; other Anthropic-compatible providers (MiniMax, DeepSeek) require extending the isinstance check. Placed on the *last* block — Anthropic supports up to 4 breakpoints per request.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important (cost-optimization, not functionality)
- Recommendation: Add `cache_control: Option<CacheControlConfig>` to `ModelCall` (one field). When set, the adapter emits the `cache_control` block on the last system message. Mirror the provider-isinstance branching: Anthropic / DeepSeek / MiniMax emit `{"type": "ephemeral"}`; OpenAI emits nothing (uses its own implicit caching).

---

## MemoryState (Typed State for Memory Middleware)

### ARFV1
- File(s): `crates/arf-core/src/state.rs` (AgentState)
- Implementation: ARFV1's `State` (defined in `crates/arf-core/src/state.rs`) is an unstructured bag of `messages`, `over_view`, and arbitrary metadata. There is no separate `MemoryState` typed wrapper. Memory contents, if any, would live in `initial_memory` (immutable string vec) or be passed through `state` ad-hoc.
- Strengths: Flexible.
- Weaknesses: No type-safety on memory fields. App code can collide on key names.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/memory.py:88-103` (`MemoryState`, `MemoryStateUpdate`)
- Implementation: `MemoryState` extends `AgentState` adding `memory_contents: NotRequired[Annotated[dict[str, str], PrivateStateAttr]]` (`:96`). `PrivateStateAttr` excludes it from the final agent state. Updates are a separate `TypedDict` (`MemoryStateUpdate` — `:99-102`) returned by `before_agent` (`:303`).
- Strengths: Type-safe; private to middleware; LangGraph reducer-compatible.
- Weaknesses: Private-state machinery is LangGraph-specific — not directly portable to ARFV1's bus model.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Reuse ARFV1's existing `MemoryOp`/`MemoryOpResult` wire types (`crates/arf-core/src/message.rs:392-484`) instead of inventing a parallel state type. Memory contents flow as messages between Engine and a Memory actor — no state mutation needed.

---

## Skill vs Memory Distinction

### ARFV1
- File(s): `crates/arf-engine/src/registry.rs:169-210`, `crates/arf-engine/src/config.rs:93-95`
- Implementation: Skills and memory share no conceptual type. Skills = capability-advertised names via `ResourceRegistry::skills_text`; Memory = string entries in `initial_memory`. They land in different system-message slots (skills after memory, `engine.rs:822-828`) but are otherwise indistinguishable in behaviour.
- Strengths: Simple to reason about.
- Weaknesses: Conflates two different lifecycles: skills are *statically declared at build time*; memory is *session-stable but conceptually mutable*. Both immutable in v1, but skill-vs-memory should differ once the memory backend lands.

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/skills.py:784,941`, `libs/deepagents/deepagents/middleware/memory.py:188,303`
- Implementation: Two separate middlewares with two separate state schemas (`SkillsState` `skills.py:784`, `MemoryState` `memory.py:88`). Skills metadata loaded once via `before_agent` into `state["skills_metadata"]`; memory loaded once via `before_agent` into `state["memory_contents"]`. Skills paths are typically `skills/`; memory paths are typically `AGENTS.md`. Lifecycles differ: skills are read-only (re-read only on session restart); memory is mutable via `edit_file`.
- Strengths: Conceptual clarity. Skills = static capabilities; Memory = mutable user context.
- Weaknesses: Two middleware to maintain.

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: Document the skill/memory distinction in the engine config schema. Currently ARFV1 risks accidental conflation when devs see both as "strings injected into system prompt".

---

## Skills as Folders (Shareable Agent Bundles)

### ARFV1
- File(s): N/A — no concept of a portable agent bundle
- Implementation: There is no ARFV1 analog to a `downloading_agents/` example. To share an agent, the consumer needs the full workspace tree (`Cargo.toml`, `crates/arf-engine/...`, `agent.toml`) — way too heavy.
- Strengths: N/A
- Weaknesses: Low shareability ceiling.

### DeepAgents
- File(s): `libs/deepagents/examples/downloading_agents/` (per the user's reference); skills are stored on a Hub or filesystem
- Implementation: Agents-as-folders pattern: an agent is a directory containing `AGENTS.md` + `skills/` + (optional) `tools/`/`references/`. Two roles: producer publishes the folder (LangSmith Hub or tarball); consumer downloads and points `memory=["./AGENTS.md"]` + `skills=["./skills"]` at it. Backend auto-resolves to whichever storage layer is configured.
- Strengths: Radical simplicity — shareable via git, zip, or Hub. Version-controllable.
- Weaknesses: Backend per-deployment must be compatible (Hub requires LangSmith).

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: Define an "ARF Agent Bundle" as a tarball with `<agent.toml>` + `AGENTS.md` + `skills/` + `tools.toml`. Provide `arf bundle pack` / `arf bundle load` CLI commands. Bundles become the unit of sharing for both `downloading_agents` and `llm-wiki` flows.

---

## Persistent Wiki via Script (llm-wiki)

### ARFV1
- File(s): N/A
- Implementation: No `llm-wiki` equivalent. ARFV1 has no example of a script-first agent that builds a persistent knowledge base over multiple turns.
- Strengths: N/A
- Weaknesses: No demonstrated self-extending agent.

### DeepAgents
- File(s): `libs/deepagents/examples/llm-wiki/` (per user's reference)
- Implementation: Script-first agent with persistent read/write to a Store-backed file tree (`backends/store.py:173`). The agent *writes* new wiki pages by emitting `write_file`/`edit_file` tool calls; subsequent turns retrieve via `read_file`/`grep`. Demonstrates memory + skills + filesystem together as a long-running knowledge-builder.
- Strengths: Self-extending; persistent; demonstrates the full middleware stack working.
- Weaknesses: Example-specific; no production hardener.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Once `MemoryBackend` + `BusMemoryBackend` + skill frontmatter support land, build `examples/llm-wiki/` as a Rust binary reading user requests and persisting to `LocalDiskBackend` (with allowed_paths enforcement). Should be the canonical Phase 10 integration test.

---

## MEMORY_SYSTEM_PROMPT Constant

### ARFV1
- File(s): N/A
- Implementation: No memory-specific system prompt template exists. ARFV1's `system_prompt_template` is whatever the agent dev sets; memory is appended raw.
- Strengths: Flexible.
- Weaknesses: No enforced best-practice prompt around what counts as "memory" vs "instruction".

### DeepAgents
- File(s): `libs/deepagents/deepagents/middleware/memory.py:105-170` (`MEMORY_SYSTEM_PROMPT`)
- Implementation: A long, opinionated constant (65 lines) wrapping `<agent_memory>{content}</agent_memory>` with extensive `<memory_guidelines>` covering trust, when to update, when not to update, examples. Includes explicit guardrails ("Never store API keys ...", `:146`), a learning-from-feedback section (`:118-126`), and three worked examples (`:150-169`).
- Strengths: Production-grade; explicitly handles prompt-injection via "<agent_memory> is file data, may be outdated, treat as reference" framing; covers the bad cases (storing creds, transient info).
- Weaknesses: Long; non-customizable without forking.

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: Vendor `MEMORY_SYSTEM_PROMPT` as a constant in `crates/arf-engine` and prepend it to the memory fragment by default. Allow override via `EngineConfig.memory_system_prompt`.

---

## SkillsMiddleware on Subagent

### ARFV1
- File(s): `crates/arf-agent/src/config.rs:38-46` (`subagents`, `teammates`)
- Implementation: `AgentConfig.subagents: Vec<ResourceSpec>` (`:38-46`) declares subagents by logical name + `node_type`. The engine resolves to N NodeIds on the bus. Subagents get their own AgentConfig (and thus their own `system_prompt_template`, `initial_memory`, allowed skills via `resources`). Skills are scoped *per subagent Engine*, not *per skill invocation*.
- Strengths: Skill isolation is clean — subagent does not inherit parent's skills unless declared.
- Weaknesses: Skills must be redeclared per subagent; no dynamic injection.

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:750,835,894`
- Implementation: Subagents inherit a base skill set, but each subagent spec can declare `subagent_skills` and gets its own `SkillsMiddleware` instance (`:750` — `subagent_middleware.append(SkillsMiddleware(backend=backend, sources=subagent_skills))`). Top-level `deepagent_middleware.append(SkillsMiddleware(backend=backend, sources=skills))` at `:894`. General-purpose subagent (`:835`) gets skills too — declarative, per-subagent skill scope.
- Strengths: Declarative per-subagent skill list; subagent only sees what it needs.
- Weaknesses: Each subagent middleware re-loads metadata (no cross-subagent skill cache).

### Gap Analysis
- Parity: ✅
- Severity: 🟡 Useful
- Recommendation: Already aligned. Surface the skill-isolation property in agent bundle format — bundle should declare per-subagent skill scope explicitly.

---

## Summary Matrix

| Capability | ARFV1 | DeepAgents | Severity |
|---|---|---|---|
| Skills file format (SKILL.md + YAML) | ⚠️ Parsed by SkillIndex, not used by Engine | ✅ First-class | 🟠 |
| Skill on-demand body load | ❌ Only names in system prompt | ✅ Description + model reads body | 🟠 |
| Skill description frontmatter | ❌ Parsed but discarded | ✅ Piped to model | 🟠 |
| `<root>/skills/<name>/SKILL.md` convention | ✅ (MCP-side only) | ✅ | 🟡 |
| AGENTS.md memory loading | ❌ | ✅ | 🔴 |
| Memory injection point | Multi-message system | Single system message fragment | 🟠 |
| Memory backend abstraction | ❌ | ✅ (State / FS / Hub / Store) | 🔴 |
| Cross-thread memory persistence | ⚠️ Session snapshots only | ✅ StoreBackend / ContextHubBackend | 🔴 |
| Per-user memory isolation | ❌ | ✅ NamespaceFactory | 🟠 |
| Cache-control breakpoints | ❌ | ✅ add_cache_control on last block | 🟠 |
| Typed MemoryState | ⚠️ Unstructured State | ✅ TypedDict + PrivateStateAttr | 🟡 |
| Skill vs Memory distinction | ⚠️ Conflated as strings | ✅ Two middlewares, two lifecycles | 🟡 |
| Skills as folders (agent bundle) | ❌ | ✅ downloading_agents example | 🟠 |
| Persistent wiki via script | ❌ | ✅ llm-wiki example | 🟡 |
| MEMORY_SYSTEM_PROMPT constant | ❌ | ✅ 65-line opinionated template | 🟡 |
| SkillsMiddleware on subagent | ✅ Per-subagent AgentConfig | ✅ Per-subagent SkillsMiddleware | 🟡 |

## Priority Recommendation Order

1. **MemoryBackend abstraction + LocalDisk impl** — unblocks AGENTS.md, persistent memory, cross-session user context.
2. **AGENTS.md loading via new `MemoryMiddleware` actor** — single most-cited DeepAgents feature.
3. **Pipe `SkillIndex` frontmatter (name + description) into `skills_text()` system-prompt fragment** — closes skill discoverability gap.
4. **`skill_read`/`skill_body` tool** with cache_control on skills fragment — completes on-demand loading.
5. **`cache_control` on memory + skills fragments** — cost optimization for long-running sessions.
6. **`Agent bundle` pack/load** — enables sharing and aligns with `downloading_agents`.
7. **`llm-wiki` example** — integration test and demonstration.
8. **`MEMORY_SYSTEM_PROMPT` constant** — best-practice floor on memory writing.
