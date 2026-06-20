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
        tool_executor: Any = None,
        agent_config: Any = None,
        event_bus: Any = None,
        max_turns: int = 50,
        data_dir: str = "./data",
    ) -> None:
        self.agent = agent
        self._plugins = plugins
        self._tool_executor = tool_manager if tool_manager is not None else tool_executor
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

            # --- model_call ---
            try:
                if agent._stream_model:
                    stream = await agent.model_call()
                    async for chunk in stream:
                        yield ctx.emit("model_chunk", chunk)
                    result = stream.result
                else:
                    result = await agent.model_call(stream=False)
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
            })

            # --- after_model ---
            if await self._checkpoint("after_model", ctx):
                yield ctx.emit("parked", {"hook_name": "after_model", "waiting": agent.state.waiting})
                await self._do_park()
                if self._parked:
                    return

            # --- tool execution ---
            if result.tool_calls and self._tool_executor:
                # --- before_tools ---
                if await self._checkpoint("before_tools", ctx):
                    yield ctx.emit("parked", {"hook_name": "before_tools", "waiting": agent.state.waiting})
                    await self._do_park()
                    if self._parked:
                        return

                # Execute tools
                for tc in result.tool_calls:
                    yield ctx.emit("tool_call_start", {"name": tc["name"], "id": tc["id"]})

                try:
                    tool_results = await self._tool_executor.execute(result.tool_calls)
                except Exception as exc:
                    ctx.hook_data["exception"] = exc
                    await self._checkpoint("on_error", ctx)
                    yield ctx.emit("error", {"detail": str(exc)})
                    break

                for tc in result.tool_calls:
                    r = tool_results.get(tc["id"])
                    agent.input("tool", {
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "result": r.data if r and r.success else "",
                        "error": str(r.error) if r and r.error else "",
                    })
                    yield ctx.emit("tool_call_end", {
                        "name": tc["name"], "id": tc["id"],
                        "success": r.success if r else False,
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
