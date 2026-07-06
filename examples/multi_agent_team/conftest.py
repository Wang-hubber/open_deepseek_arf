"""Shared pytest fixtures/hooks for the multi_agent_team example tests."""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import uvicorn


def pytest_addoption(parser):
    """Register the --run-e2e flag used by e2e tests."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests that hit a real LLM provider.",
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _provider_ready() -> bool:
    """Check ARF_PROVIDER and the matching API key are set."""
    name = os.environ.get("ARF_PROVIDER")
    if not name:
        return False
    env_vars = {
        "deepseek": "DEEPSEEK_API_KEY",
        "aliyun_bailian": "DASHSCOPE_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }
    return bool(os.environ.get(env_vars.get(name, "")))


@pytest.fixture(scope="session")
def e2e_guard(request) -> None:
    """Skip e2e tests when --run-e2e is not passed OR ARF_PROVIDER + key
    are not set. Tests should use this fixture (or set pytestmark = e2e)."""
    if not request.config.option.run_e2e:
        pytest.skip("set --run-e2e to run end-to-end tests")
    if not _provider_ready():
        pytest.skip("set ARF_PROVIDER and matching API key env var")


@pytest.fixture
def clean_storage(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    """Isolated events directory per test; monkeypatch server.STORAGE_ROOT."""
    storage = tmp_path / "events"
    storage.mkdir()
    import server
    monkeypatch.setattr(server, "STORAGE_ROOT", storage)
    yield storage
    shutil.rmtree(storage, ignore_errors=True)


@pytest.fixture
def live_server(clean_storage: Path, monkeypatch) -> Iterator[str]:
    """Run server.py on a free port; yield base URL.

    Daemon thread + uvicorn.Server.run. Server stops on test fixture
    teardown (no explicit shutdown — the daemon dies with the test).
    """
    port = _free_port()
    config = uvicorn.Config(
        "server:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server_thread = threading.Thread(
        target=uvicorn.Server(config).run, daemon=True
    )
    server_thread.start()

    # Wait for /health to be reachable
    import httpx
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError(f"server didn't start in 15s on port {port}")

    yield base
    # Daemon thread dies automatically when test process exits