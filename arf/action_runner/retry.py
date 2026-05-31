"""RetryExecutor — exponential backoff with transient/deterministic classification."""

from __future__ import annotations

import asyncio
import random
import time

from arf.core.execution import Executable, ExecuteResult, ExecutionError


class RetryExecutor:
    """Executes a single Executable with configurable retry.

    Classification:
    - transient error (network, timeout, rate limit, 5xx) -> retry with backoff
    - deterministic error (bad params, permissions, 4xx) -> no retry, fail fast
    - unhandled exception -> treated as transient (safety: prefer retry over fail)
    """

    @staticmethod
    async def execute(executable: Executable) -> ExecuteResult:
        """Execute with retry logic based on error classification.

        Args:
            executable: The executable to run.

        Returns:
            ExecuteResult from the final attempt.
        """
        policy = executable.retry_policy
        last_result: ExecuteResult | None = None

        for attempt in range(1, policy.max_attempts + 1):
            start = time.monotonic()
            try:
                result = await executable.execute()
            except Exception as exc:
                # Unhandled exceptions are transient by default
                result = ExecuteResult(
                    name=executable.name,
                    success=False,
                    error=ExecutionError(
                        kind="transient",
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                    duration_ms=(time.monotonic() - start) * 1000,
                    attempt=attempt,
                )

            result.attempt = attempt
            last_result = result

            if result.success:
                return result

            error = result.error
            if error and error.kind == "deterministic":
                return result  # no retry for deterministic errors

            if attempt < policy.max_attempts:
                delay = min(
                    policy.backoff_base * (2 ** (attempt - 1)),
                    policy.backoff_max,
                )
                if policy.jitter:
                    delay *= 0.5 + random.random()  # 0.5x ~ 1.5x
                await asyncio.sleep(delay)

        return last_result or ExecuteResult(
            name=executable.name,
            success=False,
            error=ExecutionError(kind="transient", message="max attempts exhausted"),
        )
