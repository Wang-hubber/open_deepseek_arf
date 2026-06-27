"""Shared fixtures for Python API tests."""
import pytest
from arf import Bus


@pytest.fixture
def bus():
    """Create a fresh Bus for each test."""
    b = Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=64)
    return b
