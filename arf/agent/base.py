"""BaseAgent — assembles all Protocol implementations into a running Agent."""
from pathlib import Path
from typing import Any
from arf.agent.config import AgentConfig, AdvancedConfig
from arf.agent.app_context import AppContext
from arf.engine.graph import GraphEngine
from arf.engine.loop_strategies.react import ReActStrategy
from arf.engine.checkpoint import InMemoryStateStore, FileStateStore
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.promotion.gate import Promotion
from arf.action_runner.runner import ActionRunner

from arf.event_bus import InMemoryEventBus
from arf.resources.resolver import ResourceResolver
from arf.resources.providers.tool_provider import ToolProvider
from arf.memory.file_store import FileMemoryStore
from arf.memory.recent_first import RecentFirstRetriever
from arf.memory.writer import RuleBasedMemoryWriter
from arf.hooks.runner import SubprocessHookRunner
from arf.guardrails.runner import DefaultGuardRunner
from arf.guardrails.none_guard import NoneInputGuard
from arf.guardrails.regex_clean import RegexOutputGuard
from arf.guardrails.path_check import PathCheckToolGuard
from arf.guardrails.permissions import ToolPermissionChecker
from arf.errors.retry import DefaultErrorPolicy


def _parse_duration(s: str) -> float:
    """Parse a duration string like '60s', '5m', '1h' into float seconds."""
    s = s.strip()
    if s.endswith("s"):
        return float(s[:-1])
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    raise ValueError(f"Unsupported duration unit: {s}")


def _load_resident_memory(memory_dir: str, resident_file: str = "memory.md",
                          max_size_bytes: int = 300 * 1024) -> str:
    """Load resident memory from a single Markdown file.

    Called once per session startup. Returns empty string if file absent.
    Truncates content exceeding max_size_bytes, keeping complete lines.
    """
    memory_path = Path(memory_dir) / resident_file
    if not memory_path.exists():
        return ""

    content_bytes = memory_path.read_bytes()
    if len(content_bytes) <= max_size_bytes:
        return content_bytes.decode("utf-8")

    truncated = content_bytes[:max_size_bytes]
    text = truncated.decode("utf-8", errors="replace")
    last_newline = text.rfind("\n")
    if last_newline > 0:
        text = text[:last_newline]
    text += "\n\n<!-- WARNING: resident memory truncated at "
    text += f"{max_size_bytes // 1024}KB -->\n"
    return text


