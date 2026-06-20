"""GateChecker — DEPRECATED: gate logic is a plugin or harness built-in at after_round."""
import warnings
warnings.warn("GateChecker is deprecated. Use AgentHarness max_turns or a gate plugin.", DeprecationWarning, stacklevel=2)


class GateChecker:
    """Check whether execution should be forcibly terminated."""

    def __init__(
        self,
        max_turns: int = 50,
        max_tokens: int | None = None,  # None = no token budget limit
        max_time_seconds: int | None = None,
        max_consecutive_errors: int | None = None,
    ):
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.max_time_seconds = max_time_seconds
        self.max_consecutive_errors = max_consecutive_errors
        self._reason = ""

    def is_exceeded(self, current_turn: int = 0, total_tokens: int = 0, **stats) -> bool:
        if current_turn >= self.max_turns:
            self._reason = "max_turns"
            return True
        if self.max_tokens is not None and total_tokens >= self.max_tokens:
            self._reason = "max_tokens"
            return True
        self._reason = ""
        return False

    @property
    def reason(self) -> str:
        return self._reason
