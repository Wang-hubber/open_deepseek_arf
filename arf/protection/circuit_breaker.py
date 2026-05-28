"""Circuit breaker with exponential cooldown for model fault isolation."""
import asyncio
import enum
import time


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-model circuit breaker with exponential cooldown.

    CLOSED  → OPEN       after `failure_threshold` consecutive failures
    OPEN    → HALF_OPEN  automatically after `open_duration`
    HALF_OPEN → CLOSED   on first success
    HALF_OPEN → OPEN     on first failure (cooldown *= multiplier)
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        base_cooldown: float = 10.0,
        cooldown_multiplier: float = 2.0,
        max_cooldown: float = 300.0,
        half_open_max_requests: int = 1,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if base_cooldown <= 0:
            raise ValueError("base_cooldown must be > 0")
        self.failure_threshold = failure_threshold
        self.base_cooldown = float(base_cooldown)
        self.cooldown_multiplier = float(cooldown_multiplier)
        self.max_cooldown = float(max_cooldown)
        self.half_open_max_requests = half_open_max_requests

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_successes = 0
        self.last_failure_time: float = 0.0
        self.open_duration = float(base_cooldown)
        self.half_open_requests = 0
        self.last_failure_reason: str = ""
        self._lock = asyncio.Lock()

    async def before_call(self) -> bool:
        """Check if a request can proceed. Returns False if circuit is OPEN.
        Automatically transitions OPEN → HALF_OPEN when cooldown has elapsed."""
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                elapsed = time.monotonic() - self.last_failure_time
                if elapsed >= self.open_duration:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_requests = 0
                    return True
                return False
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_requests < self.half_open_max_requests:
                    self.half_open_requests += 1
                    return True
                return False
            return False

    async def on_success(self) -> None:
        """Record a successful call. Resets counters and closes circuit."""
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.consecutive_successes = 0
                self.open_duration = self.base_cooldown
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0
                self.consecutive_successes += 1

    async def on_failure(self, error: str) -> None:
        """Record a failed call. May trip the circuit OPEN."""
        async with self._lock:
            self.last_failure_time = time.monotonic()
            self.last_failure_reason = error
            self.failure_count += 1
            self.consecutive_successes = 0

            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.open_duration = min(
                    self.open_duration * self.cooldown_multiplier,
                    self.max_cooldown,
                )
            elif self.state == CircuitState.CLOSED and self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
