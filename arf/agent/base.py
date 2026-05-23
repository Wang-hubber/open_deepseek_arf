"""BaseAgent — assembles all Protocol implementations into a running Agent."""
from pathlib import Path
from arf.agent.config import AgentConfig, AdvancedConfig
from arf.engine.graph import GraphEngine
from arf.engine.loop_strategies.react import ReActStrategy
from arf.engine.checkpoint import InMemoryStateStore
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.engine.loop_strategies.planner import PromptBasedPlanner
from arf.event_bus import InMemoryEventBus
from arf.resources.resolver import DefaultToolResolver
from arf.resources.providers.static_yaml import StaticYamlToolProvider
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
from arf.human_loop.approval_points import AlwaysAutoApprove
from arf.human_loop.channels.console import ConsoleChannel


def _build_system_prompt(config: AgentConfig) -> str:
    """Build system prompt from AgentConfig.system_prompt template.

    Supports placeholders filled by engine at runtime:
      {{AGENT_NAME}}      → config.name
      {{CRITICAL_RULES}}  → config.system_prompt.critical_rules
      {{INVENTORY}}       → kernel tools + skills (progressive disclosure)
    Falls back to auto-generated prompt if no template provided.
    """
    sp = config.system_prompt
    template = sp.template.strip()
    if not template:
        # Fallback for configs without explicit system_prompt
        template = (
            f"You are {config.name}, an AI assistant.\n\n"
            f"## Capabilities\n{config.description}\n\n"
        )

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
    prompt = prompt.replace("{{CRITICAL_RULES}}", sp.critical_rules or "")
    prompt = prompt.replace("{{INVENTORY}}", inventory)

    return prompt


class BaseAgent:
    def __init__(self, config: AgentConfig, **override_protocols) -> None:
        self.config = config
        adv = config.effective_advanced()

        # 1. Core infrastructure
        event_bus = override_protocols.pop("event_bus", InMemoryEventBus())
        state_store = override_protocols.pop("state_store", InMemoryStateStore())

        # 2. Resources
        tools_dir = override_protocols.pop("tools_dir", Path("./tools"))
        providers = override_protocols.pop("providers", [StaticYamlToolProvider(tools_dir)])
        tool_resolver = override_protocols.pop("tool_resolver", DefaultToolResolver(providers))

        # 3. Memory
        mem_cfg = (adv.memory or AdvancedConfig.default().memory) if adv else AdvancedConfig.default().memory
        memory_store = override_protocols.pop("memory_store", FileMemoryStore(mem_cfg.workspace if mem_cfg else "./memory"))
        memory_retriever = override_protocols.pop("memory_retriever", RecentFirstRetriever())
        memory_writer = override_protocols.pop("memory_writer", RuleBasedMemoryWriter())

        # 4. Guardrails
        guard_runner = override_protocols.pop("guard_runner", DefaultGuardRunner(
            input_guard=NoneInputGuard(),
            output_guard=RegexOutputGuard(),
            tool_guard=PathCheckToolGuard(),
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
        tool_executor = override_protocols.pop("tool_executor", ConcurrentToolExecutor(tool_resolver))

        # 8. Loop strategy
        ls_name = (adv.loop_strategy if adv else "react") if adv else "react"
        loop_strategy = override_protocols.pop("loop_strategy", ReActStrategy(max_turns=(adv.max_turns if adv else 50)))

        # 9. Planner (optional)
        planner = override_protocols.pop("planner", None)

        # 10. Build engine
        system_prompt = _build_system_prompt(config)

        self._engine = GraphEngine(
            loop_strategy=loop_strategy,
            state_store=state_store,
            tool_executor=tool_executor,
            tool_resolver=tool_resolver,
            transaction_ctx=transaction_ctx,
            planner=planner,
            memory_retriever=memory_retriever,
            memory_writer=memory_writer,
            hook_runner=hook_runner,
            guard_runner=guard_runner,
            event_bus=event_bus,
            error_policy=error_policy,
            system_prompt=system_prompt,
            max_turns=(adv.max_turns if adv else 50),
            **override_protocols,
        )
        self._state_store = state_store
        self._event_bus = event_bus
        self._memory_store = memory_store
        self._tool_resolver = tool_resolver
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
        default_name = config.models[0].name

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
            adapter = adapters.get(model_name, adapters[default_name])
            for chunk in adapter.chat_stream_full(messages, tools=_to_openai_tools(tools)):
                yield chunk

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
    def usage_tracker(self):
        return self._usage_tracker

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        from arf.core.state import AgentState
        # Load existing state to preserve conversation history
        existing = await self._state_store.get(session_id)
        if existing:
            messages = existing.get("messages", []) + [{"role": "user", "content": user_message}]
            turn = existing.get("current_turn", 0)
            summary = existing.get("context_summary", "")
        else:
            messages = [{"role": "user", "content": user_message}]
            turn = 0
            summary = ""
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": messages,
            "current_model": self.config.models[0].name if self.config.models else "default",
            "current_turn": turn,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }
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
        else:
            messages = [{"role": "user", "content": user_message}]
            turn = 0
            summary = ""
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": messages,
            "current_model": self.config.models[0].name if self.config.models else "default",
            "current_turn": turn,
            "context_summary": summary,
            "tool_results": {},
            "plan": None,
            "metadata": {},
        }
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
