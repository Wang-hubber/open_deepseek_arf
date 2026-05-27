"""NoneInputGuard — pass-through input guard (no filtering)."""
from arf.core.results import GuardResult


class NoneInputGuard:
    async def check(self, message: str, context: dict) -> GuardResult:
        return GuardResult(allowed=True)
