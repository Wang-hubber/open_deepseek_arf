# Phase 10 — Provider / Profile System

> Atomic-level comparison: per-provider and per-model harness tuning, system
> prompt suffix, tool description overrides, excluded tools, excluded
> middleware, extra middleware factory, general-purpose subagent profile,
> Anthropic prompt caching.

---

## 1. Provider abstraction

### ARFV1
- `crates/arf-model-adapter/src/provider.rs:22-69`: `Provider` is a
  `Send + Sync` async trait with `name()`, `supported_models() ->
  &[String]`, `chat()`, default-fallback `chat_stream()`. Each provider
  impl lives in one file (`{openai,deepseek,anthropic,minimax}.rs`).
- Strengths: Adding a provider is one new `impl Provider`; Node, Engine,
  Bus are provider-agnostic; `supported_models()` drives uniform
  `node_online` capability broadcast.
- Weaknesses: No first-class harness concept; per-model tuning leaks
  into `ModelDecl.extra`.

### DeepAgents
- `libs/deepagents/deepagents/profiles/provider/provider_profiles.py:37-130`
  and `libs/deepagents/deepagents/_models.py:35-57`: `ProviderProfile`
  is a frozen dataclass with `init_kwargs`, optional `pre_init(spec)`,
  optional `init_kwargs_factory()`. `resolve_model()` calls
  `init_chat_model(spec, **apply_provider_profile(spec))`.
- Strengths: Provider/harness separation clean; `init_kwargs_factory`
  covers runtime-derived values.
- Weaknesses: Dependent on LangChain's `init_chat_model` string format.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟠 Important
- Recommendation: Keep typed `Provider` as wire boundary; add
  `HarnessProfile` orthogonal to `ModelDecl.extra`.

---

## 2. Per-provider adapter count

### ARFV1
- `crates/arf-model-adapter/src/{openai,deepseek,anthropic,minimax}.rs`:
  four hand-rolled adapters behind the common `Provider` trait.

### DeepAgents
- `libs/deepagents/deepagents/profiles/provider/_openai.py:19-25`,
  `_openrouter.py:122-125`, `_nvidia.py:45-47`,
  `libs/deepagents/deepagents/CHANGELOG.md:5-11`: three built-in
  `ProviderProfile` registrations (OpenAI, OpenRouter, NVIDIA NIM) plus
  the `deepagents[aws]` extra for Bedrock.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: Compose LangChain-style `init_kwargs` with ARFV1's
  typed `Provider` trait to reduce adapter boilerplate.

---

## 3. Per-model profile

### ARFV1
- `crates/arf-agent/src/model.rs:10-45`, `config.rs:26-27`: `ModelDecl`
  carries `provider`, `model_name`, `endpoint`, `api_key_env`,
  `thinking_enabled`, `temperature`, `max_output_tokens`, `extra`. Tuned
  per-decl, listed in priority order. No harness overlay.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:484-543`,
  line 1255-1303: `HarnessProfile` registered per provider or
  `provider:model`. `_harness_profile_for_model()` resolves a built
  `BaseChatModel + spec` via `_get_ls_params`, fallback exact →
  provider prefix → empty default.
- Strengths: First-class concept with per-field merge (`_merge_profiles`,
  line 1197).

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important
- Recommendation: Add `ModelTuning` dataclass keyed by `ModelDecl`
  index or `"provider:model"` string.

---

## 4. Profile registry

### ARFV1
- `crates/arf-agent/src/config.rs`: no global registry; `ModelDecl`
  embedded in `AgentConfig`.

### DeepAgents
- `libs/deepagents/deepagents/profiles/provider/provider_profiles.py:195-247`
  and `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:980-1028`:
  `register_provider_profile` /
  `register_harness_profile` write into `_PROVIDER_PROFILES` (line 166)
  and `_HARNESS_PROFILES` (line 940). Additive merge on
  re-registration. Third-party plugins via `importlib.metadata` entry
  points (`deepagents.provider_profiles`, `deepagents.harness_profiles`)
  at `_builtin_profiles.py:155-156`.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important
- Recommendation: Add `ModelTuningRegistry` in `arf-model-adapter`
  with `register(key, tuning)` / `get(key) -> Option<ModelTuning>`.
  Make entry-point discovery opt-in.

---

## 5. Profile merge logic

### ARFV1
- N/A. `models: Vec<ModelDecl>` is priority-ordered; engine picks the
  first whose node is online (`config.rs:27`).

### DeepAgents
- `libs/deepagents/deepagents/profiles/provider/provider_profiles.py:383-455`
  and `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:1197-1252`:
  `_merge_provider_profiles` chains `pre_init` and `init_kwargs_factory`,
  merges `init_kwargs` per key (override wins). `_merge_profiles`: set
  fields union, `tool_description_overrides` per-key merge, scalar
  fields prefer override, excluded-tool/middleware union,
  `general_purpose_subagent` field-wise merge.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important
- Recommendation: Copy the per-field merge policy verbatim when
  introducing `ModelTuning`.

---

## 6. Excluded tools

### ARFV1
- `crates/arf-agent/src/config.rs:28-31`: `tools: Vec<ToolSpec>`.
  Removal is a config-level omission — no runtime path.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:616-627`
  and line 282-283: `excluded_tools: frozenset[str]` on both
  `HarnessProfile` and `HarnessProfileConfig`. Applied via a
  tool-exclusion middleware after tool-injecting middleware.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Add `excluded_tools: BTreeSet<String>` to
  `ModelTuning`; post-resolve filter tool list in `Engine::run`.

