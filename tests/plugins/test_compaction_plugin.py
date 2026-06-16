"""Tests for CompactionPlugin progressive compaction."""
import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arf.core.plugin_context import PluginContext
from arf.plugins.compaction.plugin import CompactionPlugin


@pytest.fixture
def tmp_data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return str(d)


@pytest.fixture
def plugin(tmp_data_dir):
    p = CompactionPlugin({
        "threshold": 0.75,
        "window_size": 10000,
        "keep_count": 3,
        "truncation": {
            "l1_threshold": 0.50,
            "l2_threshold": 0.70,
            "preview_chars": 100,
            "window_ratio": 0.15,
        },
        "data_dir": tmp_data_dir,
    })
    p._state_store = AsyncMock()
    p._call_model = AsyncMock()
    return p


@pytest.fixture
def ctx_with_emit():
    """PluginContext that captures emitted events."""
    ctx = PluginContext(session_id="test-session", interaction_round=5, turn=3)
    ctx._pending_events = []
    return ctx


def make_tool_result(tool_call_id: str, tool_name: str, data: str) -> dict:
    return {tool_call_id: {"tool_name": tool_name, "data": data, "success": True, "duration_ms": 100, "turn": 3}}


# --- Safeguard tests ---

def test_safeguard_skips_short_outputs(plugin, ctx_with_emit):
    """Results below dynamic threshold should NOT be truncated."""
    ctx_with_emit.hook_data["_raw_tool_results"] = make_tool_result("t1", "search", "short result")
    plugin._avg_tools_per_round["test-session"] = 4.0

    asyncio.run(plugin._safeguard(ctx_with_emit))

    r = ctx_with_emit.hook_data["_raw_tool_results"]["t1"]
    assert r["data"] == "short result"  # unchanged


def test_safeguard_truncates_oversized_output(plugin, ctx_with_emit, tmp_data_dir):
    """Results above dynamic threshold should be written to disk and truncated in-place."""
    big_data = "x" * 5000
    ctx_with_emit.hook_data["_raw_tool_results"] = make_tool_result("t1", "search", big_data)
    plugin._avg_tools_per_round["test-session"] = 1.0  # threshold = 10000*0.15/1 = 1500, 5000 > 1500

    asyncio.run(plugin._safeguard(ctx_with_emit))

    r = ctx_with_emit.hook_data["_raw_tool_results"]["t1"]
    assert "[Tool output truncated" in r["data"]
    assert "full at" in r["data"]
    assert r["data"].startswith("[Tool output truncated")
    # Check content was written to disk
    content_hash = hashlib.sha1(big_data.encode()).hexdigest()[:8]
    expected_file = Path(tmp_data_dir) / "test-session" / "tool_outputs" / f"turn_3_search_{content_hash}.txt"
    assert expected_file.exists()
    assert expected_file.read_text() == big_data


def test_safeguard_uses_floor_500(plugin, ctx_with_emit):
    """When dynamic threshold < 500, floor of 500 is used."""
    data_600 = "x" * 600
    ctx_with_emit.hook_data["_raw_tool_results"] = make_tool_result("t1", "grep", data_600)
    plugin._avg_tools_per_round["test-session"] = 100.0  # threshold = 10000*0.15/100 = 15, floored to 500
    # 600 > 500, so it should trigger

    asyncio.run(plugin._safeguard(ctx_with_emit))

    r = ctx_with_emit.hook_data["_raw_tool_results"]["t1"]
    assert "[Tool output truncated" in r["data"]


def test_safeguard_write_failure_skips(plugin, ctx_with_emit, tmp_data_dir):
    """If disk write fails, keep original content."""
    big_data = "x" * 5000
    ctx_with_emit.hook_data["_raw_tool_results"] = make_tool_result("t1", "search", big_data)
    plugin._avg_tools_per_round["test-session"] = 1.0

    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        asyncio.run(plugin._safeguard(ctx_with_emit))

    r = ctx_with_emit.hook_data["_raw_tool_results"]["t1"]
    assert r["data"] == big_data  # unchanged
