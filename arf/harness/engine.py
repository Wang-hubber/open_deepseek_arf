"""AgentHarness — execution skeleton + plugin scheduler + park/resume."""
from __future__ import annotations
import asyncio
import uuid
import logging
from collections.abc import AsyncIterator
from typing import Any

from arf.agent.primitive import PrimitiveAgent
from arf.core.events import AgentEvent
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin

logger = logging.getLogger("arf.harness")

CHECKPOINTS = [
    "before_round", "before_model", "after_model",
    "before_tools", "after_tools", "after_round", "on_error",
]


class AgentHarness:
    def __init__(
        self,
        agent: PrimitiveAgent,
        plugins: list[Plugin],
        tool_manager: Any = None,
        agent_config: Any = None,
        event_bus: Any = None,
        max_turns: int = 50,
        data_dir: str = "./data",
    ) -> None:
        self.agent = agent
        self._plugins = plugins
        self._tool_manager = tool_manager
        self._agent_config = agent_config
        self._event_bus = event_bus
        self._max_turns = max_turns
        self._data_dir = data_dir
        self._park_event: asyncio.Event | None = None
        self._parked: bool = False
        self._interaction_round: int = 0

        # Index plugins by hook_name for fast lookup
        self._by_hook: dict[str, list[Plugin]] = {c: [] for c in CHECKPOINTS}
        for p in plugins:
            for e in p.events:
                hook = e["hook_name"]
                if hook in self._by_hook:
                    self._by_hook[hook].append(p)

    # ── Tool filtering ──────────────────────────────────

    def _filter_tools(self, all_tools: list[dict]) -> list[dict]:
        """Filter tool definitions by agent config plugins/tools lists."""
        from arf.core.tool_naming import split_name

        if self._agent_config is None:
            return all_tools

        plugin_names: set[str] = set(self._agent_config.plugins) if self._agent_config.plugins else set()
        user_tool_names: set[str] | None = None
        if self._agent_config.tools:
            user_tool_names = {t.name if hasattr(t, 'name') else str(t) for t in self._agent_config.tools}

        result = []
        for t in all_tools:
            source, local_name = split_name(t["name"])
            if source == "kernel":
                result.append(t)
            elif source == "user":
                if user_tool_names is None or local_name in user_tool_names:
                    result.append(t)
            elif source in plugin_names:
                result.append(t)
            elif source not in ("kernel", "user", ""):
                # server__ or other namespace: include if unknown (future-proof)
                result.append(t)
        return result

    # ── Plugin scheduling ───────────────────────────────

    def _make_ctx(self, turn: int = 0) -> PluginContext:
        return PluginContext(
            agent=self.agent,
            session_id=self.agent.state.session_id,
            event_bus=self._event_bus,
            data_dir=self._data_dir,
        )

    def _sync_ctx(self, ctx: PluginContext, turn: int) -> None:
        """Update context lifecycle counters at each checkpoint."""
        ctx.turn = turn
        ctx.interaction_round = self._interaction_round

    async def _run_blocking(self, hook_name: str, ctx: PluginContext) -> None:
        for p in self._by_hook.get(hook_name, []):
            for ename in p.event_names_for_hook(hook_name):
                if p.mode_for(hook_name, ename) == "blocking":
                    await p.handle(ename, ctx)

    def _run_side(self, hook_name: str, ctx: PluginContext) -> None:
        for p in self._by_hook.get(hook_name, []):
            for ename in p.event_names_for_hook(hook_name):
                if p.mode_for(hook_name, ename) == "side":
                    asyncio.create_task(self._safe_side(p, ename, ctx))

    async def _safe_side(self, plugin: Plugin, event_name: str, ctx: PluginContext) -> None:
        try:
            await plugin.handle(event_name, ctx)
        except Exception:
            logger.exception("Side plugin %s.%s failed", plugin.name, event_name)

    # ── Checkpoint ──────────────────────────────────────

    async def _checkpoint(self, hook_name: str, ctx: PluginContext) -> bool:
        """Run plugins at checkpoint, then check waiting. Returns True if should park."""
        ctx.hook_data["_current_hook"] = hook_name

        # 1. Run blocking plugins
        await self._run_blocking(hook_name, ctx)

        # 2. Run side plugins (fire and forget)
        self._run_side(hook_name, ctx)

        # 3. Check waiting for this hook_name
        waiting = self.agent.state.waiting.get(hook_name, [])
        return len(waiting) > 0

    # ── Execution Loop ──────────────────────────────────

    async def run(self, user_message: str, session_id: str | None = None) -> AsyncIterator[AgentEvent]:
        """Main execution loop. Yields AgentEvent for SSE streaming."""
        agent = self.agent
        self._interaction_round += 1

        # Assign session_id if this is a new session
        if not agent.state.session_id:
            agent.state.session_id = session_id or str(uuid.uuid4())

        ctx = self._make_ctx()
        self._sync_ctx(ctx, turn=0)

        # Inject user message
        agent.input("user", user_message)

        # --- before_round ---
        if await self._checkpoint("before_round", ctx):
            yield ctx.emit("parked", {"hook_name": "before_round", "waiting": agent.state.waiting})
            await self._do_park()
            if self._parked:
                return

        turn = 0
        while turn < self._max_turns:
            turn += 1
            self._sync_ctx(ctx, turn)

            # --- before_model ---
            if await self._checkpoint("before_model", ctx):
                yield ctx.emit("parked", {"hook_name": "before_model", "waiting": agent.state.waiting})
                await self._do_park()
                if self._parked:
                    return

            # Fetch tool definitions and inject into model call
            openai_tools = None
            active_tool_definitions: list[dict] | None = None
            if self._tool_manager:
                from arf.core.tool_convert import to_openai_tools
                all_tools = await self._tool_manager.get_tool_definitions()
                filtered = self._filter_tools(all_tools)
                active_tool_definitions = filtered
                openai_tools = to_openai_tools(filtered)

            # --- model_call ---
            try:
                if agent._stream_model:
                    stream = await agent.model_call(tools=openai_tools)
                    async for chunk in stream:
                        yield ctx.emit("model_chunk", chunk)
                    result = stream.result
                else:
                    result = await agent.model_call(stream=False, tools=openai_tools)
            except Exception as exc:
                ctx.hook_data["exception"] = exc
                await self._checkpoint("on_error", ctx)
                yield ctx.emit("error", {"detail": str(exc)})
                break

            # Record the assistant response in agent state
            assistant_content = result.content if result.content else ""
            if result.tool_calls:
                # Store tool_calls as structured content so agent state reflects full response
                agent.input("assistant", {
                    "content": assistant_content,
                    "tool_calls": result.tool_calls,
                })
            else:
                agent.input("assistant", assistant_content)

            # Emit model_call_end for downstream consumers (collect_response, tests)
            yield ctx.emit("model_call_end", {
                "content": result.content,
                "tool_calls": result.tool_calls,
                "usage": result.usage,
                "finish_reason": result.finish_reason,
                "tool_definitions": active_tool_definitions,
            })

            # --- after_model ---
            if await self._checkpoint("after_model", ctx):
                yield ctx.emit("parked", {"hook_name": "after_model", "waiting": agent.state.waiting})
                await self._do_park()
                if self._parked:
                    return

            # --- tool execution ---
            if result.tool_calls and self._tool_manager:
                # --- before_tools ---
                if await self._checkpoint("before_tools", ctx):
                    yield ctx.emit("parked", {"hook_name": "before_tools", "waiting": agent.state.waiting})
                    await self._do_park()
                    if self._parked:
                        return

                for tc in result.tool_calls:
                    yield ctx.emit("tool_call_start", {"name": tc["name"], "id": tc["id"]})

                for tc in result.tool_calls:
                    try:
                        r = await self._tool_manager.execute(tc["name"], tc.get("params", {}))
                    except Exception as exc:
                        agent.input("tool", {
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "result": "",
                            "error": str(exc),
                        })
                        yield ctx.emit("tool_call_end", {
                            "name": tc["name"], "id": tc["id"],
                            "success": False,
                        })
                        continue

                    agent.input("tool", {
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "result": r.data if r.success else "",
                        "error": r.error or "",
                    })
                    yield ctx.emit("tool_call_end", {
                        "name": tc["name"], "id": tc["id"],
                        "success": r.success,
                    })

                # --- after_tools ---
                if await self._checkpoint("after_tools", ctx):
                    yield ctx.emit("parked", {"hook_name": "after_tools", "waiting": agent.state.waiting})
                    await self._do_park()
                    if self._parked:
                        return

                continue  # loop back to before_model

            break  # no tool_calls → round done

        # --- after_round ---
        if await self._checkpoint("after_round", ctx):
            yield ctx.emit("parked", {"hook_name": "after_round", "waiting": agent.state.waiting})
            await self._do_park()

    # ── Park / Resume ────────────────────────────────────

    async def _do_park(self) -> None:
        """Block until external resolve_wait() empties all waiting groups."""
        self._park_event = asyncio.Event()
        self._parked = True
        await self._park_event.wait()

    async def resolve_wait(self, wait_id: str, inject_message: dict | None = None) -> bool:
        """External call: finish a wait + optionally inject a message. Returns True if park resolves."""
        if inject_message:
            self.agent.input(
                role=inject_message.get("role", "user"),
                content=inject_message.get("content", ""),
            )
        self.agent.finish_wait(wait_id)

        if not self.agent.state.waiting:
            self._parked = False
            if self._park_event:
                self._park_event.set()
            return True
        return False