class BaseAgent:
    def __init__(self, config: AgentConfig, app_context: AppContext | None = None, **override_protocols) -> None:
        self.config = config
        adv = config.effective_advanced()
        ctx = app_context

        # Absorb removed protocol keys to prevent leakage into **override_protocols
        override_protocols.pop("transaction_ctx", None)

        # 1. Core infrastructure
        event_bus = override_protocols.pop("event_bus", InMemoryEventBus())
        default_state_dir = str(ctx.state_dir) if ctx else "./data/state"
        state_store = override_protocols.pop("state_store", FileStateStore(default_state_dir))

        # 2. Resources — MCP-based unified management
        # McpClientManager replaces ToolProvider + SkillProvider +
        # PluginProvider + ResourceResolver quartet.
        tools_dir = override_protocols.pop(
            "tools_dir", ctx.tools_dir if ctx else Path("./tools")
        )
        skills_dir = override_protocols.pop(
            "skills_dir", ctx.skills_dir if ctx else Path("./skills")
        )
        models_dir = override_protocols.pop(
            "models_dir", ctx.models_dir if ctx else Path("./models")
        )

        # ModelProvider stays in BaseAgent (models not in MCP scope)
        from arf.resources.providers.model_provider import ModelProvider
        model_provider = ModelProvider(models_dir)
        self._merge_models(config, model_provider)

        # Resolve plugins_dir
        _plugins_dir_raw = override_protocols.pop("plugins_dir", None)
        if _plugins_dir_raw is None:
            import arf as _arf_pkg
            _arf_root = Path(_arf_pkg.__file__).parent
            _plugins_dir = _arf_root / "plugins"
        else:
            _plugins_dir = Path(_plugins_dir_raw)

        # MCP Client Manager — spawns local MCP server subprocess
        from arf.mcp.client_manager import McpClientManager
        mcp_manager = override_protocols.pop(
            "mcp_manager",
            McpClientManager(
                tools_dir=tools_dir,
                skills_dir=skills_dir,
                models_dir=models_dir or Path("./models"),
                plugins_dir=_plugins_dir,
                mcp_servers=getattr(config, "mcp_servers", []),
                plugin_names=config.plugins,
            ),
        )
        self._mcp_manager = mcp_manager

        # Plugin system — for hooks only (tools/skills via MCP)
        self._plugin_provider = None
        if config.plugins:
            from arf.resources.providers.plugin_provider import PluginProvider
            self._plugin_provider = PluginProvider(_plugins_dir, config.plugins)

        # FileWatcher
        reload_cfg = adv.reload if adv else None
        watch_enabled = override_protocols.pop(
            "watch_enabled",
            reload_cfg.watch if reload_cfg else True,
        )
        from arf.resources.file_watcher import FileWatcher
        file_watcher = FileWatcher(
            poll_interval=reload_cfg.poll_interval if reload_cfg else 5.0
        ) if watch_enabled else None
        self._file_watcher = file_watcher

        # 3. Memory — LLM-driven by default, falls back to rule-based
        from pathlib import Path as _Path
        _mem_dir = str(ctx.memory_dir) if ctx else "./data/memory"
        _workspace_dir = str(ctx.root) if ctx else "."
        _trace_dir = str(ctx.trace_dir) if ctx else "./data/traces"
        from arf.core.plugin_runtime import PluginRuntime

        plugin_runtime = PluginRuntime(
            memory_dir=_mem_dir,
            workspace_dir=_workspace_dir,
            state_dir=str(ctx.state_dir) if ctx else "./data/state",
            trace_dir=_trace_dir,
            files_dir=str(ctx.files_dir) if ctx else "./data/files",
            system_model=adv.system_model if adv else "quick",
            model_configs={
                m.type: {
                    "api_base": m.api_base,
                    "api_key_env": m.api_key_env,
                    "context_window": m.context_window,
                }
                for m in config.models
            },
        )
        memory_store = override_protocols.pop("memory_store", FileMemoryStore(_mem_dir))

        # Build system model adapter for all background tasks (memory, routing, compaction).
        # Uses advanced.system_model if set, otherwise falls back to the first configured model.
        _system_model_call = None
        system_model_name = adv.system_model if adv else None
        if not system_model_name and config.models:
            system_model_name = config.models[0].type

        if system_model_name:
            import os as _os2, asyncio as _aio2
            from arf.core.model_adapter import ModelAdapter as _SystemAdapter
            system_model_cfg = next(
                (m for m in config.models if m.type == system_model_name),
                None,
            )
            if system_model_cfg:
                _system_adapter = _SystemAdapter({
                    "base_url": system_model_cfg.api_base,
                    "api_key": _os2.environ.get(system_model_cfg.api_key_env, ""),
                    "model_name": system_model_cfg.model,
                    "temperature": 0.3,
                    "thinking_enabled": False,
                    "max_tokens": 1024,
                })

                async def _system_model_call(prompt: str) -> str:
                    """Call the system model with a simple prompt, return text content."""
                    msg = await _system_adapter.chat_complete(
                        [{"role": "user", "content": prompt}],
                        tools=None,
                        max_tokens=1024,
                    )
                    return msg.content or ""

        # Memory extraction moved to arf/plugins/memory/ plugin.
        # Framework no longer constructs or holds a writer/retriever.
        memory_writer = override_protocols.pop("memory_writer", None)
        memory_retriever = override_protocols.pop("memory_retriever", None)

        # 3.5 Compaction — sliding window when context exceeds threshold
        compaction = override_protocols.pop("compaction", None)
        cmp_cfg = (adv.compaction or AdvancedConfig.default().compaction) if adv else None
        if cmp_cfg and cmp_cfg.strategy != "none" and not compaction:
            from arf.compaction.sliding_window import SlidingWindowCompactor
            _summarizer = None
            if _system_model_call:
                async def _summarize(msgs: list[dict]) -> str:
                    text = "\n".join(
                        f"[{m.get('role', '?')}] {m.get('content', '')[:300]}"
                        for m in msgs[-30:]  # last 30 messages for context
                    )
                    prompt = (
                        "You are compacting conversation history to free context space.\n"
                        "Write a structured summary that preserves the essential state:\n\n"
                        "<conversation>\n{text}\n</conversation>\n\n"
                        "Output a concise summary with these sections (omit empty ones):\n"
                        "- Completed: tasks finished, problems solved\n"
                        "- In Progress: current task, remaining TODO items\n"
                        "- Files Modified: paths and what was changed\n"
                        "- Decisions: architectural choices, agreed approaches\n"
                        "- Facts & Preferences: user info, likes/dislikes, constraints\n"
                        "- Errors & Debugging: error messages, stack traces, hypotheses\n"
                        "- Next Steps: what should happen next\n\n"
                        "Rules:\n"
                        "- Be specific: include file paths, function names, error messages verbatim\n"
                        "- Be concise: each bullet one line, 3-8 bullets per section\n"
                        "- Preserve reasoning: why decisions were made, not just what\n"
                        "- Keep user facts intact: name, location, preferences, skills\n\n"
                        "Summary:"
                    ).replace("{text}", text)
                    try:
                        return (await _system_model_call(prompt)).strip()
                    except Exception:
                        return "(conversation summary unavailable)"
                _summarizer = _summarize
            compaction = SlidingWindowCompactor(
                threshold=cmp_cfg.threshold,
                summarizer=_summarizer,
            )

        # 4. Guardrails — driven by adv.guardrails config, defaults match existing behavior
        _workspace_root = str(ctx.root.resolve()) if ctx else str(Path(".").resolve())
        gr_cfg = adv.guardrails if adv else None
        if gr_cfg and gr_cfg.input == "none":
            input_guard = NoneInputGuard()
        else:
            input_guard = NoneInputGuard()  # only "none" implemented currently
        if gr_cfg and gr_cfg.output == "none":
            output_guard = None
        elif gr_cfg and gr_cfg.output_patterns:
            patterns = [(p.pattern, p.replacement) for p in gr_cfg.output_patterns]
            output_guard = RegexOutputGuard(patterns=patterns)
        else:
            output_guard = RegexOutputGuard()  # built-in defaults
        if gr_cfg and gr_cfg.tool_params == "none":
            tool_guard = None
        else:
            sandbox_cfg = adv.sandbox if adv else None
            checks = sandbox_cfg.checks.model_dump() if sandbox_cfg and sandbox_cfg.checks else None
            tool_guard = PathCheckToolGuard(
                workspace_root=_workspace_root,
                writable_dirs=sandbox_cfg.writable_dirs if sandbox_cfg else None,
                allow_escape=sandbox_cfg.allow_escape if sandbox_cfg else False,
                checks=checks,
            )
        # Session mode manager + PermissionRegistry (unified permission system)
        from arf.session import SessionModeManager, SessionMode, PermissionRegistry, PermissionLists, AgentPolicy
        global_mode = SessionMode(config.session_mode) if config.session_mode else SessionMode.ASK
        session_mode_manager = SessionModeManager(global_mode=global_mode)
        permission_registry = PermissionRegistry()

        # Main agent permission lists
        perm_cfg = gr_cfg.permissions.model_dump() if gr_cfg and gr_cfg.permissions else None
        main_permission_lists = PermissionLists.from_config(perm_cfg)

        # Main agent policy from permissions config
        main_policy_raw = gr_cfg.permissions.policy if gr_cfg and gr_cfg.permissions else None
        self._main_agent_policy = AgentPolicy(main_policy_raw) if main_policy_raw else None
        self._main_permission_lists = main_permission_lists

        guard_runner = override_protocols.pop("guard_runner", DefaultGuardRunner(
            input_guard=input_guard,
            output_guard=output_guard,
            tool_guard=tool_guard,
            permission_registry=permission_registry,
            permission_lists=main_permission_lists,
        ))

        # 5. Error + Transaction
        err_cfg = (adv.errors or AdvancedConfig.default().errors) if adv else None
        error_policy = override_protocols.pop("error_policy", DefaultErrorPolicy(
            tool_retry=(err_cfg.tool_retry if err_cfg else 2),
            tool_backoff=(err_cfg.tool_backoff if err_cfg else "exponential"),
            model_5xx_action=(err_cfg.model_5xx_action if err_cfg else "fallback"),
            guardrail_block_action=(err_cfg.guardrail_block_action if err_cfg else "abort"),
        ))

        # 6. Hooks
        hooks_list = list(override_protocols.pop("hooks", list(config.hooks)))
        if self._plugin_provider and self._plugin_provider.list_hooks():
            hooks_list.extend(self._plugin_provider.list_hooks())
        hook_runner = override_protocols.pop("hook_runner", SubprocessHookRunner(hooks_list, plugin_runtime=plugin_runtime))

        # 7. Tool executor
        from arf.core.config_base import ConcurrencyConfig
        cc_cfg = adv.concurrency if adv and adv.concurrency else ConcurrencyConfig()
        tool_executor = override_protocols.pop(
            "tool_executor",
            ConcurrentToolExecutor(
                mcp_manager,
                strategy=cc_cfg.strategy,
                max_concurrency=cc_cfg.max_concurrency,
                tool_guard=tool_guard,
            ),
        )

        # 8. Loop strategy
        ls_name = (adv.loop_strategy if adv else "react") if adv else "react"
        loop_strategy = override_protocols.pop("loop_strategy", ReActStrategy(max_turns=(adv.max_turns if adv else 50)))

        # 9. Planner (optional)
        planner = override_protocols.pop("planner", None)

        # 4.5 Sub-agents — create from config.agents
        self._sub_agent_configs: dict = {}
        if config.agents:
            import os as _os3
            from arf.core.model_adapter import ModelAdapter as _SubModelAdapter
            for sub_cfg in config.agents:
                from arf.agent.default_prompt_provider import DefaultSystemPromptProvider as _SubProvider
                sub_provider = _SubProvider(config=sub_cfg)
                sub_prompt = sub_provider.build().full_text
                sub_adapters = {}
                for m in (sub_cfg.models or []):
                    sub_adapter_cfg: dict[str, Any] = {
                        "base_url": m.api_base,
                        "api_key": _os3.environ.get(m.api_key_env, ""),
                        "model_name": m.model,
                        **m.kwargs,
                    }
                    if m.max_token is not None:
                        sub_adapter_cfg["max_tokens"] = m.max_token
                    sub_adapters[m.type] = _SubModelAdapter(sub_adapter_cfg)
                # Per-agent permission lists
                sub_adv = sub_cfg.effective_advanced()
                sub_perms = sub_adv.guardrails.permissions if sub_adv.guardrails else None
                sub_perm_lists = PermissionLists.from_config(
                    sub_perms.model_dump() if sub_perms else None
                )
                # Store per-agent policy for session mode resolution
                sub_policy_raw = sub_perms.policy if sub_perms else None
                sub_policy = AgentPolicy(sub_policy_raw) if sub_policy_raw else None
                self._sub_agent_configs[sub_cfg.name] = {
                    "config": sub_cfg,
                    "system_prompt": sub_prompt,
                    "adapters": sub_adapters,
                    "permission_lists": sub_perm_lists,
                    "agent_policy": sub_policy,
                }

        # 4.6 Handoff manager — from config.handover rules
        from arf.engine.handoff import HandoffManager
        handoff_manager = None
        if config.handover and config.handover.rules:
            handoff_manager = HandoffManager(
                rules=config.handover.rules,
                system_model_call=_system_model_call,
            )

        # 10. Build system prompt via provider (prefix only — inventory via MCP)
        from arf.agent.default_prompt_provider import DefaultSystemPromptProvider
        prompt_provider = override_protocols.pop(
            "system_prompt_provider",
            DefaultSystemPromptProvider(config=config),
        )
        system_prompt_obj = prompt_provider.build()
        system_prompt = system_prompt_obj.full_text

        # Fill $INVENTORY once at startup via MCP, cached for subsequent turns
        inventory_text = self._build_inventory_from_mcp()
        if inventory_text:
            system_prompt = system_prompt.replace("$INVENTORY", inventory_text)

        # Load resident memory — injected into $MEMORY once at session start
        from arf.core.config_base import MemoryConfig
        _mem_cfg = (adv.memory or MemoryConfig()) if adv else MemoryConfig()
        resident_memory = _load_resident_memory(
            _mem_cfg.workspace,
            resident_file=_mem_cfg.resident_file,
            max_size_bytes=_mem_cfg.max_size_kb * 1024,
        )
        if resident_memory:
            system_prompt = system_prompt.replace("$MEMORY", resident_memory)

        # Auto-create model router if routing config is set (LLM classifier by default)
        model_router = None
        if adv and adv.routing and len(config.models) > 1:
            from arf.routing.two_tier import TwoTierRouter
            if adv.routing.strategy == "static":
                # static: always use default model, no classification
                model_router = TwoTierRouter(
                    config=adv.routing,
                    models=[m.type for m in config.models],
                )
            elif _system_model_call:
                from arf.routing.two_tier import keyword_classify

                async def _classify(query: str) -> str:
                    # Fast keyword heuristic first (E2E Bug 3.4)
                    kw_result = keyword_classify(query)
                    if kw_result is not None:
                        return kw_result
                    # Ambiguous — fallback to LLM classifier
                    prompt = (
                        "Classify this task as 'medium' or 'complex'. "
                        "medium = simple chat, file I/O, single tool call. "
                        "complex = multi-step reasoning, many tool calls, code generation, planning. "
                        "Return ONLY one word (medium or complex).\n\n"
                        f"Task: {query[:300]}"
                    )
                    try:
                        result = (await _system_model_call(prompt)).strip().lower()
                        return result if result in ("medium", "complex") else "medium"
                    except Exception:
                        return "medium"
                model_router = TwoTierRouter(
                    config=adv.routing,
                    models=[m.type for m in config.models],
                    classifier_call=_classify,
                )
            else:
                model_router = TwoTierRouter(
                    config=adv.routing,
                    models=[m.type for m in config.models],
                )

        self._engine = GraphEngine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_executor=tool_executor,
            tool_resolver=mcp_manager,
            planner=planner,
            memory_store=memory_store,
            memory_retriever=memory_retriever,
            memory_writer=memory_writer,
            hook_runner=hook_runner,
            guard_runner=guard_runner,
            event_bus=event_bus,
            error_policy=error_policy,
            model_router=model_router,
            compaction=compaction,
            memory_max_tokens=_mem_cfg.max_tokens if _mem_cfg else 2000,
            memory_top_k=_mem_cfg.top_k if _mem_cfg else 5,
            system_prompt=system_prompt,
            max_turns=(adv.max_turns if adv else 50),
            max_undo_depth=(adv.max_undo_depth if adv else 3),
            approval_enabled=(gr_cfg is not None and len(gr_cfg.permissions.ask) > 0) if gr_cfg else False,
            approval_allowlist=(list(gr_cfg.permissions.ask) if gr_cfg and gr_cfg.permissions.ask else None),
            approval_timeout=_parse_duration(gr_cfg.permissions.approval.timeout if gr_cfg and gr_cfg.permissions else "60s"),
            sub_agent_configs=self._sub_agent_configs,
            handoff_manager=handoff_manager,
            memory_workspace=_mem_dir,
            workspace_dir=str(ctx.workspace_dir) if ctx else "./workspace",
            promotion=self._build_promotion(adv) if adv else None,
            session_mode_manager=session_mode_manager,
            main_permissions=adv.guardrails.permissions if adv and adv.guardrails else None,
            action_runner=ActionRunner() if adv else None,
            **override_protocols,
        )
        # Pass model context windows to engine for compaction decisions
        self._engine.set_model_windows(
            {m.type: m.context_window for m in config.models}
        )
        # Store main agent's tools so _active_config doesn't fall back to resolver
        self._engine._main_agent_tools = list(config.tools)
        self._state_store = state_store
        self._event_bus = event_bus
        self._memory_store = memory_store
        self._tool_resolver = mcp_manager
        # Auto-create usage tracker (framework default)
        obs_cfg = adv.observability if adv else None
        from arf.observability.usage_tracker import UsageTracker
        usage_dir = obs_cfg.usage_dir if obs_cfg else "./memory"
        self._usage_tracker = UsageTracker(event_bus, dir=usage_dir)

        # Auto-create trace store (framework default)
        trace_dir = str(ctx.trace_dir) if ctx else (obs_cfg.trace_dir if obs_cfg else "./memory/traces")
        from arf.observability import FileTraceStore
        self._trace_store = FileTraceStore(event_bus, dir=trace_dir)

        # ---- Auto-inject model API call ----
        self._inject_model_calls(config)

        # ---- Active session tracking ----
        self._active_sessions: set[str] = set()

    def _build_inventory_from_mcp(self) -> str:
        """Build inventory section from MCP tool list. Called at startup.

        Returns empty string if MCP is not available yet.
        """
        try:
            tools = self._mcp_manager.get_tool_definitions_sync()
        except Exception:
            return ""
        kernel = [t for t in tools if t.get("activation", "") == "kernel"]
        discoverable = [t for t in tools if t.get("activation", "") == "discoverable"]
        lines: list[str] = []
        if kernel:
            lines.append("## Available Tools\n")
            for t in kernel:
                lines.append(f"- `{t['name']}`: {t.get('description', '')}")
        if discoverable:
            lines.append("\n## Discoverable Tools\n")
            lines.append("These tools are available on demand:\n")
            for t in discoverable:
                lines.append(f"- `{t['name']}`: {t.get('description', '')}")
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _build_promotion(adv: AdvancedConfig) -> Promotion | None:
        """Build Promotion gate, sharing permissions from guardrails.permissions.

        Promotion's deny/ask/allow lists are drawn from the unified permissions
        model (guardrails.permissions). The Promotion strategy controls *how*
        permission decisions are made ('auto' bypasses, 'ask' requires user
        input, 'plan' is read-only).
        """
        from arf.core.config_base import PromotionConfig, PermissionsConfig
        pc = adv.promotion or PromotionConfig()
        # Use unified permissions from guardrails as the single source of truth
        perms = adv.guardrails.permissions if adv.guardrails else PermissionsConfig()
        return Promotion(
            strategy=pc.strategy,
            deny=pc.deny or perms.deny,
            ask=pc.ask or perms.ask,
            allow=pc.allow or perms.allow,
            deny_patterns=pc.deny_patterns or perms.deny_patterns,
        )

    def _build_resource_resolver(self, config: AgentConfig, tool_provider, skill_provider,
                                   model_provider, tools_dir, skills_dir, models_dir,
                                   watch_enabled: bool, reload_cfg, override_protocols: dict[str, Any]):
        """Build ResourceResolver with override merge and optional FileWatcher."""
        from arf.resources.file_watcher import FileWatcher
        overrides = {
            "tools": [t.model_dump(exclude_none=True) for t in (config.tools or [])],
            "skills": [s.model_dump(exclude_none=True) for s in (config.skills or [])],
            "models": [m.model_dump(exclude_none=True) for m in (config.models or [])],
        }
        resource_resolver = override_protocols.pop("tool_resolver", ResourceResolver(
            tool_provider=tool_provider,
            skill_provider=skill_provider,
            model_provider=model_provider,
            agent_yaml_overrides=overrides,
        ))
        file_watcher = None
        if watch_enabled:
            poll_interval = reload_cfg.poll_interval if reload_cfg else 5.0
            file_watcher = FileWatcher(poll_interval=poll_interval)
            async def _on_fs_change(changed_paths):
                if hasattr(resource_resolver, "reload_dynamic"):
                    await resource_resolver.reload_dynamic()
            for d in [tools_dir, skills_dir, models_dir]:
                path = Path(d)
                if path.exists():
                    file_watcher.add_watch(path, _on_fs_change)
        return resource_resolver, file_watcher

    def _merge_models(self, config: AgentConfig, model_provider) -> None:
        """Merge filesystem models with agent.yaml overrides into config.models."""
        fs_models = model_provider.list()
        agent_models = {m.type: m for m in (config.models or [])}
        merged_models: list = []
        for fm in fs_models:
            if fm.type in agent_models:
                merged_models.append(
                    fm.model_copy(update=agent_models[fm.type].model_dump(exclude_none=True))
                )
            else:
                merged_models.append(fm)
        for t, am in agent_models.items():
            if not any(m.type == t for m in merged_models):
                merged_models.append(am)
        config.models = merged_models

    def _inject_model_calls(self, config) -> None:
        """Create ModelAdapter for each configured model and inject call_model into engine."""
        import os as _os, json as _json, asyncio as _asyncio
        from arf.core.model_adapter import ModelAdapter

        adapters: dict[str, ModelAdapter] = {}
        for m in config.models:
            api_key = _os.environ.get(m.api_key_env, "")
            adapter_cfg: dict[str, Any] = {
                "base_url": m.api_base,
                "api_key": api_key,
                "model_name": m.model,
                **m.kwargs,
            }
            if m.max_token is not None:
                adapter_cfg["max_tokens"] = m.max_token
            adapters[m.type] = ModelAdapter(adapter_cfg)
        default_name = config.models[0].type if config.models else ""

        # --- Protection layer (TODO #10) ---
        adv = config.effective_advanced()
        protector = None
        if adv and adv.protection and adv.protection.enabled:
            from arf.protection.protector import ModelCallProtector
            pc = adv.protection
            protector = ModelCallProtector(
                event_bus=self._event_bus,
                model_map={
                    m.type: {"base_url": m.api_base, "model_name": m.model}
                    for m in config.models
                },
                rate_limit_config={
                    "requests_per_second": pc.rate_limit.requests_per_second,
                    "max_burst": pc.rate_limit.max_burst,
                },
                breaker_config={
                    "failure_threshold": pc.circuit_breaker.failure_threshold,
                    "base_cooldown": pc.circuit_breaker.base_cooldown,
                    "cooldown_multiplier": pc.circuit_breaker.cooldown_multiplier,
                    "max_cooldown": pc.circuit_breaker.max_cooldown,
                    "half_open_max_requests": pc.circuit_breaker.half_open_max_requests,
                },
            )

        def _to_openai_tools(tools):
            """Convert framework ToolDefinition list to OpenAI tool format."""
            if not tools:
                return None
            result = []
            for t in tools:
                if isinstance(t, dict):
                    params = t.get("parameters", {})
                    result.append({
                        "type": "function",
                        "function": {
                            "name": t.get("name", ""),
                            "description": t.get("description", ""),
                            "parameters": params if params else {"type": "object", "properties": {}},
                        },
                    })
                else:
                    params = getattr(t, "parameters", {})
                    result.append({
                        "type": "function",
                        "function": {
                            "name": getattr(t, "name", ""),
                            "description": getattr(t, "description", ""),
                            "parameters": params if params else {"type": "object", "properties": {}},
                        },
                    })
            return result

        async def _call_model(messages: list[dict], model_name: str = "", tools=None) -> dict:
            adapter = adapters.get(model_name, adapters[default_name])
            msg = await adapter.chat_complete(messages, tools=_to_openai_tools(tools))
            tool_calls = []
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        params = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except _json.JSONDecodeError:
                        params = {}
                    tool_calls.append({"id": tc.id, "name": tc.function.name, "params": params})
            usage = {}
            if hasattr(msg, "usage") and msg.usage:
                usage = dict(msg.usage)
            reasoning = getattr(msg, "reasoning_content", None) or ""
            finish_reason = getattr(msg, "finish_reason", "stop")
            return {"content": msg.content or "", "tool_calls": tool_calls, "usage": usage, "reasoning": reasoning, "finish_reason": finish_reason}

        async def _stream_model(messages: list[dict], model_name: str = "", tools=None):
            """Token-level streaming via ModelAdapter.chat_stream_full."""
            adapter = adapters.get(model_name, adapters[default_name])
            async for chunk in adapter.chat_stream_full(messages, tools=_to_openai_tools(tools)):
                yield chunk

        # --- Apply protection wrapper ---
        if protector:
            raw_call = _call_model
            raw_stream = _stream_model
            engine_ref = self._engine

            async def _on_400_repair(exc, messages):
                """Try to repair messages on 400 tool errors. Returns repaired list or None."""
                detail = str(exc)
                if "400" not in detail or "tool" not in detail.lower():
                    return None
                # The engine repairs its own internal state; we rebuild externally
                import logging
                logger = logging.getLogger("arf.agent")
                logger.info("Protection: 400 tool error detected, attempting repair")
                return None  # engine handles repair internally in model_call step

            async def _protected_call(messages, model_name="", tools=None):
                return await protector.call_with_protection(
                    raw_call, messages, model_name, tools=tools,
                    on_400=_on_400_repair,
                )

            async def _protected_stream(messages, model_name="", tools=None):
                async for chunk in protector.stream_with_protection(
                    raw_stream, messages, model_name, tools=tools,
                ):
                    yield chunk

            self._engine.set_call_model(_protected_call)
            self._engine.set_stream_model(_protected_stream)
        else:
            self._engine.set_call_model(_call_model)
            self._engine.set_stream_model(_stream_model)

    @property
    def state_store(self):
        return self._state_store

    @property
    def event_bus(self):
        return self._event_bus

    @property
    def memory_store(self):
        return self._memory_store

    @property
    def tool_resolver(self):
        return self._tool_resolver

    @property
    def resource_resolver(self):
        """Alias for tool_resolver — handles tools, skills, and models."""
        return self._tool_resolver

    @property
    def engine(self):
        """GraphEngine — for cancel, undo, checkpoint introspection."""
        return self._engine

    @property
    def usage_tracker(self):
        return self._usage_tracker

    @property
    def trace_store(self):
        """FileTraceStore — auto-created by BaseAgent."""
        return self._trace_store

    @property
    def sub_agent_configs(self) -> dict:
        """Return {agent_name: {config, system_prompt, adapters}} for all sub-agents."""
        return self._sub_agent_configs

    async def start(self) -> None:
        """Start the FileWatcher (called once event loop is ready)."""
        if self._file_watcher:
            import asyncio
            asyncio.create_task(self._file_watcher.start())

    async def stop(self) -> None:
        """Stop the FileWatcher and close all active sessions."""
        if self._file_watcher:
            await self._file_watcher.stop()

        for sid in list(self._active_sessions):
            try:
                state = await self._state_store.get(sid)
                if state:
                    state["session_active"] = False
                    await self._state_store.put(sid, state)
                if self._engine.hook_runner:
                    await self._engine.hook_runner.fire("session_end", {
                        "session_id": sid,
                        "reason": "shutdown",
                    })
            except Exception:
                pass
        self._active_sessions.clear()

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        from arf.core.state import AgentState
        existing = await self._state_store.get(session_id)
        if existing:
            existing = self._engine._close_tool_calls(existing)
            await self._state_store.put(session_id, existing)

        # Determine new session and crash recovery
        if session_id in self._active_sessions:
            # Already started, just continuing
            is_new_session = False
        elif existing and existing.get("session_active"):
            # Found active state on disk but this agent hasn't tracked it — crash recovery
            is_new_session = True
            if self._engine.hook_runner:
                await self._engine.hook_runner.fire("session_end", {
                    "session_id": session_id,
                    "reason": "recovery",
                })
        else:
            # New session (no state, or state without active flag)
            is_new_session = True

        turn = 0  # reset per round; max_turns is a per-round circuit breaker
        if existing:
            messages = existing["messages"] + [{"role": "user", "content": user_message}]
            summary = existing.get("context_summary", "")
            interaction = existing.get("interaction_round", 0) + 1
        else:
            messages = [{"role": "user", "content": user_message}]
            summary = ""
            interaction = 0

        # Route to currently active agent if handoff is in progress
        active_agent = existing.get("active_agent", "") if existing else ""
        if active_agent and active_agent in getattr(self._engine, '_sub_agent_configs', {}):
            agent_name = active_agent
        else:
            agent_name = self.config.name

        state: AgentState = {
            "session_id": session_id,
            "agent_name": agent_name,
            "active_agent": active_agent,
            "messages": messages,
            "current_model": self.config.models[0].type if self.config.models else "default",
            "current_turn": turn,
            "interaction_round": interaction,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
            "session_active": True,
        }

        self._engine._rounds.begin_round(state)
        self._active_sessions.add(session_id)

        # Restore sub-agent permissions if continuing a handoff
        if active_agent and active_agent in getattr(self._engine, '_sub_agent_configs', {}):
            self._engine._activate_agent(active_agent)

        if self._engine.hook_runner:
            self._engine.hook_runner.update_runtime(session_id=session_id, interaction_round=interaction)

        if is_new_session and self._engine.hook_runner:
            await self._engine.hook_runner.fire("session_start", {
                "session_id": session_id,
            })

        if self._engine.hook_runner:
            await self._engine.hook_runner.fire("round_start", {
                "session_id": session_id,
                "round": interaction,
            })

        result = await self._engine.invoke(state)
        for m in reversed(result.get("messages", [])):
            if m.get("role") == "assistant":
                return m.get("content", "")
        return ""

    async def astream(self, user_message: str, session_id: str = "default"):
        from arf.core.state import AgentState
        existing = await self._state_store.get(session_id)
        if existing:
            existing = self._engine._close_tool_calls(existing)
            await self._state_store.put(session_id, existing)

        # Determine new session and crash recovery
        if session_id in self._active_sessions:
            # Already started, just continuing
            is_new_session = False
        elif existing and existing.get("session_active"):
            # Found active state on disk but this agent hasn't tracked it — crash recovery
            is_new_session = True
            if self._engine.hook_runner:
                await self._engine.hook_runner.fire("session_end", {
                    "session_id": session_id,
                    "reason": "recovery",
                })
        else:
            # New session (no state, or state without active flag)
            is_new_session = True

        turn = 0  # reset per round; max_turns is a per-round circuit breaker
        if existing:
            messages = existing["messages"] + [{"role": "user", "content": user_message}]
            summary = existing.get("context_summary", "")
            interaction = existing.get("interaction_round", 0) + 1
        else:
            messages = [{"role": "user", "content": user_message}]
            summary = ""
            interaction = 0

        # Route to currently active agent if handoff is in progress
        active_agent = existing.get("active_agent", "") if existing else ""
        if active_agent and active_agent in getattr(self._engine, '_sub_agent_configs', {}):
            agent_name = active_agent
        else:
            agent_name = self.config.name

        state: AgentState = {
            "session_id": session_id,
            "agent_name": agent_name,
            "active_agent": active_agent,
            "messages": messages,
            "current_model": self.config.models[0].type if self.config.models else "default",
            "current_turn": turn,
            "interaction_round": interaction,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
            "session_active": True,
        }

        self._engine._rounds.begin_round(state)
        self._active_sessions.add(session_id)

        # Restore sub-agent permissions if continuing a handoff
        if active_agent and active_agent in getattr(self._engine, '_sub_agent_configs', {}):
            self._engine._activate_agent(active_agent)

        if self._engine.hook_runner:
            self._engine.hook_runner.update_runtime(session_id=session_id, interaction_round=interaction)

        if is_new_session and self._engine.hook_runner:
            await self._engine.hook_runner.fire("session_start", {
                "session_id": session_id,
            })

        if self._engine.hook_runner:
            await self._engine.hook_runner.fire("round_start", {
                "session_id": session_id,
                "round": interaction,
            })

        async for event in self._engine.astream(state):
            yield event

    def reconfigure(self, **overrides) -> None:
        if "advanced" in overrides:
            new_data = self.config.model_dump()
            new_data["advanced"] = overrides["advanced"]
            self.config = AgentConfig(**new_data)

    async def evaluate(self, benchmark):
        """Run an EvalBenchmark against this agent, returning EvalReport."""
        from arf.evaluation.runner import EvalRunner
        runner = EvalRunner(self, self._event_bus)
        return await runner.run(benchmark)
