"""Shared fixtures for Phase 6 Python E2E tests.

[构造] [方法] [边界]
"""
import os
import pytest


def require_minimax_key() -> str | None:
    """Read MINIMAX_API_KEY (or MINIMAX_TOKEN fallback). Return None if missing."""
    return os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_TOKEN")


@pytest.fixture
def minimax_key():
    """Skip the test if MINIMAX_API_KEY is not set."""
    key = require_minimax_key()
    if not key:
        pytest.skip("MINIMAX_API_KEY not set")
    return key


@pytest.fixture
def live_bus():
    """Fresh Bus for each test."""
    from arf import Bus
    return Bus(heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=32)