---

## 7. Excluded middleware

### ARFV1
- `crates/arf-engine/src/engine.rs`: middleware fixed at compile time.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:629-688`:
  `excluded_middleware: frozenset[type[AgentMiddleware] | str]` accepts
  class (exact-type match) or string (`AgentMiddleware.name`).
  Scaffolding rejected at construction via
  `_format_scaffolding_rejection` (line 65-79). Grammar validated by
  `_validate_config_middleware_string` (line 869).

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: When ARFV1 introduces pluggable middleware, mirror
  the class-or-string union and the scaffolding-rejection guard.

---

## 8. Extra middleware factory

### ARFV1
- `EngineBuilder::build` takes static middleware; no zero-arg factory
  slot.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:690-713`,
  line 762-780: `extra_middleware: Sequence[AgentMiddleware] | Callable[[], Sequence[AgentMiddleware]]`.
  `materialize_extra_middleware()` invokes factory if supplied. Discriminated
  `callable()` vs sequence; rebuilt per resolution.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Defer until middleware is pluggable. Pattern is
  correct; copy verbatim.

---

## 9. Tool description overrides

### ARFV1
- `ToolSpec` (`config.rs`) carries one-shot `description: Option<String>`.
  No per-profile override mechanism.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:583-614`:
  `tool_description_overrides: Mapping[str, str]` wrapped in
  `MappingProxyType` (line 322-329). `task` override must include the
  `{available_agents}` placeholder (line 604-613). Plain callable tools
  unchanged (line 598-602). Merge at line 1241-1244 with override
  winning per key.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Add `description_overrides: BTreeMap<String, String>`
  to `ModelTuning`; overwrite tool descriptions post-resolve.

---

## 10. base_system_prompt override

### ARFV1
- `crates/arf-engine/src/engine.rs:50-51`: one `system_prompt_template`
  per Engine. No replacement slot.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:545-560`,
  line 783-801: `base_system_prompt: str | None` replaces
  `BASE_AGENT_PROMPT`. `_apply_profile_prompt` (line 798) applies
  uniformly across main agent, declarative subagents, auto-added GP
  subagent.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: `ModelTuning.base_system_prompt: Option<String>`
  overrides the per-agent template when a tuning key matches.

---

## 11. system_prompt_suffix

### ARFV1
- `crates/arf-engine/src/engine.rs:822`: pushes only the per-agent
  template; no suffix hook.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:562-581`,
  line 800: `prompt + "\n\n" + profile.system_prompt_suffix`. Appended
  last, uniformly across main agent and subagents.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important
- Recommendation: Add `system_prompt_suffix: Option<String>` to
  `ModelTuning`; append after `system_prompt_template` in
  `Engine::build_messages`.

---

## 12. GeneralPurposeSubagentProfile

### ARFV1
- `crates/arf-agent/src/config.rs:38-41`: `subagents: Vec<ResourceSpec>`
  is opt-in only; no auto-added default.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:82-188`:
  `GeneralPurposeSubagentProfile` with three-state `enabled: bool | None`
  (None = inherit/true, True = force, False = disable),
  `description`, `system_prompt`. GP `system_prompt` wins over
  `HarnessProfile.base_system_prompt` (line 124-136).

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Track as follow-up after phase10 lands; current ARFV1
  keeps subagents opt-in by design.

