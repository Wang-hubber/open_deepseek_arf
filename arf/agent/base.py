"""BaseAgent — assembles all Protocol implementations into a running Agent."""
from pathlib import Path
from typing import Any
from arf.agent.config import AgentConfig, AdvancedConfig
from arf.agent.app_context import AppContext
from arf.engine.control_plane import ControlPlane
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
            "tools_dir", ctx.tools_dir if ctx else Path.cwd() / "tools"
        )
        skills_dir = override_protocols.pop(
            "skills_dir", ctx.skills_dir if ctx else Path.cwd() / "skills"
        )
        models_dir = override_protocols.pop(
            "models_dir", ctx.models_dir if ctx else Path.cwd() / "models"
        )

        # Models — resolved from config.model_defs (new format) or config.models (legacy)
        # ModelProvider (filesystem scanning) has been removed.

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
                plugin_configs=config.plugins_config,
            ),
        )
        self._mcp_manager = mcp_manager

        # MCP tool resolver wrapper for ControlPlane
        async def _mcp_tool_resolver(state):
            """Resolve tool definitions from MCP for ControlPlane dispatch."""
            try:
                tools = await mcp_manager.get_tool_definitions(query_context="", top_k=50)
                return [
                    {
                        "name": t.name if hasattr(t, "name") else t.get("name", ""),
                        "description": t.description if hasattr(t, "description") else t.get("description", ""),
                        "parameters": t.parameters if hasattr(t, "parameters") else t.get("parameters", {}),
                    }
                    for t in (tools or [])
                ]
            except Exception:
                return []

        # Plugin system — for hooks only (tools/skills via MCP)
        self._plugin_provider = None
        if config.plugins:
            from arf.resources.providers.plugin_provider import PluginProvider
            self._plugin_provider = PluginProvider(_plugins_dir, config.plugins,
                                                   config.plugins_config)

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
            system_model="quick",
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

        # System model adapter — removed (each Plugin now configures its own model).
        _system_model_call = None

        # Memory extraction moved to arf/plugins/memory/ plugin.
        # Framework no longer constructs or holds a writer/retriever.
        memory_writer = override_protocols.pop("memory_writer", None)
        memory_retriever = override_protocols.pop("memory_retriever", None)

        # Compaction — moved to CompactionPlugin (round_end hook).
        # Framework no longer constructs SlidingWindowCompactor inline.
        compaction = override_protocols.pop("compaction", None)

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
            default_boundary = None
            tool_boundaries: dict = {}
        else:
            sandbox_cfg = adv.sandbox if adv else None
            checks = sandbox_cfg.checks.model_dump() if sandbox_cfg and sandbox_cfg.checks else None
            tool_guard = PathCheckToolGuard(checks=checks)

            # Build per-tool directory boundaries from tool.yaml allowed_dir
            from arf.sandbox.directory_boundary import DirectoryBoundary
            default_boundary = DirectoryBoundary(_workspace_root)
            tool_boundaries: dict[str, DirectoryBoundary] = {}
            all_tool_defs = []
            try:
                all_tool_defs = mcp_manager.get_tool_definitions_sync()
            except Exception:
                pass
            for tdef in all_tool_defs:
                allowed_dir = getattr(tdef, 'allowed_dir', None)
                name = getattr(tdef, 'name', '')
                if allowed_dir and name:
                    tool_boundaries[name] = DirectoryBoundary(allowed_dir)
        # SandboxManager — session-level isolation
        from arf.sandbox.sandbox_manager import SandboxManager
        sandbox_cfg = adv.sandbox if adv else None
        sandbox_manager = SandboxManager(
            workspace_root=_workspace_root,
            blacklist=(sandbox_cfg.blacklist if sandbox_cfg else None),
            auto_destroy=(sandbox_cfg.auto_destroy if sandbox_cfg else False),
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
                tool_boundaries=tool_boundaries,
                default_boundary=default_boundary,
                sandbox_manager=sandbox_manager,
            ),
        )

        # 8. Loop strategy
        ls_name = (adv.loop_strategy if adv else "react") if adv else "react"
        loop_strategy = override_protocols.pop("loop_strategy", ReActStrategy(max_turns=(adv.max_turns if adv else 50)))

        # 9. Planner (optional)
        planner = override_protocols.pop("planner", None)

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

        # Model routing — deprecated TwoTierRouter removed.
        # Use model_defs + agent_models degradation instead.


        # --- Resolve tool names → namespaced names for permission lists ---
        # Collect base_name → [namespaced_name] mapping from local sources.
        _name_map: dict[str, list[str]] = {}
        for t in config.tools:
            ns = f"arf__{t.name}"
            _name_map.setdefault(t.name, []).append(ns)
        if self._plugin_provider:
            for t in self._plugin_provider.list_tools():
                ns = f"arf__{t.name}"
                _name_map.setdefault(t.name, []).append(ns)
        for srv in getattr(config, "mcp_servers", []):
            _name_map.setdefault("", [])  # sentinel: external MCP namespace exists

        def _resolve_perm_name(name: str) -> str:
            """Resolve a permission-list name to a namespaced tool name."""
            if "__" in name:
                return name  # already namespaced
            matches = _name_map.get(name, [])
            if len(matches) > 1:
                raise ValueError(
                    f"Tool '{name}' is ambiguous — it exists in multiple sources: "
                    f"{matches}. Use the full namespaced name (e.g. arf__{name}) "
                    f"in your permission lists."
                )
            if matches:
                return matches[0]
            return f"arf__{name}"  # unknown source, assume local

        _namespaced_deny = [_resolve_perm_name(t) for t in main_permission_lists.deny]
        _namespaced_ask = [_resolve_perm_name(t) for t in main_permission_lists.ask]
        _namespaced_allow = [_resolve_perm_name(t) for t in main_permission_lists.allow]

        # Merge deny_patterns from two config sources:
        # 1. permissions.deny_patterns — string patterns from PermissionsConfig
        # 2. content_guard.dangerous_patterns — structured rules from ContentGuardConfig
        _all_deny_patterns = list(main_permission_lists.deny_patterns)
        if gr_cfg and gr_cfg.content_guard:
            for dp in gr_cfg.content_guard.dangerous_patterns:
                _all_deny_patterns.append(dp.pattern)

        # Blocking plugins run in registration order:
        # 1. ToolGuardPlugin — deny (immediate rejection) + allow (bypass)
        # 2. ApprovalPlugin — ask (human-in-the-loop wait)
        blocking_plugins: list = []
        if _namespaced_deny or _namespaced_allow or _all_deny_patterns:
            from arf.plugins.tool_guard.plugin import ToolGuardPlugin
            blocking_plugins.append(ToolGuardPlugin({
                "deny": _namespaced_deny,
                "allow": _namespaced_allow,
                "deny_patterns": _all_deny_patterns,
                "sandbox_check": False,  # PathCheckToolGuard handles this
            }))
        if _namespaced_ask:
            from arf.plugins.approval.plugin import ApprovalPlugin
            blocking_plugins.append(ApprovalPlugin({"ask_list": _namespaced_ask}))

        # Auto-discovered framework plugins (blocking + side).
        # tool_guard and approval are constructed separately with merged
        # permission config — skip their auto-discovered instances.
        _SPECIAL_PLUGINS = {"tool_guard", "approval"}
        _obs_cfg = adv.observability if adv else None
        side_plugins: list = []
        if self._plugin_provider:
            for p in self._plugin_provider.list_plugins():
                if p.name in _SPECIAL_PLUGINS:
                    continue
                has_side = any(m == "side" for m in p.hooks.values())
                has_blocking = any(m == "blocking" for m in p.hooks.values())
                if has_blocking:
                    blocking_plugins.append(p)
                if has_side and not has_blocking:
                    side_plugins.append(p)
                if hasattr(p, "set_state_store"):
                    p.set_state_store(state_store)

        self._engine = ControlPlane(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_executor=tool_executor,
            event_bus=event_bus,
            blocking_plugins=blocking_plugins,
            side_plugins=side_plugins,
            call_model=None,
            stream_model=None,
            cancel_event=None,
            system_prompt=system_prompt,
            max_turns=(adv.max_turns if adv else 50),
            workspace_dir=str(ctx.workspace_dir) if ctx else "./workspace",
            memory_dir=_mem_dir,
            state_dir=str(ctx.state_dir) if ctx else "./data/state",
            trace_dir=_trace_dir,
            mcp_tool_resolver=_mcp_tool_resolver,
        )
        self._hook_runner = hook_runner
        self._state_store = state_store
        self._event_bus = event_bus
        self._memory_store = memory_store
        self._tool_resolver = mcp_manager

        # Auto-create trace store (framework default)
        trace_dir = str(ctx.trace_dir) if ctx else (_obs_cfg.trace_dir if _obs_cfg else "./data/traces")
        from arf.observability import FileTraceStore
        self._trace_store = FileTraceStore(event_bus, dir=trace_dir)

        # ---- Auto-inject model API call ----
        self._inject_model_calls(config)

        # ---- Active session tracking ----
        self._active_sessions: set[str] = set()

    async def _resolve_session(self, session_id: str) -> tuple[str, dict | None, bool]:
        """Resolve session_id, auto-generating UUID if configured.

        Returns (resolved_session_id, existing_state, is_new_session).
        """
        adv = self.config.effective_advanced()
        sess_cfg = adv.session if adv else None

        if not session_id or session_id.strip() == "":
            if sess_cfg and sess_cfg.enabled and sess_cfg.generate_id:
                import uuid
                session_id = str(uuid.uuid4())
            else:
                session_id = "default"

        existing = await self._state_store.get(session_id)

        if session_id in self._active_sessions:
            is_new_session = False
        elif existing and existing.get("session_active"):
            is_new_session = True
            if self._hook_runner:
                await self._hook_runner.fire("session_end", {
                    "session_id": session_id,
                    "reason": "recovery",
                })
        else:
            is_new_session = True

        return session_id, existing, is_new_session

    def _build_inventory_from_mcp(self) -> str:
        """Build inventory section from MCP tool list. Called at startup.

        Returns empty string if MCP is not available yet. This runs during
        __init__ before the event loop is ready — the real MCP connection
        happens in start(). Check _mcp_manager.healthy after start().
        """
        try:
            tools = self._mcp_manager.get_tool_definitions_sync()
        except Exception:
            return ""
        lines: list[str] = []
        if tools:
            lines.append("## Available Tools\n")
            for t in tools:
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
                                   tools_dir, skills_dir, models_dir,
                                   watch_enabled: bool, reload_cfg, override_protocols: dict[str, Any]):
        """Build ResourceResolver with override merge and optional FileWatcher."""
        from arf.resources.file_watcher import FileWatcher
        overrides = {
            "tools": [t.model_dump(exclude_none=True) for t in (config.tools or [])],
            "skills": [s.model_dump(exclude_none=True) for s in (config.skills or [])],
        }
        resource_resolver = override_protocols.pop("tool_resolver", ResourceResolver(
            tool_provider=tool_provider,
            skill_provider=skill_provider,
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

    def _inject_model_calls(self, config) -> None:
        """Create ModelAdapter for each configured model and inject call_model into engine."""
        import os as _os, json as _json, asyncio as _asyncio
        from arf.core.model_adapter import ModelAdapter

        # Build ModelDegrader from new format (model_defs) or legacy (config.models)
        _model_degrader = None
        model_registry = config.get_model_registry()
        if model_registry is not None:
            model_registry.validate()
            agent_models = config.get_agent_model_configs()
            if agent_models:
                _deg_adapters = []
                for mcfg in agent_models:
                    _api_key = _os.environ.get(mcfg.api_key_env, "")
                    _deg_adapters.append(ModelAdapter({
                        "base_url": mcfg.api_base,
                        "api_key": _api_key,
                        "model_name": mcfg.model,
                        **mcfg.kwargs,
                    }))
                from arf.core.model_degrader import ModelDegrader
                _model_degrader = ModelDegrader(_deg_adapters)

        if _model_degrader is None and config.models:
            from arf.core.model_degrader import ModelDegrader
            _deg_adapters = []
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
                _deg_adapters.append(ModelAdapter(adapter_cfg))
            _model_degrader = ModelDegrader(_deg_adapters)

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
            msg = await _model_degrader.chat_complete(
                messages, tools=_to_openai_tools(tools))
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
            """Token-level streaming via ModelDegrader."""
            async for chunk in _model_degrader.chat_stream_full(
                messages, tools=_to_openai_tools(tools)):
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
        """ControlPlane — execution loop for invoke/astream."""
        return self._engine

    @property
    def trace_store(self):
        """FileTraceStore — auto-created by BaseAgent."""
        return self._trace_store

    async def start(self) -> None:
        """Start FileWatcher and MCP manager (called once event loop is ready)."""
        if self._file_watcher:
            import asyncio
            asyncio.create_task(self._file_watcher.start())
        if self._mcp_manager:
            await self._mcp_manager.start()

    async def stop(self) -> None:
        """Stop the FileWatcher, MCP manager, and close all active sessions."""
        if self._file_watcher:
            await self._file_watcher.stop()
        if self._mcp_manager:
            await self._mcp_manager.stop()

        for sid in list(self._active_sessions):
            try:
                state = await self._state_store.get(sid)
                if state:
                    state["session_active"] = False
                    await self._state_store.put(sid, state)
                if self._hook_runner:
                    await self._hook_runner.fire("session_end", {
                        "session_id": sid,
                        "reason": "shutdown",
                    })
            except Exception:
                pass
        self._active_sessions.clear()

    def approve(self, decision_id: str, approved: bool = True) -> bool:
        """Delegate approval to the ApprovalPlugin (if registered)."""
        plugin = self._engine._blocking.get_plugin("approval")
        if plugin is None:
            return False
        return plugin.approve(decision_id, approved)

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        from arf.core.state import AgentState
        session_id, existing, is_new_session = await self._resolve_session(session_id)

        turn = 0  # reset per round; max_turns is a per-round circuit breaker
        if existing:
            messages = existing["messages"] + [{"role": "user", "content": user_message}]
            summary = existing.get("context_summary", "")
            interaction = existing.get("interaction_round", 0) + 1
        else:
            messages = [{"role": "user", "content": user_message}]
            summary = ""
            interaction = 0

        agent_name = self.config.name

        state: AgentState = {
            "session_id": session_id,
            "agent_name": agent_name,
            "messages": messages,
            "current_model": self.config.models[0].type if self.config.models else "default",
            "current_turn": turn,
            "interaction_round": interaction,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
            "session_active": True,
            "session_title": existing.get("session_title", "") if existing else "",
        }

        self._active_sessions.add(session_id)

        if self._hook_runner:
            self._hook_runner.update_runtime(session_id=session_id, interaction_round=interaction)

        if is_new_session and self._hook_runner:
            await self._hook_runner.fire("session_start", {
                "session_id": session_id,
            })

        if self._hook_runner:
            await self._hook_runner.fire("round_start", {
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
        session_id, existing, is_new_session = await self._resolve_session(session_id)

        turn = 0  # reset per round; max_turns is a per-round circuit breaker
        if existing:
            messages = existing["messages"] + [{"role": "user", "content": user_message}]
            summary = existing.get("context_summary", "")
            interaction = existing.get("interaction_round", 0) + 1
        else:
            messages = [{"role": "user", "content": user_message}]
            summary = ""
            interaction = 0

        agent_name = self.config.name

        state: AgentState = {
            "session_id": session_id,
            "agent_name": agent_name,
            "messages": messages,
            "current_model": self.config.models[0].type if self.config.models else "default",
            "current_turn": turn,
            "interaction_round": interaction,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
            "session_active": True,
            "session_title": existing.get("session_title", "") if existing else "",
        }

        self._active_sessions.add(session_id)

        if self._hook_runner:
            self._hook_runner.update_runtime(session_id=session_id, interaction_round=interaction)

        if is_new_session and self._hook_runner:
            await self._hook_runner.fire("session_start", {
                "session_id": session_id,
            })

        if self._hook_runner:
            await self._hook_runner.fire("round_start", {
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
