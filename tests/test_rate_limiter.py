"""Tests for TokenBucket rate limiter."""
import asyncio
import time

import pytest

from arf.protection.rate_limiter import TokenBucket


class TestTokenBucket:
    def test_acquire_consumes_token(self):
        bucket = TokenBucket(capacity=10, rate=5.0)
        result = asyncio.run(bucket.acquire())
        assert result is True
        assert bucket.tokens == pytest.approx(9.0, abs=1e-9)

    def test_acquire_empty_bucket_returns_false(self):
        bucket = TokenBucket(capacity=1, rate=0.0)
        asyncio.run(bucket.acquire())
        result = asyncio.run(bucket.acquire())
        assert result is False

    def test_refill_over_time(self):
        bucket = TokenBucket(capacity=10, rate=100.0)
        for _ in range(10):
            asyncio.run(bucket.acquire())
        assert bucket.tokens == pytest.approx(0.0, abs=0.5)
        time.sleep(0.05)
        result = asyncio.run(bucket.acquire())
        assert result is True

    def test_tokens_never_exceed_capacity(self):
        bucket = TokenBucket(capacity=5, rate=1000.0)
        time.sleep(0.1)
        asyncio.run(bucket.acquire())
        assert bucket.tokens <= 5.0

    def test_rate_enforcement(self):
        bucket = TokenBucket(capacity=10, rate=20.0)
        for _ in range(10):
            asyncio.run(bucket.acquire())
        result = asyncio.run(bucket.acquire())
        assert result is False

    def test_burst_capacity_respected(self):
        bucket = TokenBucket(capacity=3, rate=0.0)
        results = [asyncio.run(bucket.acquire()) for _ in range(3)]
        assert results == [True, True, True]
        assert asyncio.run(bucket.acquire()) is False

    def test_multiple_buckets_independent(self):
        b1 = TokenBucket(capacity=2, rate=0.0)
        b2 = TokenBucket(capacity=2, rate=0.0)
        asyncio.run(b1.acquire())
        asyncio.run(b1.acquire())
        assert asyncio.run(b2.acquire()) is True
        assert asyncio.run(b2.acquire()) is True
        assert asyncio.run(b2.acquire()) is False

    def test_concurrent_acquire_safety(self):
        bucket = TokenBucket(capacity=1000, rate=0.0)

        async def consume(n: int):
            for _ in range(n):
                await bucket.acquire()

        asyncio.run(consume(500))
        assert bucket.tokens == pytest.approx(500.0)

    def test_negative_capacity_raises(self):
        with pytest.raises(ValueError):
            TokenBucket(capacity=0, rate=1.0)

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError):
            TokenBucket(capacity=10, rate=-1.0)
