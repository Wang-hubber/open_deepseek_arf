"""LangGraph-based agent graph and GraphEngine wrapper.

GraphEngine drives the agent loop via a LangGraph StateGraph.

Two graph variants:
  build_agent_graph()           -- sync nodes, for graph.invoke() (non-streaming)
  build_streaming_agent_graph() -- async call_model node, for graph.astream()
                                  with stream_mode=["custom", "values"]
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from langgraph.graph import StateGraph, START, END


@dataclass
class GraphParams:
    """Input parameters for GraphEngine.run() and run_stream()."""
    messages: list[dict]
    system_prompt: str = ""
    tools: list[dict] | None = None
    max_turns: int = 8


@dataclass
class GraphResult:
    """Output of a completed (or truncated) graph execution."""
    response: str
    history: list[dict]
    tool_events: list[dict]
    transition_log: list[dict]
    turns: int
    truncated: bool = False
    usage: dict | None = None

from .state import AgentState, default_state
from .nodes import (
    classify_node,
	    compact_node,
    call_model_node,
    call_model_node_stream,
    execute_tools_node,
    respond_node,
    recovery_node,
)
from .router import decide_entry, route_after_model, route_after_tools, should_continue
from .tracing import DevTracer

logger = logging.getLogger("arf.graph.engine")

# Module-level tracer instance
_dev_tracer = DevTracer()


def build_agent_graph() -> StateGraph:
    """Build the sync ARF agent state graph. Compile once, reuse across invocations.

    Graph structure:
        START -> [classify?] -> call_model -> route:
                    ├── execute_tools -> call_model (loop)
                    ├── recovery -> [continue?] -> call_model / respond
                    └── respond -> END
    """
    workflow = StateGraph(AgentState)

    # Sync nodes
    workflow.add_node("classify", classify_node)
    workflow.add_node("compact", compact_node)
    workflow.add_node("call_model", call_model_node)
    workflow.add_node("execute_tools", execute_tools_node)
    workflow.add_node("respond", respond_node)
    workflow.add_node("recovery", recovery_node)

    workflow.add_conditional_edges(START, decide_entry, {
        "classify": "classify",
        "call_model": "compact",
    })
    workflow.add_edge("classify", "compact")
    workflow.add_edge("compact", "call_model")

    workflow.add_conditional_edges("call_model", route_after_model, {
        "execute_tools": "execute_tools",
        "recovery": "recovery",
        "respond": "respond",
    })

    workflow.add_conditional_edges("execute_tools", route_after_tools, {
        "call_model": "compact",
        "respond": "respond",
    })

    workflow.add_conditional_edges("recovery", should_continue, {
        "continue": "compact",
        "stop": "respond",
    })

    workflow.add_edge("respond", END)

    return workflow.compile()


def build_streaming_agent_graph() -> StateGraph:
    """Build the streaming ARF agent state graph.

    Same structure as build_agent_graph() but uses the async streaming
    call_model_node_stream for token-level event emission.
    """
    workflow = StateGraph(AgentState)

    # Nodes -- classify, execute_tools, respond, recovery are sync
    # call_model uses the async streaming variant
    workflow.add_node("classify", classify_node)
    workflow.add_node("compact", compact_node)
    workflow.add_node("call_model", call_model_node_stream)
    workflow.add_node("execute_tools", execute_tools_node)
    workflow.add_node("respond", respond_node)
    workflow.add_node("recovery", recovery_node)

    workflow.add_conditional_edges(START, decide_entry, {
        "classify": "classify",
        "call_model": "compact",
    })
    workflow.add_edge("classify", "compact")
    workflow.add_edge("compact", "call_model")

    workflow.add_conditional_edges("call_model", route_after_model, {
        "execute_tools": "execute_tools",
        "recovery": "recovery",
        "respond": "respond",
    })

    workflow.add_conditional_edges("execute_tools", route_after_tools, {
        "call_model": "compact",
        "respond": "respond",
    })

    workflow.add_conditional_edges("recovery", should_continue, {
        "continue": "compact",
        "stop": "respond",
    })

    workflow.add_edge("respond", END)

    return workflow.compile()


class GraphEngine:
    """LangGraph-based agent graph engine.

    Same dependency-injection contract:
      call_model(msgs, tools) -> response object
      execute_tool(name, args) -> str
      stream_model(msgs, tools) -> Generator[dict]
      run_hook(event, payload) -> dict | None

    Additional (LangGraph-specific):
      model_adapter_factory(model_type: str) -> adapter | None
      classifier_call(messages: list[dict]) -> str
      classifier_enabled: bool
      available_model_types: set[str]
    """

    def __init__(
        self,
        call_model: Callable[[list[dict], Optional[list[dict]]], Any],
        execute_tool: Callable[[str, str], str],
        stream_model: Callable = None,
        run_hook: Callable[[str, dict], Optional[dict]] = None,
        model_adapter_factory: Callable[[str], Any] = None,
        classifier_call: Callable = None,
        classifier_enabled: bool = False,
        available_model_types: Optional[set[str]] = None,
        user_model_preference: Optional[str] = None,
        compaction_threshold: int = 255000,
        compaction_keep_tokens: int = 55000,
    ):
        self._call_model = call_model
        self._execute_tool = execute_tool
        self._stream_model = stream_model
        self._run_hook = run_hook
        self._model_adapter_factory = model_adapter_factory
        self._classifier_call = classifier_call
        self._classifier_enabled = classifier_enabled
        self._available_model_types = available_model_types or set()
        self._user_model_preference = user_model_preference
        self._refresh_tools_fn = None
        self._project_dir = None
        self._system_tool_names: frozenset[str] = frozenset()
        self._compaction_threshold = compaction_threshold
        self._compaction_keep_tokens = compaction_keep_tokens

        # Compile both graphs once
        self._graph = build_agent_graph()
        try:
            self._streaming_graph = build_streaming_agent_graph()
        except Exception as e:
            logger.warning("Failed to compile streaming graph: %s", e)
            self._streaming_graph = None

    # ---- public API ------------------------------------------------

    def run(self, params) -> Any:
        """Execute a non-streaming query through the LangGraph."""
        initial_state = self._params_to_state(params)
        config = self._build_config()

        _dev_tracer.node_start("graph", 0, initial_state.get("current_model", "?"))

        try:
            final = self._graph.invoke(initial_state, config)
        except Exception as e:
            logger.error("Graph invocation failed: %s", e, exc_info=True)
            return GraphResult(
                response=f"Graph execution error: {e}",
                history=self._display_history(
                    initial_state.get("messages", [])
                ),
                tool_events=[],
                transition_log=[],
                turns=initial_state.get("turn_count", 1),
                truncated=True,
                usage=initial_state.get("usage", {}),
            )

        _dev_tracer.node_end("graph", final.get("turn_count", 1),
                            final.get("current_model", "?"),
                            {"classification": final.get("classification", "?")})

        return GraphResult(
            response=final.get("final_response") or "No response generated.",
            history=self._display_history(final.get("messages", [])),
            tool_events=final.get("tool_events", []),
            transition_log=final.get("node_traces", []),
            turns=final.get("turn_count", 1),
            truncated=final.get("truncated", False),
            usage=final.get("usage", {}),
        )

    async def run_stream_async(self, params) -> Any:
        """Async streaming generator -- yields ARF event dicts.

        Uses graph.astream() with stream_mode=["custom", "values"]:
          - "custom" events: token chunks, tool calls, usage from call_model_node_stream
          - "values" events: state updates (used for tool_result and done detection)
        """
        if self._streaming_graph is None:
            yield {"type": "error", "detail": "Streaming graph not compiled"}
            yield {"type": "done", "response": "", "history": [], "error": True}
            return

        initial_state = self._params_to_state(params)
        config = self._build_config()
        # Inject stream_model for the streaming node
        config["configurable"]["stream_model"] = self._stream_model

        _dev_tracer.node_start("graph_stream", 0, initial_state.get("current_model", "?"))

        done_yielded = False
        final_state = None
        emitted_tool_events = 0  # track count to avoid duplicates

        try:
            async for mode, chunk in self._streaming_graph.astream(
                initial_state, config,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    # Token chunks, tool calls, usage from call_model_node_stream
                    yield chunk

                elif mode == "values":
                    # Full state after each node completes
                    final_state = chunk

                    # Emit only NEW tool_result events (tool_events accumulates)
                    all_tool_events = chunk.get("tool_events", [])
                    new_events = all_tool_events[emitted_tool_events:]
                    for te in new_events:
                        if te.get("type") == "tool_result":
                            yield te
                    emitted_tool_events = len(all_tool_events)

            # Done -- build final event from the last state
            if not done_yielded and final_state:
                yield {
                    "type": "done",
                    "response": final_state.get("final_response", ""),
                    "history": self._display_history(final_state.get("messages", [])),
                    "truncated": final_state.get("truncated", False),
                    "traces": final_state.get("node_traces", []),
                    "usage": final_state.get("usage", {}),
                }
                done_yielded = True

        except Exception as e:
            logger.error("Streaming error: %s", e, exc_info=True)
            if not done_yielded:
                yield {"type": "error", "detail": str(e)}
                yield {
                    "type": "done",
                    "response": f"Streaming error: {e}",
                    "history": [],
                    "error": True,
                }

    def run_stream(self, params) -> Any:
        """Synchronous streaming generator.

        Wraps run_stream_async via asyncio, yielding ARF event dicts.
        """
        import queue
        import threading

        event_queue: queue.Queue = queue.Queue()
        stream_done = threading.Event()
        stream_error: Optional[Exception] = None

        def _run_async():
            nonlocal stream_error
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                async_gen = self.run_stream_async(params)

                async def _collect():
                    async for event in async_gen:
                        event_queue.put(event)

                loop.run_until_complete(_collect())
            except Exception as e:
                stream_error = e
            finally:
                stream_done.set()

        thread = threading.Thread(target=_run_async, daemon=True)
        thread.start()

        while not stream_done.is_set() or not event_queue.empty():
            try:
                event = event_queue.get(timeout=0.1)
                yield event
            except queue.Empty:
                continue

        if stream_error:
            yield {"type": "error", "detail": str(stream_error)}

        thread.join(timeout=5)

    def set_tools_refresher(self, refresh_fn: Callable[[], list[dict] | None]):
        """Set a callback that returns the current active tools list."""
        self._refresh_tools_fn = refresh_fn

    # ---- internals ----------------------------------------------------

    def _params_to_state(self, params) -> dict:
        """Convert GraphParams to AgentState initial dict.

        System messages are stripped from the message list -- call_model_node
        prepends system_prompt separately to avoid duplication.
        """
        conversation_msgs = [
            m for m in params.messages
            if m.get("role") != "system"
        ]
        return default_state(
            messages=conversation_msgs,
            system_prompt=params.system_prompt,
            tools=params.tools,
            max_turns=params.max_turns,
            current_model=self._user_model_preference or "quick_thinking",
        )

    def _build_config(self) -> dict:
        """Build the LangGraph invocation config with all DI callables."""
        return {
            "configurable": {
                "model_resolvers": self._build_model_resolvers(),
                "tool_executor": self._execute_tool,
                "hook_runner": self._run_hook,
                "classifier_call": self._classifier_call,
                "classifier_enabled": self._classifier_enabled,
                "available_model_types": self._available_model_types,
                "user_model_preference": self._user_model_preference,
                "refresh_tools": self._refresh_tools_fn or (lambda: None),
                "workspace_dir": str(self._project_dir) if self._project_dir else "",
                "system_tool_names": self._system_tool_names,
                "compact_model": self._build_compact_model(),
                "compaction_threshold": getattr(self, '_compaction_threshold', 255000),
                "compaction_keep_tokens": getattr(self, '_compaction_keep_tokens', 55000),
                "trace_collector": getattr(self, '_trace_collector', None),
            },
        }

    def _build_model_resolvers(self) -> dict[str, Callable]:
        """Build a dict of model_type -> factory callable that returns a ModelAdapter.

        Each factory is called with no args by _resolve_model_adapter in nodes.py,
        and should return a ModelAdapter instance. Resolvers are lazy — each call
        re-invokes the factory so model switches mid-session are reflected immediately.
        """
        resolvers: dict[str, Callable] = {}

        factory = self._model_adapter_factory
        if factory is not None:
            for mt in self._available_model_types:
                # Closure captures mt by value, calls factory fresh each time
                def _make_resolver(model_type: str):
                    return lambda mt=model_type: factory(mt)
                resolvers[mt] = _make_resolver(mt)

        # Fallback: use the single injected call_model for all types
        if not resolvers and self._call_model is not None:
            fallback_call = self._call_model

            class _LegacyAdapter:
                def chat_complete(self, msgs, tools=None):
                    return fallback_call(msgs, tools)

            legacy = _LegacyAdapter()
            for mt in self._available_model_types or {"quick_thinking"}:
                resolvers[mt] = lambda adp=legacy: adp

        return resolvers

    def _build_compact_model(self):
        """Build a ModelAdapter (quick_no_thinking) for context compaction."""
        factory = self._model_adapter_factory
        if factory is not None:
            try:
                return factory("quick_no_thinking")
            except Exception:
                pass
        return None

    @staticmethod
    def _display_history(messages: list[dict]) -> list[dict]:
        return [
            m for m in messages
            if m.get("role") in ("user", "assistant")
            and "tool_calls" not in m
        ]
