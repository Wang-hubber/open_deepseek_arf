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

    @staticmethod
    def _is_degradable_status(status_code: int) -> bool:
        """Return True if status_code should trigger model degradation."""
        return status_code >= 500 or status_code == 429

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
            return self._is_degradable_status(status)
        # No HTTP status → assume transient (network/timeout/etc), degrade
        return True

    async def chat_stream_full(self, messages: list[dict], tools=None,
                               max_tokens=None):
        """Stream with ordered fallback on connection failure.

        Degrades only when the stream fails before producing any content.
        Once chunks start flowing, we commit to that adapter — mid-stream
        failures are not recovered.
        """
        last_error = None
        for i, adapter in enumerate(self._adapters):
            started = False
            try:
                async for chunk in adapter.chat_stream_full(
                    messages, tools=tools, max_tokens=max_tokens,
                ):
                    if chunk.get("type") == "error":
                        if not started and i < len(self._adapters) - 1:
                            code = chunk.get("code", 0)
                            if self._is_degradable_status(code):
                                logger.warning(
                                    "Model adapter %d/%d stream errored, degrading: %s",
                                    i + 1, len(self._adapters), code)
                                break  # exit inner loop, try next adapter
                        yield chunk
                        return
                    started = True
                    yield chunk
                else:
                    return  # stream completed normally
            except Exception as e:
                if not started and i < len(self._adapters) - 1 and self._should_degrade(e):
                    last_error = e
                    logger.warning(
                        "Model adapter %d/%d stream failed, degrading: %s",
                        i + 1, len(self._adapters), e)
                    continue
                raise
        if last_error:
            raise last_error
