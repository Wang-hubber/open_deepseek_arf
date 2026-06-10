"""GateChecker — execution termination conditions."""


class GateChecker:
    """Check whether execution should be forcibly terminated."""

    def __init__(
        self,
        max_turns: int = 50,
        max_tokens: int | None = None,
        max_time_seconds: int | None = None,
        max_consecutive_errors: int | None = None,
    ):
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.max_time_seconds = max_time_seconds
        self.max_consecutive_errors = max_consecutive_errors
        self._reason = ""

    def is_exceeded(self, current_turn: int = 0, **stats) -> bool:
        if current_turn >= self.max_turns:
            self._reason = "max_turns"
            return True
        self._reason = ""
        return False

    @property
    def reason(self) -> str:
        return self._reason