---

## 13. File-friendly config

### ARFV1
- `crates/arf-agent/src/config.rs:20-47`: one file-friendly
  dataclass — `AgentConfig`. No separate runtime-only subset.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:191-480`:
  `HarnessProfileConfig` is the YAML/JSON subset (excludes
  `extra_middleware`). `from_dict`/`to_dict` (line 339-405),
  `to_harness_profile()` (line 407). Reverse conversion raises
  `ValueError` on runtime-only state (line 461-468).

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: When introducing `ModelTuning`, mirror the
  `*Config` split so file-loaded YAML can layer onto programmatic
  runtime-only fields without lossy coercion.

---

## 14. Provider-specific init_kwargs

### ARFV1
- `crates/arf-agent/src/model.rs:41-44`,
  `crates/arf-model-adapter/src/types.rs:19-24`: `ModelDecl.extra:
  serde_json::Value`, mirrored on the wire by `ModelParams.extra`.
- Weaknesses: No defensive immutability; incidental mutation propagates.

### DeepAgents
- `libs/deepagents/deepagents/profiles/provider/provider_profiles.py:79-91,
  132-163`: `ProviderProfile.init_kwargs: Mapping[str, Any]` wrapped in
  `MappingProxyType` via `__post_init__` (line 158-163). Registry stores
  its own defensive copy.
- Strengths: Post-construction mutation raises `TypeError`.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: Wrap `ModelDecl.extra` in a frozen serde wrapper when
  used inside the registry.

---

## 15. Model normalization

### ARFV1
- `crates/arf-agent/src/model.rs:13`: `provider: String` is free-form;
  alias normalization is each adapter's job.

### DeepAgents
- `libs/deepagents/deepagents/_models.py:15-23, 146-203`:
  `_PROVIDER_ALIASES = {"azure_openai": "azure", "mistralai":
  "mistral"}` plus `_normalize_provider()` (lowercase, `-`→`_`).
  `model_matches_spec()` compares normalized strings.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Add a `ProviderId` newtype with `as_canonical()` for
  the four built-in providers.

---

## 16. Bedrock detection

### ARFV1
- N/A — no Bedrock model surface.

### DeepAgents
- `libs/deepagents/deepagents/_models.py:25-33, 122-143`:
  `is_bedrock_model()` checks provider against `_BEDROCK_PROVIDERS =
  {"amazon_bedrock", "anthropic_bedrock", "aws", "bedrock",
  "bedrock_converse"}` and class names against `_BEDROCK_MODEL_CLASSES
  = {"ChatAnthropicBedrock", "ChatBedrock", ...}`. Strips
  `_BEDROCK_REGIONAL_PREFIXES` (`apac.`, `amer.`, `au.`, `eu.`, `jp.`,
  `sa.`, `us.`, `us-gov.`, `global.`) before the Nova check.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Document out-of-scope; defer to Phase 11+.

---

## 17. Anthropic prompt caching middleware

### ARFV1
- N/A. The Anthropic adapter ships only the chat completion call.

### DeepAgents
- `libs/deepagents/deepagents/graph.py:18, 255-264, 468-470`: imports
  `langchain_anthropic.middleware.AnthropicPromptCachingMiddleware` and
  always appends via `_append_prompt_caching_middleware()`.
  `unsupported_model_behavior="ignore"` makes it a no-op on non-
  Anthropic. Per-memory-block breakpoint at `memory.py:391-403`
  complements it.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟠 Important
- Recommendation: Add an `anthropic-cache` feature to
  `arf-model-adapter`. When enabled, wrap system prompt + tool schema
  in `cache_control` breakpoints before each `chat_stream()`.

---

## 18. Bedrock prompt caching middleware

### ARFV1
- N/A. No Bedrock adapter, no caching middleware.

### DeepAgents
- `libs/deepagents/deepagents/graph.py:245-264`,
  `libs/deepagents/deepagents/CHANGELOG.md:5-11`:
  `_create_bedrock_prompt_caching_middleware()` lazy-imports
  `langchain_aws.middleware.prompt_caching`. Falls back to `DEBUG` log +
  `None` when not installed (line 250-253). 0.6.12 added the
  `deepagents[aws]` extra.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: Defer until Bedrock is wired.

---

## 19. Memory cache_control breakpoints

### ARFV1
- N/A — no per-block content block system. ARFV1 stores
  `ModelMessage.content: String`; `cache_control` not injectable.

### DeepAgents
- `libs/deepagents/deepagents/middleware/memory.py:391-403`:
  `MemoryMiddleware._add_cache_control = True` mutates the last content
  block at `modify_request()`:
  `blocks[-1] = {**base, "cache_control": {"type": "ephemeral"}}`. Pairs
  with `AnthropicPromptCachingMiddleware`'s breakpoint (line 215-219).
  No-op on non-`ChatAnthropic` and Bedrock/Vertex wrappers.

### Gap Analysis
- Parity: ❌ Missing · Severity: 🟡 Useful
- Recommendation: After introducing per-block content in `ModelMessage`,
  expose optional `cache_control` on system-message blocks.

---

## 20. Provider-specific model resolution

### ARFV1
- `crates/arf-agent/src/model.rs:13-14`,
  `crates/arf-engine/src/engine.rs:92`: `ModelDecl.provider` +
  `ModelDecl.model_name` — two-part, not colon-joined. Adapter
  selection via `config.model.provider`.
- Strengths: Two fields individually typed; no string parsing.

### DeepAgents
- `libs/deepagents/deepagents/_models.py:35-57`:
  `init_chat_model("openai:gpt-5.4", **apply_provider_profile(model))`.
  Spec string is the sole public API.
- Weaknesses: `provider_profiles.py:293-298` rejects malformed specs
  only at lookup, not construction.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: Keep typed two-field model. Add `FromStr` for
  `"provider:model"` for interop with DeepAgents config imports.

---

## 21. Profile bootstrap safety

### ARFV1
- N/A — no lazy bootstrap; loaded eagerly via Rust static initializers.

### DeepAgents
- `libs/deepagents/deepagents/profiles/_builtin_profiles.py:74-176`:
  `_loaded: bool` + `_BOOTSTRAP_CONDITION: threading.Condition()` +
  `_loading_thread_id: int | None`. Same-thread re-entry short-circuits
  (line 137); other threads block on `_BOOTSTRAP_CONDITION.wait()`
  (line 140). On exception, restores saved registries in place
  (line 162-169).

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: If ARFV1 adopts entry-point loading, copy the
  condition-variable + thread-id pair verbatim.

---

## 22. Profile key validation

### ARFV1
- `crates/arf-agent/src/config.rs:83-107`: `provider` / `model_name`
  are `String` with no validation beyond non-empty presence.

### DeepAgents
- `libs/deepagents/deepagents/profiles/_keys.py:11-43`:
  `validate_profile_key()` rejects empty, padded, multi-colon,
  empty-half, whitespace-adjacent-to-colon strings.
- Strengths: Fail-fast at registration; consistent across both
  registries.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: Add `validate_provider_id()` to ARFV1 in
  `arf-agent::config` once a tuning registry lands.

---

## 23. system_prompt_template (per-agent)

### ARFV1
- `crates/arf-engine/src/engine.rs:50-51, 146-147, 822`:
  `system_prompt_template` is one `String` per Engine. Engine pushes it
  as the first message at line 822:
  `messages.push(ModelMessage::new("system", &self.system_prompt_template))`.
- Weaknesses: One agent → one template; no layered assembly.

### DeepAgents
- `libs/deepagents/deepagents/profiles/harness/harness_profiles.py:783-801`:
  `_apply_profile_prompt()` builds a 3-slot layered prompt —
  caller-supplied prompt, then `base_system_prompt` (or
  `BASE_AGENT_PROMPT` default), then `system_prompt_suffix`.
- Strengths: Same overlay applies uniformly to main agent and subagents.

### Gap Analysis
- Parity: ⚠️ Partial · Severity: 🟡 Useful
- Recommendation: Refactor `Engine::build_messages` to compose three
  slots: caller-prompt (`AgentConfig.system_prompt`), base/template,
  optional profile suffix.

---

## Summary

5 🟠 (caps 1, 3, 4, 5, 11, 17): typed `ModelTuning` dataclass + registry
mirroring DeepAgents' two-phase design; `system_prompt_suffix` wiring in
`Engine::build_messages`; `anthropic-cache` feature for prompt caching.
Remaining 17 capabilities are 🟡 (Useful): file-friendly config split,
frozen `extra`, registry thread-safety, defsensible failure pathways for
Bedrock, prompt cache breakpoints, profile key validation,
`FromStr<"provider:model">`, and the layered `system_prompt_template`.
