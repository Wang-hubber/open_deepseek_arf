"""Error types raised by the protection layer."""


class RateLimitError(Exception):
    """Raised when the rate limiter refuses a request."""
    def __init__(self, model: str, api_base: str):
        self.model = model
        self.api_base = api_base
        super().__init__(f"Rate limit exceeded for {model} at {api_base}")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker rejects a request."""
    def __init__(self, model: str, circuit_state: str):
        self.model = model
        self.circuit_state = circuit_state
        super().__init__(f"Circuit open for {model} (state={circuit_state})")
