"""Token bucket rate limiter for per-endpoint request throttling."""
import asyncio
import time


class TokenBucket:
    """Async-safe token bucket for rate limiting.

    Refills tokens at `rate` per second, capped at `capacity`.
    Each acquire() consumes 1 token. Returns False immediately
    when empty — never blocks.
    """

    def __init__(self, capacity: float, rate: float):
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if rate < 0:
            raise ValueError("rate must be >= 0")
        self.capacity = float(capacity)
        self.rate = float(rate)
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0 and self.rate > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    async def acquire(self) -> bool:
        """Try to consume 1 token. True = acquired, False = bucket empty."""
        async with self._lock:
            self._refill()
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False
