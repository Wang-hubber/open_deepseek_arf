"""HandoffManager — detect handoff signals, resolve targets, build contexts."""
import logging
from arf.core.config_base import HandoverRuleConfig
from arf.core.state import AgentState

logger = logging.getLogger("arf.engine.handoff")


class HandoffManager:
    def __init__(self, rules: list[HandoverRuleConfig], system_model_call=None):
        self._rules: dict[str, list[HandoverRuleConfig]] = {}
        for r in rules:
            self._rules.setdefault(r.from_agent, []).append(r)
        self._system_model_call = system_model_call

    # ---- detection ----

    def detect(self, tool_results: dict) -> dict | None:
        """Scan tool results for {"handoff": True}. Return the first match.

        Handles both ToolResult objects (via .data attribute) and plain dicts.
        """
        for tc_id, result in tool_results.items():
            data = getattr(result, "data", None) if hasattr(result, "data") else result
            if isinstance(data, dict) and data.get("handoff"):
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

        # Multiple candidates — LLM match trigger against task description
        task = handoff_data.get("task", "")
        if self._system_model_call:
            try:
                prompt = (
                    "Select the best matching rule for this handoff task.\n\n"
                    "Task: {task}\n\n"
                    "Rules:\n{rules}\n\n"
                    "Return ONLY the rule index (0-{max_idx})."
                ).format(
                    task=task[:300],
                    rules="\n".join(
                        f"[{i}] trigger: {r.trigger} → to: {r.to_agent}"
                        for i, r in enumerate(candidates)
                    ),
                    max_idx=len(candidates) - 1,
                )
                idx = int((await self._system_model_call(prompt)).strip()[0])
                if 0 <= idx < len(candidates):
                    return candidates[idx].to_agent
            except Exception:
                pass

        # Fallback: first exact trigger keyword match
        for r in candidates:
            if any(w in task for w in r.trigger.split()):
                return r.to_agent
        return candidates[0].to_agent

    def get_rule(self, from_agent: str, to_agent: str) -> HandoverRuleConfig | None:
        """Get the matching handover rule for the given agent pair."""
        for r in self._rules.get(from_agent, []):
            if r.to_agent == to_agent:
                return r
        return None

    # ---- context building ----

    def build_target_context(
        self,
        from_state: AgentState,
        rule: HandoverRuleConfig,
        handoff_data: dict,
        target_system_prompt: str,
    ) -> list[dict]:
        """Build target agent's initial messages."""
        ctx_cfg = rule.context
        messages = [{"role": "system", "content": target_system_prompt}]

        # Raw context: last N turns
        if ctx_cfg.raw_turns != 0:
            all_msgs = from_state.get("messages", [])
            if ctx_cfg.raw_turns > 0:
                take = min(len(all_msgs), ctx_cfg.raw_turns * 2)
                raw_context = all_msgs[-take:]
            else:
                raw_context = all_msgs  # -1 = all
            messages.extend(raw_context)

        # Task summary placeholder (populated by engine after LLM call)
        if ctx_cfg.task_summary:
            messages.append({
                "role": "system",
                "content": "__TASK_SUMMARY_PLACEHOLDER__",
            })

        # Handoff user message
        task = handoff_data.get("task", "")
        ctx_val = handoff_data.get("context", "")
        messages.append({
            "role": "user",
            "content": (
                f"[Handoff: {rule.from_agent} → {rule.to_agent}]\n"
                f"Task: {task}\n"
                f"Context: {ctx_val}"
            ),
        })

        return messages

    @property
    def has_rules(self) -> bool:
        return len(self._rules) > 0
