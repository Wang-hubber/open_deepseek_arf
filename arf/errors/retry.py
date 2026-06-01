"""DefaultErrorPolicy — tool and model retry with exponential backoff."""
from arf.core.state import TurnContext
from arf.core.results import ErrorAction, GuardResult


class DefaultErrorPolicy:
    def __init__(self, tool_retry: int = 2, tool_backoff: str = "exponential",
                 model_5xx_action: str = "fallback", guardrail_block_action: str = "abort") -> None:
        self._tool_retry = tool_retry
        self._tool_backoff = tool_backoff
        self._model_5xx_action = model_5xx_action
        self._guardrail_block_action = guardrail_block_action

    def on_tool_error(self, error: Exception, tool_name: str, attempt: int) -> ErrorAction:
        if attempt < self._tool_retry:
            if self._tool_backoff == "none":
                delay = 0.0
            elif self._tool_backoff == "linear":
                delay = (attempt + 1) * 2.0
            else:  # exponential (default)
                delay = 2 ** attempt * 1.0
            return ErrorAction(action="retry", delay=delay)
        return ErrorAction(action="abort", message=str(error))

    def on_model_error(self, error: Exception, model_name: str, attempt: int) -> ErrorAction:
        msg = str(error).lower()
        is_5xx = any(str(code) in msg for code in [500, 502, 503, 504])
        if is_5xx and self._model_5xx_action == "fallback":
            return ErrorAction(action="fallback")
        # No engine-level retry — protection layer handles transient retry and fault isolation
        return ErrorAction(action="abort", message=str(error))

    def on_guardrail_block(self, result: GuardResult, context: TurnContext) -> ErrorAction:
        if self._guardrail_block_action == "ask_user":
            return ErrorAction(action="ask_user", message=result.reason)
        return ErrorAction(action="abort", message=result.reason)
