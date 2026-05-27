"""ApprovalPoint implementations for human-in-the-loop workflows."""
from arf.core.state import TurnContext
from arf.core.results import ApprovalRequest


class AlwaysAutoApprove:
    def should_pause(self, context: TurnContext) -> bool:
        return False

    def approval_form(self, context: TurnContext) -> ApprovalRequest:
        return ApprovalRequest(agent_name="", session_id="", turn=0, tool_name="", params={}, reason="")


class ToolNameAllowlist:
    def __init__(self, allowlist: list[str]) -> None:
        self._allowlist = set(allowlist)

    def should_pause(self, context: TurnContext) -> bool:
        for tc in context.last_tool_calls:
            if tc.get("name", "") in self._allowlist:
                return True
        return False

    def approval_form(self, context: TurnContext) -> ApprovalRequest:
        tc = context.last_tool_calls[0] if context.last_tool_calls else {}
        return ApprovalRequest(
            agent_name=context.agent_name, session_id=context.session_id,
            turn=context.turn, tool_name=tc.get("name", ""),
            params=tc.get("params", {}),
            reason=f"Tool '{tc.get('name', '')}' requires approval",
        )
