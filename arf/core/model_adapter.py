"""Model adapter -- unified interface for OpenAI-compatible endpoints.

Handles all provider-specific adaptation:
- Message format conversion (internal → provider API format)
- Provider-specific params (thinking → extra_body, etc.)
- Retry / connection management
"""

import asyncio
import json
import os
import logging

import httpx
from openai import AsyncOpenAI, APIStatusError

logger = logging.getLogger("arf.model_adapter")

# HTTP status codes that warrant a retry
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = int(os.environ.get("ARF_API_MAX_RETRIES", "3"))
RETRY_BACKOFF_BASE = float(os.environ.get("ARF_API_RETRY_BACKOFF", "1.5"))


class ModelAdapterError(Exception):
    """Non-retryable error from the model API (e.g. 400 Bad Request)."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API error {status_code}: {message}")


class ModelAdapter:
    """Wraps OpenAI-compatible chat/completions API."""

    # Keys used for client init or internal metadata -- NOT forwarded to the API
    _META_KEYS = frozenset({"base_url", "api_key", "model_name", "context_window", "message_format"})

    # Keys that belong to the API call but are handled explicitly in
    # _create_completion / _call_with_retry -- must NOT appear in **params
    _CTRL_KEYS = frozenset({"stream"})

    # Standard OpenAI API params -- forwarded as-is
    _KNOWN_PARAMS = frozenset({
        "temperature", "max_tokens", "top_p", "frequency_penalty",
        "presence_penalty", "response_format", "stop",
    })

    # Provider-specific params that need translation
    _PROVIDER_KEYS = frozenset({"thinking_enabled", "reasoning_effort"})

    def __init__(self, config: dict, context_window: int = 1048576):
        self._config = config
        self._timeout = httpx.Timeout(
            connect=10.0, read=300.0, write=30.0, pool=10.0)
        self._context_window = int(config.get("context_window", context_window))
        self._init_client()
        self.model_name = config.get("model_name", "")
        self.context_window = self._context_window
        self.message_format = config.get("message_format", "openai")
        self.default_params = {}
        for k, v in config.items():
            if k not in self._META_KEYS:
                if k not in self._KNOWN_PARAMS and k not in self._PROVIDER_KEYS and k not in self._CTRL_KEYS:
                    logger.warning("Unknown config key '%s' -- will be forwarded to API as-is", k)
                self.default_params[k] = v

    def _init_client(self) -> None:
        """Create (or recreate) the AsyncOpenAI client with fresh connections."""
        self.client = AsyncOpenAI(
            base_url=self._config.get("base_url"),
            api_key=self._config.get("api_key", "") or "sk-placeholder",
            timeout=self._timeout,
        )

    async def _reset_client(self) -> None:
        """Close the old client and create a new one.

        Called before retrying after a connection-level error so we don't
        reuse a stale pooled connection that the server already closed.
        """
        try:
            await self.client.close()
        except Exception:
            pass
        self._init_client()

    # ---- retry helpers --------------------------------------------------

    def _should_retry(self, status_code: int) -> bool:
        return status_code in RETRYABLE_STATUS

    async def _call_with_retry(self, messages, tools, stream=False, max_tokens=None):
        """Call the API with exponential backoff retry on transient errors.

        Raises ModelAdapterError for non-retryable errors (400, 401, etc.).
        """
        last_exc = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await self._create_completion(messages, tools, stream, max_tokens)
            except APIStatusError as e:
                last_exc = e
                if self._should_retry(e.status_code) and attempt < MAX_RETRIES:
                    delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "API call attempt %d/%d failed with %d, retrying in %.1fs",
                        attempt + 1, MAX_RETRIES + 1, e.status_code, delay,
                    )
                    # Server-side 5xx / 429 can also leave pooled connections
                    # in a bad state. Reset to force fresh TCP on retry.
                    await self._reset_client()
                    await asyncio.sleep(delay)
                    continue
                raise ModelAdapterError(
                    status_code=e.status_code,
                    message=str(e),
                ) from e
            except Exception as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "API call attempt %d/%d failed with %s, retrying in %.1fs",
                        attempt + 1, MAX_RETRIES + 1, type(e).__name__, delay,
                    )
                    # Reset the HTTP client before retrying — stale pooled
                    # connections survive across retries if not drained.
                    await self._reset_client()
                    await asyncio.sleep(delay)
                    continue
                raise ModelAdapterError(
                    status_code=0,
                    message=f"{type(e).__name__}: {e}",
                ) from e
        # Should be unreachable, but safe
        raise ModelAdapterError(status_code=0, message=str(last_exc))

    # ---- message format conversion ---------------------------------------

    def format_messages(self, messages: list[dict]) -> list[dict]:
        """Convert internal state messages to provider API format.

        Internal format: assistant/tool may carry dict content
        (e.g. {content, tool_calls} for assistant, {tool_call_id, result, error}
        for tool). This adapter flattens them to OpenAI-compatible shape.
        Subclasses override for other providers.
        """
        result: list[dict] = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")

            if role == "assistant" and isinstance(content, dict):
                tc = content.get("tool_calls", [])
                text = content.get("content")
                # Avoid falsy-coercion: "" is a valid (empty) content, don't
                # turn it into None via ``or None``.
                api_msg: dict = {"role": "assistant", "content": text}
                if tc:
                    api_msg["tool_calls"] = [
                        {
                            "id": t["id"],
                            "type": "function",
                            "function": {
                                "name": t["name"],
                                "arguments": json.dumps(t.get("params", {})),
                            },
                        }
                        for t in tc
                    ]
                # DeepSeek format: passthrough reasoning_content for thinking mode continuity
                if self.message_format == "deepseek" and content.get("reasoning_content"):
                    api_msg["reasoning_content"] = content["reasoning_content"]
                # Skip assistant messages that have neither content nor tool_calls
                if api_msg.get("content") is None and not api_msg.get("tool_calls"):
                    continue
                result.append(api_msg)
            elif role == "tool" and isinstance(content, dict):
                r = content.get("result")
                error = content.get("error", "")
                if isinstance(r, dict):
                    tool_content = json.dumps(r, ensure_ascii=False)
                elif isinstance(r, str) and r:
                    tool_content = r
                elif r:
                    tool_content = str(r)
                elif error:
                    tool_content = f"[FAILED] {error}"
                else:
                    tool_content = ""
                result.append({
                    "role": "tool",
                    "tool_call_id": content.get("tool_call_id", ""),
                    "content": tool_content,
                })
            else:
                # Skip assistant messages with no content (API rejects them)
                if role == "assistant" and not content:
                    continue
                result.append({"role": role, "content": content})
        return result

    def _build_api_params(self) -> tuple[dict, dict]:
        """Build API params from config. Returns (standard_params, extra_body).
        Standard params are known OpenAI keys. Provider-specific keys go in
        extra_body (passed via the OpenAI SDK's extra_body mechanism)."""
        standard: dict = {}
        extra_body: dict = {}
        src = dict(self.default_params)
        # Strip control keys
        for ck in self._CTRL_KEYS:
            src.pop(ck, None)
        # Translate thinking_enabled + reasoning_effort -> DeepSeek thinking
        thinking_enabled = src.pop("thinking_enabled", None)
        if thinking_enabled is None and "reasoning_effort" in src:
            thinking_enabled = True
        if thinking_enabled is not None:
            if thinking_enabled:
                effort = src.pop("reasoning_effort", "high")
                extra_body["thinking"] = {"type": "enabled", "effort": effort}
            else:
                src.pop("reasoning_effort", None)
                extra_body["thinking"] = {"type": "disabled"}
        # Sort: known params -> standard, unknown -> extra_body
        for k, v in src.items():
            if k in self._KNOWN_PARAMS:
                standard[k] = v
            else:
                extra_body[k] = v
        # Normalize response_format: DeepSeek API requires object form
        rf = standard.get("response_format")
        if isinstance(rf, str):
            standard["response_format"] = {"type": rf}
        return standard, extra_body

    async def _create_completion(self, messages, tools, stream=False, max_tokens=None):
        """Raw API call -- separated so retry logic is clean."""
        params, extra = self._build_api_params()
        if max_tokens is not None:
            default_max = self.default_params.get("max_tokens")
            if default_max is not None:
                max_tokens = min(max_tokens, default_max)
            params["max_tokens"] = max_tokens
        kwargs = {}
        if tools:
            kwargs["tools"] = tools
        if extra:
            kwargs["extra_body"] = extra
        return await self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            stream=stream,
            **params,
            **kwargs,
        )

    def describe(self) -> dict[str, Any]:
        """Return a snapshot-safe description of this model adapter's config."""
        default_temp = self.default_params.get("temperature", None)
        default_max = self.default_params.get("max_tokens", None)
        return {
            "provider": "openai",
            "base_url": self._config.get("base_url", ""),
            "model_name": self.model_name,
            "temperature": default_temp,
            "max_tokens": default_max,
            "thinking": self.default_params.get("thinking_enabled", False),
        }

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """Send a chat completion request and return the response text."""
        messages = self.format_messages(messages)
        params, extra = self._build_api_params()
        params.update(kwargs)
        kwargs2 = {}
        if extra:
            kwargs2["extra_body"] = extra
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            **params,
            **kwargs2,
        )
        return response.choices[0].message.content

    async def chat_complete(self, messages: list[dict], tools: list[dict] | None = None,
                            max_tokens: int | None = None):
        """Send a chat completion and return the full message (content + tool_calls).

        Retries on transient errors (429, 5xx, network). Raises ModelAdapterError
        for non-retryable errors so the engine can handle them gracefully.

        Args:
            messages: Conversation messages (internal format).
            tools: Optional tool definitions (OpenAI format, already converted by harness).
            max_tokens: Optional per-call override.
        """
        messages = self.format_messages(messages)
        response = await self._call_with_retry(messages, tools, stream=False,
                                               max_tokens=max_tokens)
        msg = response.choices[0].message
        msg.finish_reason = response.choices[0].finish_reason
        if response.usage:
            msg.usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        else:
            msg.usage = None
        return msg

    async def chat_stream_full(self, messages: list[dict], tools: list[dict] | None = None,
                               max_tokens: int | None = None):
        """Stream with full delta support -- yields text chunks and accumulated tool calls.

        Args:
            messages: Conversation messages (internal format).
            tools: Optional tool definitions (OpenAI format, already converted by harness).
        """
        messages = self.format_messages(messages)
        try:
            stream = await self._call_with_retry(messages, tools, stream=True,
                                                 max_tokens=max_tokens)
        except ModelAdapterError as e:
            yield {
                "type": "error",
                "code": e.status_code,
                "detail": e.message,
            }
            return
        tool_calls_acc: dict[int, dict] = {}

        async for chunk in stream:
            # Capture usage from the final chunk before any skip guards.
            # Many providers (e.g. DeepSeek) send usage on a chunk with choices=[].
            if hasattr(chunk, "usage") and chunk.usage:
                yield {
                    "type": "usage",
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue

            # DeepSeek deep-thinking models emit reasoning_content before content.
            reasoning = getattr(delta, "reasoning_content", None) or ""
            text = delta.content or ""

            if reasoning or text:
                event = {"type": "chunk", "content": text}
                if reasoning:
                    event["reasoning"] = reasoning
                yield event

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"name": "", "arguments": ""}
                    if tc.function and tc.function.name:
                        tool_calls_acc[idx]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc.function.arguments
                        # Yield incremental updates so frontend can show progress
                        yield {
                            "type": "tool_call_chunk",
                            "name": tool_calls_acc[idx]["name"],
                            "arguments": tool_calls_acc[idx]["arguments"],
                            "id": f"call_{idx}",
                            "delta": tc.function.arguments,
                        }

            if chunk.choices[0].finish_reason == "tool_calls":
                for idx in sorted(tool_calls_acc.keys()):
                    tc = tool_calls_acc[idx]
                    yield {
                        "type": "tool_call",
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                        "id": f"call_{idx}",
                    }
                tool_calls_acc = {}
