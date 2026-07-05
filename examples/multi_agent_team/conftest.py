"""Shared pytest fixtures/hooks for the multi_agent_team example tests."""
from __future__ import annotations


def pytest_addoption(parser):
    """Register the --run-e2e flag used by the e2e smoke test."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests that hit a real DeepSeek API.",
    )