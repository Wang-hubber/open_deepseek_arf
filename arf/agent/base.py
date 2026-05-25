"""BaseAgent — assembles all Protocol implementations into a running Agent."""
from pathlib import Path
from arf.agent.config import AgentConfig, AdvancedConfig
from arf.agent.app_context import AppContext
from arf.engine.graph import GraphEngine
from arf.engine.loop_strategies.react import ReActStrategy
from arf.engine.checkpoint import InMemoryStateStore, FileStateStore
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.engine.loop_strategies.planner import PromptBasedPlanner
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
from arf.errors.transaction import SnapshotRollback


def _build_system_prompt(config: AgentConfig) -> str:
    """Build system prompt from AgentConfig.system_prompt template.

    Supports placeholders filled by engine at runtime:
      {{AGENT_NAME}}      → config.name
      {{AGENT_ROLE}}      → config.role
      {{AGENT_TASK}}      → config.task
      {{CRITICAL_RULES}}  → config.system_prompt.critical_rules
      {{INVENTORY}}       → kernel tools + skills (progressive disclosure)
    Falls back to auto-generated prompt if no template provided.
    """
    sp = config.system_prompt
    template = sp.template.strip()
    if not template:
        # Fallback for configs without explicit system_prompt
        lines = [f"You are {config.name}, an AI assistant."]
        if config.role:
            lines.append(f"Role: {config.role}")
        if config.task:
            lines.append(f"Task: {config.task}")
        if config.description:
            lines.append(f"\n## Capabilities\n{config.description}")
        template = "\n".join(lines) + "\n\n"

    # Build inventory: kernel tools + discoverable skills (name + one-line desc)
    kernel_tools = [t for t in config.tools if getattr(t, "activation", "discoverable") == "kernel"]
    skills = config.skills

    inv_lines = []
    if kernel_tools:
        inv_lines.append("## Available Tools\n")
        for t in kernel_tools:
            inv_lines.append(f"- `{t.name}`: {t.description}")
    if skills:
        inv_lines.append("\n## Available Skills\n")
        inv_lines.append("Skills are loaded on demand. Read a skill's full instructions via `file_reader`:\n")
        for s in skills:
            inv_lines.append(f"- `{s.name}`: {s.description or '(no description)'}  → read `skills/{s.name}.yaml`")

    inventory = "\n".join(inv_lines) if inv_lines else ""

    prompt = template
    prompt = prompt.replace("{{AGENT_NAME}}", config.name)
    prompt = prompt.replace("{{AGENT_ROLE}}", config.role or "")
    prompt = prompt.replace("{{AGENT_TASK}}", config.task or "")
    prompt = prompt.replace("{{CRITICAL_RULES}}", sp.critical_rules or "")
    prompt = prompt.replace("{{INVENTORY}}", inventory)

    return prompt


