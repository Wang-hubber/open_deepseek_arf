"""End-to-end smoke test for multi_agent_team example."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arf._arf import TeamConfig, SseFormatter


def test_team_config_loads():
    cfg = TeamConfig.from_yaml(str(Path(__file__).parent.parent / "teams" / "default.yaml"))
    assert cfg.team_id == "default"
    assert len(cfg.persistent_engines) >= 4
    assert len(cfg.subagent_pools) >= 2


def test_sse_formatter_roundtrip():
    sse = SseFormatter.format('{"x": 1}', 7, "peer_message")
    assert 'id: 7' in sse
    assert 'event: peer_message' in sse
    nid, seq = SseFormatter.parse_last_event_id("engine-A:7")
    assert (nid, seq) == ("engine-A", 7)


@pytest.mark.skipif(
    "not config.getoption('--run-e2e')",
    reason="E2E requires DEEPSEEK_API_KEY",
)
def test_server_starts_and_chat():
    from server import app
    client = TestClient(app)
    with client:
        r = client.post("/chat", json={"message": "hello"})
        assert r.status_code == 200
        assert "response" in r.json()