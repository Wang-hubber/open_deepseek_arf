"""ModelCallProtector — composite rate limiter + circuit breaker wrapper."""
import logging

logger = logging.getLogger("arf.protection")

from .rate_limiter import TokenBucket
from .circuit_breaker import CircuitBreaker
from .errors import RateLimitError, CircuitOpenError


class ModelCallProtector:
    """Wraps model calls with rate limiting (per api_base) and circuit breaking (per model_name).

    Injected at BaseAgent._inject_model_calls(). Resolves engine model_type
    (e.g. "deep", "quick") → api_base + model_name via model_map.
    """

    def __init__(self, event_bus=None, model_map=None,
                 rate_limit_config=None, breaker_config=None):
        self.event_bus = event_bus
        self._model_map = model_map or {}
        self.rate_limiters: dict[str, TokenBucket] = {}
        self.breakers: dict[str, CircuitBreaker] = {}

        rl_cfg = rate_limit_config or {}
        cb_cfg = breaker_config or {}
        self._rl_requests_per_second = float(rl_cfg.get("requests_per_second", 5.0))
        self._rl_max_burst = int(rl_cfg.get("max_burst", 10))
        self._cb_failure_threshold = int(cb_cfg.get("failure_threshold", 3))
        self._cb_base_cooldown = _parse_duration(cb_cfg.get("base_cooldown", "10s"))
        self._cb_cooldown_multiplier = float(cb_cfg.get("cooldown_multiplier", 2.0))
        self._cb_max_cooldown = _parse_duration(cb_cfg.get("max_cooldown", "300s"))
        self._cb_half_open_max = int(cb_cfg.get("half_open_max_requests", 1))

    def set_model_map(self, model_map: dict) -> None:
        self._model_map = model_map

    def _resolve(self, model_type: str) -> tuple[str, str]:
        info = self._model_map.get(model_type, {})
        return (
            info.get("base_url", "unknown"),
            info.get("model_name", model_type),
        )

    def _get_rate_limiter(self, api_base: str) -> TokenBucket:
        if api_base not in self.rate_limiters:
            self.rate_limiters[api_base] = TokenBucket(
                capacity=self._rl_max_burst,
                rate=self._rl_requests_per_second,
            )
        return self.rate_limiters[api_base]

    def _get_breaker(self, model_name: str) -> CircuitBreaker:
        if model_name not in self.breakers:
            self.breakers[model_name] = CircuitBreaker(
                failure_threshold=self._cb_failure_threshold,
                base_cooldown=self._cb_base_cooldown,
                cooldown_multiplier=self._cb_cooldown_multiplier,
                max_cooldown=self._cb_max_cooldown,
                half_open_max_requests=self._cb_half_open_max,
            )
        return self.breakers[model_name]

    async def _check_rate_limit(self, api_base: str, model_name: str) -> None:
        limiter = self._get_rate_limiter(api_base)
        if not await limiter.acquire():
            self._emit("rate_limited", {
                "model": model_name, "api_base": api_base,
            })
            raise RateLimitError(model=model_name, api_base=api_base)

    async def _check_breaker(self, model_name: str) -> None:
        breaker = self._get_breaker(model_name)
        prev_state = breaker.state.value
        allowed = await breaker.before_call()
        if not allowed:
            self._emit("breaker_blocked", {
                "model": model_name, "circuit_state": breaker.state.value,
            })
            raise CircuitOpenError(model=model_name, circuit_state=breaker.state.value)
        if breaker.state.value == "half_open" and prev_state != "half_open":
            self._emit("circuit_half_open", {
                "model": model_name,
                "open_duration_ms": int(breaker.open_duration * 1000),
            })

    async def _on_failure(self, model_name: str, exc: Exception) -> None:
        breaker = self._get_breaker(model_name)
        prev_state = breaker.state.value
        await breaker.on_failure(str(exc))
        if breaker.state.value == "open" and prev_state != "open":
            self._emit("circuit_opened", {
                "model": model_name,
                "failure_count": breaker.failure_count,
                "fail_reason": breaker.last_failure_reason,
            })

    async def _on_success(self, model_name: str) -> None:
        breaker = self._get_breaker(model_name)
        prev_state = breaker.state.value
        await breaker.on_success()
        if breaker.state.value == "closed" and prev_state != "closed":
            self._emit("circuit_closed", {"model": model_name})

    async def call_with_protection(self, raw_call, messages,
                                     model_name="", tools=None):
        api_base, mn = self._resolve(model_name)
        await self._check_rate_limit(api_base, mn)
        await self._check_breaker(mn)
        try:
            result = await raw_call(messages, model_name, tools=tools)
        except Exception as exc:
            await self._on_failure(mn, exc)
            raise
        await self._on_success(mn)
        return result

    async def stream_with_protection(self, raw_stream, messages,
                                       model_name="", tools=None):
        api_base, mn = self._resolve(model_name)
        await self._check_rate_limit(api_base, mn)
        await self._check_breaker(mn)
        try:
            async for chunk in raw_stream(messages, model_name, tools=tools):
                yield chunk
        except Exception as exc:
            await self._on_failure(mn, exc)
            raise
        await self._on_success(mn)

    def _emit(self, event_type: str, data: dict) -> None:
        if self.event_bus:
            from arf.core.events import AgentEvent
            self.event_bus.emit(AgentEvent(type=event_type, data=data))


def _parse_duration(value: str | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip().lower()
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    if value.endswith("s"):
        return float(value[:-1])
    if value.endswith("m"):
        return float(value[:-1]) * 60.0
    if value.endswith("h"):
        return float(value[:-1]) * 3600.0
    return float(value)
