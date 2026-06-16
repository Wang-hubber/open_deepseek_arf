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


# --- Message helpers ---

def make_msg(role: str, content: str, **kwargs) -> dict:
    m = {"role": role, "content": content}
    m.update(kwargs)
    return m


def make_tool_msg(content: str, tool_call_id: str = "tc1", tool_name: str = "read") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": content}


# --- Round boundary tests ---

def test_find_round_boundaries():
    """User messages (non-isCompactSummary) mark round starts."""
    msgs = [
        make_msg("system", "You are a helpful assistant"),
        make_msg("user", "First question"),
        make_msg("assistant", "First answer"),
        make_msg("user", "Second question"),
        make_msg("assistant", "Second answer"),
        make_msg("user", "Third question"),
        make_msg("assistant", "Third answer"),
    ]
    from arf.plugins.compaction.plugin import _find_round_boundaries
    boundaries = _find_round_boundaries(msgs)
    assert boundaries == [1, 3, 5]  # indices of user messages


def test_find_round_boundaries_skips_compact_summary():
    """isCompactSummary user messages are NOT round boundaries."""
    msgs = [
        make_msg("user", "summary", isCompactSummary=True),
        make_msg("user", "Real question"),
    ]
    from arf.plugins.compaction.plugin import _find_round_boundaries
    boundaries = _find_round_boundaries(msgs)
    assert boundaries == [1]  # only the real question


def test_find_round_boundaries_empty():
    from arf.plugins.compaction.plugin import _find_round_boundaries
    assert _find_round_boundaries([]) == []


# --- Truncation tests ---

def test_truncate_tool_messages_l1(plugin, ctx_with_emit, tmp_data_dir):
    """L1 truncation keeps keep_count*2 rounds (6 rounds), truncates tool msgs before."""
    keep_count = 3
    # Build 8 rounds (user+assistant+tool per round), keep 6
    msgs = [make_msg("system", "system prompt")]
    for i in range(8):
        msgs.append(make_msg("user", f"Question {i}"))
        msgs.append(make_msg("assistant", f"Answer {i}"))
        msgs.append(make_tool_msg(f"Tool result for round {i}: " + "x" * 2000, f"tc{i}", "search"))
    # Rounds at indices: 1, 4, 7, 10, 13, 16, 19, 22

    from arf.plugins.compaction.plugin import _truncate_tool_messages
    new_msgs, truncated_count = _truncate_tool_messages(
        msgs, keep_rounds=keep_count * 2,  # 6
        data_dir=tmp_data_dir,
        session_id="test-session",
        preview_chars=100,
    )

    # First 2 rounds (indices 1-6) should have tool results truncated
    # Rounds 3-8 (indices 7+) should be intact
    # Round 1 tool at index 3
    assert "[Tool output truncated" in new_msgs[3]["content"]
    # Round 6 tool at index 18
    assert "[Tool output truncated" not in new_msgs[18]["content"]
    # Last round tool intact
    assert new_msgs[22]["content"] == msgs[22]["content"]
    assert truncated_count == 2  # 2 tool messages truncated


def test_truncate_tool_messages_l2(plugin, ctx_with_emit, tmp_data_dir):
    """L2 truncation tightens to keep_count rounds (3 rounds)."""
    keep_count = 3
    msgs = [make_msg("system", "system prompt")]
    for i in range(8):
        msgs.append(make_msg("user", f"Question {i}"))
        msgs.append(make_msg("assistant", f"Answer {i}"))
        msgs.append(make_tool_msg(f"Tool result {i}: " + "x" * 2000, f"tc{i}", "search"))

    from arf.plugins.compaction.plugin import _truncate_tool_messages
    new_msgs, truncated_count = _truncate_tool_messages(
        msgs, keep_rounds=keep_count,
        data_dir=tmp_data_dir,
        session_id="test-session",
        preview_chars=100,
    )

    # First 5 rounds truncated, last 3 intact
    assert truncated_count == 5


def test_truncate_writes_to_disk(plugin, ctx_with_emit, tmp_data_dir):
    """Truncated content is written to tool_outputs/ directory."""
    big_content = "IMPORTANT_DATA_" + "x" * 2000
    msgs = [
        make_msg("user", "Q1"),
        make_msg("assistant", "A1"),
        make_tool_msg(big_content, "tc1", "grep"),
        make_msg("user", "Q2"),
        make_msg("assistant", "A2"),
        make_tool_msg("small", "tc2", "read"),
    ]

    from arf.plugins.compaction.plugin import _truncate_tool_messages
    new_msgs, _ = _truncate_tool_messages(
        msgs, keep_rounds=1,
        data_dir=tmp_data_dir,
        session_id="test-session",
        preview_chars=100,
    )

    # Verify file written
    content_hash = hashlib.sha1(big_content.encode()).hexdigest()[:8]
    expected_file = Path(tmp_data_dir) / "test-session" / "tool_outputs" / f"round_0_grep_{content_hash}.txt"
    assert expected_file.exists()
    assert expected_file.read_text() == big_content

    # Truncated message has preview (100 chars) + path reference
    truncated = new_msgs[2]
    assert "[Tool output truncated" in truncated["content"]
    assert "IMPORTANT_DATA_" in truncated["content"]
    assert "..." in truncated["content"]


def test_truncate_nothing_when_under_keep_count(plugin, tmp_data_dir):
    """No truncation when rounds <= keep_rounds."""
    msgs = [
        make_msg("user", "Q1"),
        make_msg("assistant", "A1"),
        make_tool_msg("result", "tc1", "read"),
    ]

    from arf.plugins.compaction.plugin import _truncate_tool_messages
    new_msgs, truncated_count = _truncate_tool_messages(
        msgs, keep_rounds=5,
        data_dir=tmp_data_dir,
        session_id="test-session",
        preview_chars=100,
    )

    assert truncated_count == 0
    assert new_msgs == msgs


def test_truncate_skips_non_tool_messages(plugin, tmp_data_dir):
    """Only tool-role messages get truncated."""
    msgs = [
        make_msg("user", "Q1"),
        make_msg("assistant", "A1 long answer " + "y" * 2000),
        make_tool_msg("tool output " + "x" * 2000, "tc1", "read"),
        make_msg("user", "Q2"),
        make_msg("assistant", "A2"),
        make_tool_msg("small", "tc2", "read"),
    ]

    from arf.plugins.compaction.plugin import _truncate_tool_messages
    new_msgs, _ = _truncate_tool_messages(
        msgs, keep_rounds=1,
        data_dir=tmp_data_dir,
        session_id="test-session",
        preview_chars=100,
    )

    # Assistant message content is NOT truncated (not a tool message)
    assert new_msgs[1]["content"] == msgs[1]["content"]
    # Tool message IS truncated
    assert "[Tool output truncated" in new_msgs[2]["content"]
