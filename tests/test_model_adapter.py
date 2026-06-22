"""Tests for ModelAdapter retry, error handling, and rate limiting behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import APIStatusError

from arf.core.model_adapter import (
    ModelAdapter,
    ModelAdapterError,
    RETRYABLE_STATUS,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
)


def make_status_error(status_code: int, message: str = "") -> APIStatusError:
    """Build a realistic APIStatusError for a given status code."""
    import httpx
    response = httpx.Response(status_code=status_code, headers={},
                               content=message or f"Error {status_code}",
                               request=httpx.Request("POST", "https://test.api/chat/completions"))
    return APIStatusError(message or f"Error {status_code}", response=response, body=None)


async def _collect(async_gen):
    """Collect all items from an async generator into a list."""
    return [item async for item in async_gen]


# ---------------------------------------------------------------------------
# Retry logic — _call_with_retry
# ---------------------------------------------------------------------------

class TestCallWithRetry:
    """Tests for _call_with_retry exponential backoff behavior."""

    @pytest.fixture
    def adapter(self):
        return ModelAdapter({"base_url": "https://test.api", "api_key": "sk-test",
                              "model_name": "test-model"})

    def test_success_first_attempt(self, adapter):
        """First successful call should not retry."""
        mock_resp = MagicMock()
        adapter._create_completion = AsyncMock(return_value=mock_resp)

        result = asyncio.run(adapter._call_with_retry([], None))

        assert result is mock_resp
        assert adapter._create_completion.call_count == 1

    def test_retry_on_429_then_succeed(self, adapter):
        """Should retry on 429 and return result on success."""
        mock_resp = MagicMock()
        adapter._create_completion = AsyncMock(side_effect=[
            make_status_error(429, "Rate limited"),
            mock_resp,
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(adapter._call_with_retry([], None))

        assert result is mock_resp
        assert adapter._create_completion.call_count == 2
        mock_sleep.assert_called_once()

    def test_retry_on_5xx_then_succeed(self, adapter):
        """Should retry on 500/502/503/504 and return result on success."""
        for status in [500, 502, 503, 504]:
            mock_resp = MagicMock()
            adapter._create_completion = AsyncMock(side_effect=[
                make_status_error(status, f"Server error {status}"),
                mock_resp,
            ])

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(adapter._call_with_retry([], None))

            assert result is mock_resp, f"Failed for status {status}"
            assert adapter._create_completion.call_count == 2

    def test_retry_on_network_error_then_succeed(self, adapter):
        """Should retry on network/timeout errors and return result on success."""
        mock_resp = MagicMock()
        adapter._create_completion = AsyncMock(side_effect=[
            ConnectionError("Connection reset"),
            mock_resp,
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(adapter._call_with_retry([], None))

        assert result is mock_resp
        assert adapter._create_completion.call_count == 2

    def test_exhausts_retries_on_5xx(self, adapter):
        """Should raise ModelAdapterError after exhausting all retries on 5xx."""
        adapter._create_completion = AsyncMock(side_effect=[
            make_status_error(503, "Service Unavailable")
            for _ in range(MAX_RETRIES + 2)
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ModelAdapterError) as exc_info:
                asyncio.run(adapter._call_with_retry([], None))

        assert exc_info.value.status_code == 503
        assert adapter._create_completion.call_count == MAX_RETRIES + 1

    def test_exhausts_retries_on_network_error(self, adapter):
        """Should raise ModelAdapterError with status_code=0 on network errors."""
        adapter._create_completion = AsyncMock(side_effect=[
            ConnectionError("Connection reset")
            for _ in range(MAX_RETRIES + 2)
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ModelAdapterError) as exc_info:
                asyncio.run(adapter._call_with_retry([], None))

        assert exc_info.value.status_code == 0
        assert "ConnectionError" in exc_info.value.message

    def test_no_retry_on_400(self, adapter):
        """Non-retryable errors (400, 401) should raise immediately without retry."""
        adapter._create_completion = AsyncMock(side_effect=[
            make_status_error(400, "Bad Request"),
        ])

        with pytest.raises(ModelAdapterError) as exc_info:
            asyncio.run(adapter._call_with_retry([], None))

        assert exc_info.value.status_code == 400
        assert adapter._create_completion.call_count == 1

    def test_no_retry_on_401(self, adapter):
        """401 Unauthorized should raise immediately without retry."""
        adapter._create_completion = AsyncMock(side_effect=[
            make_status_error(401, "Unauthorized"),
        ])

        with pytest.raises(ModelAdapterError) as exc_info:
            asyncio.run(adapter._call_with_retry([], None))

        assert exc_info.value.status_code == 401
        assert adapter._create_completion.call_count == 1

    def test_exponential_backoff_increases(self, adapter):
        """Verify backoff delay increases exponentially: base^(1), base^(2), base^(3)."""
        delays = []
        adapter._create_completion = AsyncMock(side_effect=[
            make_status_error(500, "Error")
            for _ in range(MAX_RETRIES + 2)
        ])

        async def capture_sleep(seconds):
            delays.append(seconds)

        with patch("asyncio.sleep", side_effect=capture_sleep):
            try:
                asyncio.run(adapter._call_with_retry([], None))
            except ModelAdapterError:
                pass

        assert len(delays) == MAX_RETRIES
        expected = [RETRY_BACKOFF_BASE ** i for i in range(1, MAX_RETRIES + 1)]
        for actual, exp in zip(delays, expected):
            assert actual == pytest.approx(exp, rel=1e-9), \
                f"Expected {exp:.2f}, got {actual:.2f}"

    def test_success_after_partial_failures_stops_retrying(self, adapter):
        """Once a call succeeds, stop retrying immediately."""
        mock_resp = MagicMock()
        adapter._create_completion = AsyncMock(side_effect=[
            make_status_error(500, "Error 1"),
            make_status_error(500, "Error 2"),
            mock_resp,
            make_status_error(500, "Should not be called"),
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(adapter._call_with_retry([], None))

        assert result is mock_resp
        assert adapter._create_completion.call_count == 3


# ---------------------------------------------------------------------------
# chat_complete — integration of _call_with_retry + response parsing
# ---------------------------------------------------------------------------

class TestChatComplete:
    @pytest.fixture
    def adapter(self):
        return ModelAdapter({"base_url": "https://test.api", "api_key": "sk-test",
                              "model_name": "test-model"})

    def test_returns_message_with_usage(self, adapter):
        msg = MagicMock()
        msg.content = "test response"
        msg.tool_calls = None
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=msg)]
        mock_resp.usage = mock_usage

        adapter._create_completion = AsyncMock(return_value=mock_resp)

        result = asyncio.run(adapter.chat_complete([], None))

        assert result.content == "test response"
        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }

    def test_returns_none_usage_when_absent(self, adapter):
        msg = MagicMock()
        msg.content = "no usage"
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=msg)]
        mock_resp.usage = None

        adapter._create_completion = AsyncMock(return_value=mock_resp)

        result = asyncio.run(adapter.chat_complete([], None))

        assert result.content == "no usage"
        assert result.usage is None


# ---------------------------------------------------------------------------
# chat_stream_full — streaming with error handling
# ---------------------------------------------------------------------------

class TestChatStreamFull:
    @pytest.fixture
    def adapter(self):
        return ModelAdapter({"base_url": "https://test.api", "api_key": "sk-test",
                              "model_name": "test-model"})

    def test_model_adapter_error_propagates_to_caller(self, adapter):
        """chat_stream_full lets ModelAdapterError propagate so the harness
        catch-point can emit a proper error event.  Degradation is handled
        at the ModelDegrader level via except Exception."""
        adapter._call_with_retry = AsyncMock(
            side_effect=ModelAdapterError(status_code=503, message="Service Unavailable")
        )

        with pytest.raises(ModelAdapterError, match="Service Unavailable"):
            asyncio.run(_collect(adapter.chat_stream_full([], None)))

    def test_streams_text_chunks(self, adapter):
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock(delta=MagicMock(content="Hello", reasoning_content=None),
                                     finish_reason=None)]
        chunk1.usage = None
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock(delta=MagicMock(content=" World", reasoning_content=None),
                                     finish_reason="stop")]
        chunk2.usage = None

        async def _mock_stream():
            yield chunk1
            yield chunk2

        adapter._call_with_retry = AsyncMock(return_value=_mock_stream())

        events = asyncio.run(_collect(adapter.chat_stream_full([], None)))

        content_events = [e for e in events if e["type"] == "chunk"]
        assert len(content_events) == 2
        assert content_events[0]["content"] == "Hello"
        assert content_events[1]["content"] == " World"

    def test_streams_reasoning_content(self, adapter):
        chunk = MagicMock()
        chunk.choices = [MagicMock(
            delta=MagicMock(content="", reasoning_content="Let me think..."),
            finish_reason=None,
        )]
        chunk.usage = None

        async def _mock_stream():
            yield chunk

        adapter._call_with_retry = AsyncMock(return_value=_mock_stream())

        events = asyncio.run(_collect(adapter.chat_stream_full([], None)))

        reasoning_events = [e for e in events if "reasoning" in e]
        assert len(reasoning_events) == 1
        assert reasoning_events[0]["reasoning"] == "Let me think..."

    def test_skips_empty_choices(self, adapter):
        chunk = MagicMock()
        chunk.choices = []
        chunk.usage = None

        async def _mock_stream():
            yield chunk

        adapter._call_with_retry = AsyncMock(return_value=_mock_stream())

        events = asyncio.run(_collect(adapter.chat_stream_full([], None)))

        assert len(events) == 0


# ---------------------------------------------------------------------------
# ModelAdapterError
# ---------------------------------------------------------------------------

class TestModelAdapterError:
    def test_stores_status_code_and_message(self):
        err = ModelAdapterError(status_code=429, message="Too Many Requests")
        assert err.status_code == 429
        assert err.message == "Too Many Requests"
        assert "429" in str(err)
        assert "Too Many Requests" in str(err)


# ---------------------------------------------------------------------------
# RETRYABLE_STATUS correctness
# ---------------------------------------------------------------------------

def test_retryable_status_set():
    """Sanity check: only transient+rate_limit codes are retryable."""
    assert RETRYABLE_STATUS == {429, 500, 502, 503, 504}


def test_max_retries_default():
    assert MAX_RETRIES == 3


# ---------------------------------------------------------------------------
# describe() -- snapshot-safe model config introspection
# ---------------------------------------------------------------------------

def test_model_adapter_describe():
    config = {
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test",
        "model_name": "deepseek-chat",
        "temperature": 0.7,
        "max_tokens": 4096,
        "thinking_enabled": True,
        "reasoning_effort": "high",
    }
    adapter = ModelAdapter(config)
    desc = adapter.describe()
    assert desc["provider"] == "openai"
    assert desc["base_url"] == "https://api.example.com/v1"
    assert desc["model_name"] == "deepseek-chat"
    assert desc["temperature"] == 0.7
    assert desc["max_tokens"] == 4096
    assert desc["thinking"] is True
    assert "api_key" not in desc  # never leak secrets


def test_model_adapter_describe_minimal():
    adapter = ModelAdapter({"base_url": "http://localhost:8080"})
    desc = adapter.describe()
    assert desc["model_name"] == ""
    assert desc["temperature"] is None
    assert desc["thinking"] is False
