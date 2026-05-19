"""Dispatcher — two-phase agent orchestrator.

Runs UserAgent graph first. If UserAgent calls handoff_to_sys,
runs SysAgent graph with full context and returns its result.
"""

import json
import logging
from typing import Any

from .graph import GraphResult

logger = logging.getLogger("arf.engine.dispatcher")

DEFAULT_USER_MAX_TURNS = 6
DEFAULT_SYS_MAX_TURNS = 10


class Dispatcher:
    """Two-phase agent orchestrator.

    Phase 1: UserAgent graph (user persona, restricted tools, classifier-enabled)
    Phase 2: SysAgent graph (sys persona, all tools, fixed deep_thinking)
             — only invoked if UserAgent calls handoff_to_sys.
    """

    def __init__(self, user_agent, sys_agent):
        self.user_agent = user_agent
        self.sys_agent = sys_agent
        self._trace_collector = None  # injected by SessionManager

    def run(self, message: str, history: list[dict],
            project_dir: str | None = None) -> GraphResult:
        """Non-streaming dispatch. Runs Phase 1, optionally Phase 2."""

        user_max = getattr(self.user_agent, 'max_turns', DEFAULT_USER_MAX_TURNS)
        sys_max = getattr(self.sys_agent, 'max_turns', DEFAULT_SYS_MAX_TURNS)
        total_max = sys_max

        # Phase 1: User Agent
        user_result = self._run_phase(
            self.user_agent, message, history, project_dir,
            max_turns=min(user_max, total_max),
        )

        if not self._detect_handoff(user_result.tool_events):
            return user_result

        # Phase 2: Sys Agent
        handoff = self._extract_handoff(user_result.tool_events)

        if self._trace_collector:
            self._trace_collector.emit({
                "event_type": "lifecycle.handoff",
                "status": "ok",
                "metadata": {
                    "phase": "user_agent_complete",
                    "intent": handoff.get("intent", ""),
                    "required_actions": handoff.get("required_actions", []),
                    "user_turns_used": user_result.turns,
                },
            })

        sys_history = self._build_sys_history(history, message)
        sys_message = self._build_handoff_message(message, handoff)
        remaining_turns = max(1, total_max - user_result.turns)

        sys_result = self._run_phase(
            self.sys_agent, sys_message, sys_history, project_dir,
            max_turns=remaining_turns,
        )

        if self._trace_collector:
            self._trace_collector.emit({
                "event_type": "lifecycle.handoff",
                "status": "ok",
                "metadata": {
                    "phase": "sys_agent_complete",
                    "sys_model": self.sys_agent.default_model,
                    "remaining_turns": remaining_turns,
                    "sys_turns_used": sys_result.turns,
                },
            })

        # Merge: use Sys response but include full history
        return GraphResult(
            response=sys_result.response,
            history=sys_result.history,
            tool_events=user_result.tool_events + sys_result.tool_events,
            transition_log=user_result.transition_log + sys_result.transition_log,
            turns=user_result.turns + sys_result.turns,
            truncated=sys_result.truncated,
            usage=_merge_usage(user_result.usage, sys_result.usage),
        )

    def run_stream(self, message: str, history: list[dict],
                   project_dir: str | None = None) -> Any:
        """Streaming dispatch. Emits events from Phase 1, then Phase 2 on handoff."""

        user_max = getattr(self.user_agent, 'max_turns', DEFAULT_USER_MAX_TURNS)
        sys_max = getattr(self.sys_agent, 'max_turns', DEFAULT_SYS_MAX_TURNS)
        total_max = sys_max

        # Phase 1: Stream User Agent
        handoff_info = None
        user_events = []
        user_turns = 0

        for event in self.user_agent.chat_stream_with_tools(
            message, history, project_dir, max_turns=user_max,
        ):
            etype = event.get("type", "")
            user_events.append(event)

            if etype == "done":
                user_turns = event.get("turns", user_max)
                if self._detect_handoff_from_events(user_events):
                    handoff_info = self._extract_handoff_from_events(user_events)
                if not handoff_info:
                    yield event
                    return
                # Emit lifecycle.handoff trace for user_agent_complete
                if self._trace_collector:
                    self._trace_collector.emit({
                        "event_type": "lifecycle.handoff",
                        "status": "ok",
                        "metadata": {
                            "phase": "user_agent_complete",
                            "intent": handoff_info.get("intent", ""),
                            "required_actions": handoff_info.get("required_actions", []),
                            "user_turns_used": user_turns,
                        },
                    })
                # Don't yield done yet — continue with Phase 2
                continue

            if etype == "error":
                yield event
                yield {"type": "done", "response": event.get("detail", "Error"),
                       "history": history, "error": True}
                return

            yield event

        if not handoff_info:
            return

        # Phase 2: Emit handoff event, then stream Sys Agent
        yield {
            "type": "handoff",
            "from": "user_agent",
            "to": "sys_agent",
            "intent": handoff_info.get("intent", ""),
        }

        sys_history = self._build_sys_history(history, message)
        sys_message = self._build_handoff_message(message, handoff_info)
        remaining_turns = max(1, total_max - user_turns)

        sys_turns_used = 0
        for event in self.sys_agent.chat_stream_with_tools(
            sys_message, sys_history, project_dir, max_turns=remaining_turns,
        ):
            etype = event.get("type", "")
            if etype == "done":
                sys_turns_used = event.get("turns", remaining_turns)
            yield event

        if self._trace_collector:
            self._trace_collector.emit({
                "event_type": "lifecycle.handoff",
                "status": "ok",
                "metadata": {
                    "phase": "sys_agent_complete",
                    "sys_model": self.sys_agent.default_model,
                    "remaining_turns": remaining_turns,
                    "sys_turns_used": sys_turns_used,
                },
            })

    # ---- ARFAgent-compatible interface for routes.py -------------------

    def chat_with_tools(self, message: str, history: list[dict],
                        project_dir: str | None = None):
        """ARFAgent-compatible interface for routes.py."""
        result = self.run(message, history, project_dir)
        return (
            result.response,
            result.history,
            result.tool_events,
            result.usage,
            result.transition_log,
        )

    def chat_stream_with_tools(self, message: str, history: list[dict],
                               project_dir: str | None = None):
        """ARFAgent-compatible streaming interface for routes.py."""
        yield from self.run_stream(message, history, project_dir)

    @property
    def model(self):
        """Expose model for usage tracking in routes.py."""
        return self.user_agent.model

    # ---- helpers -------------------------------------------------------

    def _run_phase(self, agent, message, history, project_dir, max_turns):
        """Run one agent phase, return GraphResult-compatible object."""
        response, full_history, tool_events, usage, traces = agent.chat_with_tools(
            message, history, project_dir, max_turns=max_turns,
        )
        turns = self._count_turns(traces)
        return GraphResult(
            response=response,
            history=full_history,
            tool_events=tool_events,
            transition_log=traces,
            turns=turns,
            truncated=False,
            usage=usage,
        )

    @staticmethod
    def _count_turns(traces: list[dict] | None) -> int:
        """Count actual conversation turns from trace events.

        Each trace event has a "turn" key set to the current turn_count.
        The highest turn value seen is the actual number of turns used.
        """
        if not traces:
            return 1
        max_turn = 1
        for t in traces:
            turn = t.get("turn")
            if isinstance(turn, (int, float)):
                max_turn = max(max_turn, int(turn))
        return max_turn

    @staticmethod
    def _detect_handoff(tool_events: list[dict]) -> bool:
        for te in tool_events:
            if te.get("type") == "tool_result" and te.get("tool") == "handoff_to_sys":
                try:
                    result = json.loads(te.get("result", "{}"))
                    if result.get("handoff"):
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
        return False

    @staticmethod
    def _detect_handoff_from_events(events: list[dict]) -> bool:
        for e in events:
            if e.get("type") == "tool_result" and e.get("tool") == "handoff_to_sys":
                try:
                    result = json.loads(e.get("result", "{}"))
                    if result.get("handoff"):
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
        return False

    @staticmethod
    def _extract_handoff(tool_events: list[dict]) -> dict:
        for te in tool_events:
            if te.get("type") == "tool_result" and te.get("tool") == "handoff_to_sys":
                try:
                    return json.loads(te.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
        return {}

    @staticmethod
    def _extract_handoff_from_events(events: list[dict]) -> dict:
        for e in events:
            if e.get("type") == "tool_result" and e.get("tool") == "handoff_to_sys":
                try:
                    return json.loads(e.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
        return {}

    @staticmethod
    def _build_sys_history(original_history: list[dict], original_msg: str) -> list[dict]:
        """Build history for Sys Agent: original history + user message.

        The handoff message (sent as the new user message) carries
        intent, required_actions, reason, and original user message --
        sufficient context for Sys Agent to start without User Agent's
        intermediate tool calls.
        """
        history = list(original_history)
        history.append({"role": "user", "content": original_msg})
        return history

    @staticmethod
    def _build_handoff_message(original_msg: str, handoff: dict) -> str:
        intent = handoff.get("intent", "")
        actions = handoff.get("required_actions", [])
        reason = handoff.get("reason", "")
        return (
            f"[Handoff from User Agent]\n"
            f"意图: {intent}\n"
            f"需要动作: {', '.join(actions) if actions else '无'}\n"
            f"原因: {reason}\n"
            f"原始用户消息: {original_msg}"
        )


def _merge_usage(a: dict | None, b: dict | None) -> dict:
    result = {}
    for u in (a, b):
        if u:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                result[k] = result.get(k, 0) + u.get(k, 0)
    return result
