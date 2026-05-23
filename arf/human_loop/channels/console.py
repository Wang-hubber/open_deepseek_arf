import asyncio
from arf.core.results import ApprovalRequest, ApprovalResponse


class ConsoleChannel:
    async def send(self, request: ApprovalRequest) -> str:
        print(f"\n[APPROVAL] {request.reason}")
        print(f"  Tool: {request.tool_name}")
        print(f"  Params: {request.params}")
        return f"console:{request.session_id}:{request.turn}"

    async def wait(self, approval_id: str, timeout: int) -> ApprovalResponse:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(input, "Approve? [Y/n/modify]: "),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return ApprovalResponse(action="reject", comment="timeout")
        result = result.strip().lower()
        if result in ("y", "yes", ""):
            return ApprovalResponse(action="approve")
        elif result in ("n", "no"):
            return ApprovalResponse(action="reject")
        return ApprovalResponse(action="reject")
