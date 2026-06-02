"""ModelDegrader — ordered model fallback on failure.

Tries each ModelAdapter in order. Degrades on network errors, timeouts,
429 (rate limit), and 5xx. Client errors (4xx) do not trigger fallback.
"""
import logging

logger = logging.getLogger("arf.models")


class ModelDegrader:
    """Wraps multiple ModelAdapter instances. Tries each in order on failure.

    Usage::

        degrader = ModelDegrader([adapter_pro, adapter_flash])
        result = await degrader.chat_complete(messages, tools=tools)
    """

    def __init__(self, adapters: list) -> None:
        if not adapters:
            raise ValueError("At least one model adapter required")
        self._adapters = adapters

    @property
    def adapters(self) -> list:
        return list(self._adapters)

    async def chat_complete(self, messages: list[dict], tools=None,
                            max_tokens=None):
        """Try adapters in order. On transient failure, fall through to next.
        Client errors (4xx) are raised immediately without retry.

        Returns the raw message object from the adapter (has .content,
        .tool_calls, .usage, .reasoning_content, .finish_reason).
        """
        last_error = None
        for i, adapter in enumerate(self._adapters):
            try:
                return await adapter.chat_complete(
                    messages, tools=tools, max_tokens=max_tokens)
            except Exception as e:
                last_error = e
                if i < len(self._adapters) - 1 and self._should_degrade(e):
                    logger.warning(
                        "Model adapter %d/%d failed, degrading: %s",
                        i + 1, len(self._adapters), e)
                    continue
                raise
        raise last_error  # unreachable

    def _should_degrade(self, error: Exception) -> bool:
        """Check if this error should trigger degradation to the next model.

        Degrade on: 5xx, 429, and any error without an HTTP status
        (network errors, timeouts, connection failures, etc.).
        Do NOT degrade on: 4xx client errors.
        """
        status = (
            getattr(error, 'status_code', None)
            or getattr(error, 'status', None)
            or getattr(error, 'http_status', None)
        )
        if status is not None:
            if 400 <= status < 500 and status != 429:
                return False  # client error, don't degrade
            return True  # 5xx or 429
        # No HTTP status → assume transient (network/timeout/etc), degrade
        return True
