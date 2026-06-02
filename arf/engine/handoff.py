"""HandoffManager — detect handoff signals, resolve targets, build contexts."""
import logging
from arf.core.config_base import HandoverRuleConfig
from arf.core.state import AgentState

logger = logging.getLogger("arf.engine.handoff")


class HandoffManager:
    def __init__(self, rules: list[HandoverRuleConfig]):
        self._rules: dict[str, list[HandoverRuleConfig]] = {}
        for r in rules:
            self._rules.setdefault(r.from_agent, []).append(r)

    # ---- detection ----

    def detect(self, tool_results: dict) -> dict | None:
        """Scan tool results for {"handoff": True}. Return the first match."""
        for tc_id, result in tool_results.items():
            if hasattr(result, "data") and not isinstance(result, dict):
                data = getattr(result, "data", None)
            elif isinstance(result, dict) and "data" in result:
                data = result["data"]
            else:
                data = result

            if not isinstance(data, dict):
                continue

            if "handoff" not in data and "result" in data and isinstance(data["result"], dict):
                inner = data["result"]
                if inner.get("handoff"):
                    return {"tool_call_id": tc_id, **inner}
            elif data.get("handoff"):
                return {"tool_call_id": tc_id, **data}
        return None

    # ---- resolution ----

    async def resolve(self, from_agent: str, handoff_data: dict) -> str:
        """Resolve target agent name from handover rules."""
        candidates = self._rules.get(from_agent, [])
        if not candidates:
            return ""
        if len(candidates) == 1:
            return candidates[0].to_agent
        return candidates[0].to_agent if candidates else ""

    @property
    def has_rules(self) -> bool:
        return bool(self._rules)

    # ---- context builder ----

    def build_target_context(
        self, state: AgentState, from_agent: str, to_agent: str,
        handoff_data: dict,
    ) -> list[dict]:
        """Build initial messages for the target agent."""
        rule = self._resolve_rule(from_agent, to_agent)
        if not rule:
            return [{"role": "user", "content": handoff_data.get("task", "")}]

        messages: list[dict] = []

        if rule.context.raw_turns > 0:
            all_msgs = state.get("messages", [])
            n = min(rule.context.raw_turns * 2, len(all_msgs))
            messages.extend(all_msgs[-n:])
        elif rule.context.raw_turns == -1:
            messages.extend(state.get("messages", []))

        task = handoff_data.get("task", "")
        ctx = handoff_data.get("context", "")
        content = f"[Handoff from {from_agent}]\nTask: {task}"
        if ctx:
            content += f"\nContext: {ctx}"
        if rule.context.task_summary:
            summary = state.get("context_summary", "")
            if summary:
                content += f"\nSummary: {summary}"

        messages.append({"role": "user", "content": content})
        return messages

    def _resolve_rule(self, from_agent: str, to_agent: str) -> HandoverRuleConfig | None:
        for rule in self._rules.get(from_agent, []):
            if rule.to_agent == to_agent:
                return rule
        return None
