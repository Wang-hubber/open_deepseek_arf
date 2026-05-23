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
        system_prompt = (
            f"You are {config.name}, an AI assistant.\n\n"
            f"## Capabilities\n{config.description}\n\n"
            f"{'## Critical Rules\n' + (adv.critical_rules if adv else '') if adv and (adv.critical_rules if adv else '') else ''}"
        )

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

    @property
    def state_store(self):
        return self._state_store

    @property
    def event_bus(self):
        return self._event_bus

    async def chat(self, user_message: str, session_id: str = "default") -> str:
        from arf.core.state import AgentState
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": [{"role": "user", "content": user_message}],
            "current_model": self.config.models[0].name if self.config.models else "default",
            "current_turn": 0,
            "context_summary": "",
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
        state: AgentState = {
            "session_id": session_id,
            "agent_name": self.config.name,
            "messages": [{"role": "user", "content": user_message}],
            "current_model": self.config.models[0].name if self.config.models else "default",
            "current_turn": 0,
            "context_summary": "",
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