class BaseAgent:
    def __init__(self, config: AgentConfig, app_context: AppContext | None = None, **override_protocols) -> None:
        self.config = config
        adv = config.effective_advanced()
        ctx = app_context

        # 1. Core infrastructure
        event_bus = override_protocols.pop("event_bus", InMemoryEventBus())
        default_state_dir = str(ctx.state_dir) if ctx else "./memory/state"
        state_store = override_protocols.pop("state_store", FileStateStore(default_state_dir))

        # 2. Resources — from AppContext if provided, otherwise defaults
        tools_dir = override_protocols.pop("tools_dir", ctx.tools_dir if ctx else Path("./tools"))
        skills_dir = override_protocols.pop("skills_dir", ctx.skills_dir if ctx else Path("./skills"))
        models_dir = override_protocols.pop("models_dir", ctx.models_dir if ctx else Path("./models"))
        reload_cfg = adv.reload if adv else None
        watch_enabled = override_protocols.pop("watch_enabled",
            reload_cfg.watch if reload_cfg else True)

        from arf.resources.providers.skill_provider import SkillProvider
        from arf.resources.providers.model_provider import ModelProvider
        from arf.resources.file_watcher import FileWatcher

        tool_provider = ToolProvider(tools_dir)
        skill_provider = SkillProvider(skills_dir)
        model_provider = ModelProvider(models_dir)

        # Merge filesystem models with agent.yaml overrides (filesystem is base).
        # This gives init-phase code (adapters, system model, router) the full
        # model list without depending on ResourceResolver's lazy merge-on-read.
        fs_models = model_provider.list()
        agent_models = {m.name: m for m in (config.models or [])}
        merged_models: list = []
        for fm in fs_models:
            if fm.name in agent_models:
                merged_models.append(
                    fm.model_copy(update=agent_models[fm.name].model_dump(exclude_none=True))
                )
            else:
                merged_models.append(fm)
        for name, am in agent_models.items():
            if not any(m.name == name for m in merged_models):
                merged_models.append(am)
        config.models = merged_models

        # Build override dict from agent.yaml for merge-on-read
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

        # FileWatcher
        file_watcher = None
        if watch_enabled:
            poll_interval = float(reload_cfg.poll_interval) if reload_cfg else 5.0
            file_watcher = FileWatcher(poll_interval=poll_interval)

            async def _on_fs_change(changed_paths):
                if hasattr(resource_resolver, "reload_dynamic"):
                    await resource_resolver.reload_dynamic()

            for d in [tools_dir, skills_dir, models_dir]:
                path = Path(d)
                if path.exists():
                    file_watcher.add_watch(path, _on_fs_change)

        self._file_watcher = file_watcher
        self._resource_resolver = resource_resolver

        # 3. Memory — LLM-driven by default, falls back to rule-based
        mem_cfg = (adv.memory or AdvancedConfig.default().memory) if adv else AdvancedConfig.default().memory
        default_workspace = str(ctx.workspace_dir) if ctx else "./memory"
        mem_workspace = mem_cfg.workspace if mem_cfg else default_workspace
        memory_store = override_protocols.pop("memory_store", FileMemoryStore(mem_workspace))

        # Build a dedicated cheap model adapter for system background tasks
        _system_model_call = None
        if mem_cfg and mem_cfg.writer == "llm":
            import os as _os2, asyncio as _aio2
            from arf.core.model_adapter import ModelAdapter as _SystemAdapter
            system_model_cfg = next(
                (m for m in config.models if m.name == mem_cfg.model),
                config.models[0] if config.models else None,
            )
            if system_model_cfg:
                _system_adapter = _SystemAdapter({
                    "base_url": system_model_cfg.api_base,
                    "api_key": _os2.environ.get(system_model_cfg.api_key_env, ""),
                    "model_name": system_model_cfg.model,
                    "temperature": mem_cfg.temperature,
                    "thinking_enabled": str(mem_cfg.thinking_enabled).lower(),
                    "max_tokens": 1024,
                })

                async def _system_model_call(prompt: str) -> str:
                    """Call the system model with a simple prompt, return text content."""
                    msg = await _aio2.to_thread(
                        _system_adapter.chat_complete,
                        [{"role": "user", "content": prompt}],
                        tools=None,
                        max_tokens=1024,
                    )
                    return msg.content or ""

        if mem_cfg and mem_cfg.writer == "llm" and _system_model_call:
            from arf.memory.llm_writer import LLMMemoryWriter
            memory_writer = override_protocols.pop("memory_writer", LLMMemoryWriter(_system_model_call))
        else:
            memory_writer = override_protocols.pop("memory_writer", RuleBasedMemoryWriter())

        if mem_cfg and mem_cfg.retriever == "llm" and _system_model_call:
            from arf.memory.llm_retriever import LLMMemoryRetriever
            memory_retriever = override_protocols.pop("memory_retriever", LLMMemoryRetriever(_system_model_call))
        else:
            memory_retriever = override_protocols.pop("memory_retriever", RecentFirstRetriever())

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
        _workspace_root = str(tools_dir.parent.resolve())
        gr_cfg = adv.guardrails if adv else None
        if gr_cfg and gr_cfg.input == "none":
            input_guard = NoneInputGuard()
        else:
            input_guard = NoneInputGuard()  # only "none" implemented currently
        if gr_cfg and gr_cfg.output == "none":
            output_guard = None
        else:
            output_guard = RegexOutputGuard()  # default and only implemented option
        if gr_cfg and gr_cfg.tool_params == "none":
            tool_guard = None
        else:
            tool_guard = PathCheckToolGuard(workspace_root=_workspace_root)  # default, only implemented
        guard_runner = override_protocols.pop("guard_runner", DefaultGuardRunner(
            input_guard=input_guard,
            output_guard=output_guard,
            tool_guard=tool_guard,
        ))

        # 5. Error + Transaction
        err_cfg = (adv.errors or AdvancedConfig.default().errors) if adv else None
        tool_retry = err_cfg.tool_retry if err_cfg else 2
        model_retry = err_cfg.model_retry if err_cfg else 3
        error_policy = override_protocols.pop("error_policy", DefaultErrorPolicy(
            tool_retry=tool_retry, model_retry=model_retry,
        ))
        transaction_ctx = override_protocols.pop("transaction_ctx", SnapshotRollback())

        # 6. Hooks
        hooks_list = override_protocols.pop("hooks", config.hooks)
        hook_runner = override_protocols.pop("hook_runner", SubprocessHookRunner(hooks_list))

        # 7. Tool executor
        tool_executor = override_protocols.pop("tool_executor", ConcurrentToolExecutor(resource_resolver))

        # 8. Loop strategy
        ls_name = (adv.loop_strategy if adv else "react") if adv else "react"
        loop_strategy = override_protocols.pop("loop_strategy", ReActStrategy(max_turns=(adv.max_turns if adv else 50)))

        # 9. Planner (optional)
        planner = override_protocols.pop("planner", None)

        # 10. Build engine
        system_prompt = _build_system_prompt(config)

        # Auto-create model router if routing config is set (LLM classifier by default)
        model_router = None
        if adv and adv.routing and len(config.models) > 1:
            from arf.routing.two_tier import TwoTierRouter
            if _system_model_call:
                async def _classify(query: str) -> str:
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
                    models=[m.name for m in config.models],
                    classifier_call=_classify,
                )
            else:
                model_router = TwoTierRouter(
                    config=adv.routing,
                    models=[m.name for m in config.models],
                )

        self._engine = GraphEngine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_executor=tool_executor,
            tool_resolver=resource_resolver,
            transaction_ctx=transaction_ctx,
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
            system_prompt=system_prompt,
            max_turns=(adv.max_turns if adv else 50),
            **override_protocols,
        )
        # Pass model context windows to engine for compaction decisions
        self._engine.set_model_windows(
            {m.name: m.context_window for m in config.models}
        )
        self._state_store = state_store
        self._event_bus = event_bus
        self._memory_store = memory_store
        self._tool_resolver = resource_resolver
        # Auto-create usage tracker (framework default)
        from arf.observability.usage_tracker import UsageTracker
        self._usage_tracker = UsageTracker(event_bus)

        # ---- Auto-inject model API call ----
        self._inject_model_calls(config)

    def _inject_model_calls(self, config) -> None:
        """Create ModelAdapter for each configured model and inject call_model into engine."""
        import os as _os, json as _json, asyncio as _asyncio
        from arf.core.model_adapter import ModelAdapter

        adapters: dict[str, ModelAdapter] = {}
        for m in config.models:
            api_key = _os.environ.get(m.api_key_env, "")
            adapters[m.name] = ModelAdapter({
                "base_url": m.api_base,
                "api_key": api_key,
                "model_name": m.model,
                **m.kwargs,
            })
        default_name = config.models[0].name if config.models else ""

        def _to_openai_tools(tools):
            """Convert framework ToolDefinition list to OpenAI tool format."""
            if not tools:
                return None
            return [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        async def _call_model(messages: list[dict], model_name: str = "", tools=None) -> dict:
            adapter = adapters.get(model_name, adapters[default_name])
            msg = await _asyncio.to_thread(
                adapter.chat_complete, messages, tools=_to_openai_tools(tools),
            )
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
            return {"content": msg.content or "", "tool_calls": tool_calls, "usage": usage, "reasoning": reasoning}

        async def _stream_model(messages: list[dict], model_name: str = "", tools=None):
            """Token-level streaming via ModelAdapter.chat_stream_full."""
            import asyncio as _asyncio_stream
            adapter = adapters.get(model_name, adapters[default_name])
            for chunk in adapter.chat_stream_full(messages, tools=_to_openai_tools(tools)):
                yield chunk
                await _asyncio_stream.sleep(0)  # yield to event loop so ASGI can flush

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

    async def start(self) -> None:
        """Start the FileWatcher (called once event loop is ready)."""
        if self._file_watcher:
            import asyncio
            asyncio.create_task(self._file_watcher.start())

    async def stop(self) -> None:
        """Stop the FileWatcher (called on shutdown)."""
        if self._file_watcher:
            await self._file_watcher.stop()

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        from arf.core.state import AgentState
        # Load existing state to preserve conversation history
        existing = await self._state_store.get(session_id)
        if existing:
            messages = existing.get("messages", []) + [{"role": "user", "content": user_message}]
            turn = existing.get("current_turn", 0)
            summary = existing.get("context_summary", "")
            interaction = existing.get("interaction_round", 0) + 1
        else:
            messages = [{"role": "user", "content": user_message}]
            turn = 0
            summary = ""
            interaction = 0
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": messages,
            "current_model": self.config.models[0].name if self.config.models else "default",
            "current_turn": turn,
            "interaction_round": interaction,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }
        # Push checkpoint before engine execution (for undo)
        self._engine.push_checkpoint(state)
        result = await self._engine.invoke(state)
        for m in reversed(result.get("messages", [])):
            if m.get("role") == "assistant":
                return m.get("content", "")
        return ""

    async def astream(self, user_message: str, session_id: str = "default"):
        from arf.core.state import AgentState
        existing = await self._state_store.get(session_id)
        if existing:
            messages = existing.get("messages", []) + [{"role": "user", "content": user_message}]
            turn = existing.get("current_turn", 0)
            summary = existing.get("context_summary", "")
            interaction = existing.get("interaction_round", 0) + 1
        else:
            messages = [{"role": "user", "content": user_message}]
            turn = 0
            summary = ""
            interaction = 0
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": messages,
            "current_model": self.config.models[0].name if self.config.models else "default",
            "current_turn": turn,
            "interaction_round": interaction,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }
        # Push checkpoint before engine execution (for undo)
        self._engine.push_checkpoint(state)
        async for event in self._engine.astream(state):
            yield event

    def reconfigure(self, **overrides) -> None:
        if "advanced" in overrides:
            new_data = self.config.model_dump()
            new_data["advanced"] = overrides["advanced"]
            self.config = AgentConfig(**new_data)

    def evaluate(self, dataset, metrics):
        from arf.evaluation.runner import DefaultEvalRunner
        runner = DefaultEvalRunner()
        return runner.run(self, dataset, metrics)
