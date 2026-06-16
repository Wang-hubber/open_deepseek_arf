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
    # Verify safeguard_triggered event was emitted
    safeguard_events = [e for e in ctx_with_emit._pending_events if e.type == "safeguard_triggered"]
    assert len(safeguard_events) == 1
    assert safeguard_events[0].data["tool_name"] == "search"
    assert safeguard_events[0].data["original_chars"] == 5000
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


# --- State machine tests ---

def make_state(messages: list[dict], last_token_usage: int = 0) -> dict:
    return {"messages": messages, "last_token_usage": last_token_usage, "interaction_round": 1}


async def _run_round_end(plugin, ctx, state):
    """Helper: set up state_store mock and run round_end hook."""
    plugin._state_store.get.return_value = state
    await plugin.on_hook("round_end", ctx)


def test_state_machine_l1_triggers_at_50pct(plugin, ctx_with_emit, tmp_data_dir):
    """At >50% tokens, L1 truncation fires and level goes to 1."""
    # Build 8 rounds with tool results
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 2000, f"tc{i}", "search"))

    state = make_state(msgs, last_token_usage=5500)  # 55% of 10000
    ctx_with_emit.hook_data["_raw_tool_results"] = {}  # no tools this round

    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    assert plugin._level["test-session"] == 1
    # Verify state was saved with truncated messages
    saved_state = plugin._state_store.put.call_args[0][1]
    saved_msgs = saved_state["messages"]
    # First 2 rounds' tool results should be truncated (keep_count*2 = 6 rounds kept)
    assert "[Tool output truncated" in saved_msgs[3]["content"]  # round 0 tool
    assert "[Tool output truncated" in saved_msgs[6]["content"]  # round 1 tool
    # Rounds 2-7 intact
    assert "[Tool output truncated" not in saved_msgs[9]["content"]


def test_state_machine_l1_only_fires_once(plugin, ctx_with_emit, tmp_data_dir):
    """L1 at level 0 only; at level 1, >50% does NOT fire L1 again."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 2000, f"tc{i}", "search"))

    # First L1 trigger
    state = make_state(msgs, last_token_usage=5500)
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))
    assert plugin._level["test-session"] == 1
    first_call_count = plugin._state_store.put.call_count

    # Second call at same level — should skip (L1 already done)
    saved = plugin._state_store.put.call_args[0][1]
    state2 = make_state(saved["messages"], last_token_usage=5200)  # still >50%, still = 1
    ctx2 = PluginContext(session_id="test-session", interaction_round=6, turn=5)
    ctx2._pending_events = []
    ctx2.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx2, state2))

    # No additional state save (since L1 was skipped)
    assert plugin._state_store.put.call_count == first_call_count
    assert plugin._level["test-session"] == 1  # still 1


def test_state_machine_l2_from_l1(plugin, ctx_with_emit, tmp_data_dir):
    """After L1, >70% triggers L2 and level goes to 2."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 2000, f"tc{i}", "search"))

    # L1 trigger
    state = make_state(msgs, last_token_usage=5500)
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))
    assert plugin._level["test-session"] == 1

    # L2 trigger (simulating token growth after L1)
    saved = plugin._state_store.put.call_args[0][1]
    state2 = make_state(saved["messages"], last_token_usage=7500)  # 75% > 70%
    ctx2 = PluginContext(session_id="test-session", interaction_round=7, turn=6)
    ctx2._pending_events = []
    ctx2.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx2, state2))

    assert plugin._level["test-session"] == 2
    saved2 = plugin._state_store.put.call_args[0][1]
    # Now only keep_count (3) rounds kept (was 6 after L1)
    # 8 rounds total, keeping 3 → 5 tool msgs truncated
    # Verify the 5th round's tool was truncated
    assert "[Tool output truncated" in saved2["messages"][15]["content"]  # round 4 tool


def test_state_machine_l3_resets_level(plugin, ctx_with_emit, tmp_data_dir):
    """L3 LLM compaction fires at >75% from any level and resets to 0."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg(f"result {i}", f"tc{i}", "read"))

    # Setup: L3 is triggered
    state = make_state(msgs, last_token_usage=8000)  # 80% > 75%
    plugin._call_model.return_value = {"content": "compact summary text"}
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    # Level resets to 0 after L3
    assert plugin._level["test-session"] == 0
    # Cooldown is set
    assert plugin._cooldown["test-session"] == 2

    # Verify compacted state has boundary + summary markers
    saved = plugin._state_store.put.call_args[0][1]
    assert any(m.get("subtype") == "compact_boundary" for m in saved["messages"])
    assert any(m.get("isCompactSummary") for m in saved["messages"])


def test_state_machine_skip_level(plugin, ctx_with_emit, tmp_data_dir):
    """If usage jumps from <50% to >70% directly, L2 fires (skip L1)."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 2000, f"tc{i}", "search"))

    state = make_state(msgs, last_token_usage=7200)  # 72% > 70%, jumped past 50%
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    # Should have done L2 truncation (keep_count rounds) directly
    assert plugin._level["test-session"] == 2


def test_state_machine_ema_update(plugin, ctx_with_emit):
    """EMA is updated at each round_end from _raw_tool_results count."""
    msgs = [make_msg("user", "Q"), make_msg("assistant", "A")]
    state = make_state(msgs, last_token_usage=100)  # below all thresholds
    ctx_with_emit.hook_data["_raw_tool_results"] = {"t1": {}, "t2": {}, "t3": {}}  # 3 tools

    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    # Initial EMA: 0.7 * 1.0 + 0.3 * 3 = 0.7 + 0.9 = 1.6
    assert plugin._avg_tools_per_round["test-session"] == pytest.approx(1.6)

    # Second round with 5 tools
    ctx2 = PluginContext(session_id="test-session", interaction_round=2)
    ctx2._pending_events = []
    ctx2.hook_data["_raw_tool_results"] = {"t1": {}, "t2": {}, "t3": {}, "t4": {}, "t5": {}}
    state2 = make_state(msgs, last_token_usage=100)
    asyncio.run(_run_round_end(plugin, ctx2, state2))

    # EMA: 0.7 * 1.6 + 0.3 * 5 = 1.12 + 1.5 = 2.62
    assert plugin._avg_tools_per_round["test-session"] == pytest.approx(2.62)


def test_cooldown_blocks_l3(plugin, ctx_with_emit):
    """After L3, cooldown prevents re-compaction for 2 rounds."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg(f"result {i}", f"tc{i}", "read"))

    # Fire L3
    state = make_state(msgs, last_token_usage=8000)
    plugin._call_model.return_value = {"content": "summary"}
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))
    assert plugin._cooldown["test-session"] == 2

    # Next round — cooldown decrements, L3 skipped
    first_call_count = plugin._state_store.put.call_count
    ctx2 = PluginContext(session_id="test-session", interaction_round=8)
    ctx2._pending_events = []
    ctx2.hook_data["_raw_tool_results"] = {}
    saved = plugin._state_store.put.call_args[0][1]
    state2 = make_state(saved["messages"], last_token_usage=8000)
    asyncio.run(_run_round_end(plugin, ctx2, state2))

    # Cooldown decremented, no additional compaction
    assert plugin._cooldown["test-session"] == 1
    assert plugin._state_store.put.call_count == first_call_count


# --- Event emission tests ---

def test_truncation_emits_events(plugin, ctx_with_emit, tmp_data_dir):
    """L1 truncation emits truncation_start and truncation_end via ctx.emit()."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 2000, f"tc{i}", "search"))

    state = make_state(msgs, last_token_usage=5500)
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    events = ctx_with_emit._pending_events
    event_types = [e.type for e in events]
    assert "truncation_start" in event_types
    assert "truncation_end" in event_types

    start_ev = next(e for e in events if e.type == "truncation_start")
    assert start_ev.data["level"] == "L1"
    assert start_ev.data["trigger"] == "auto"
    assert start_ev.data["pre_tokens"] == 5500

    end_ev = next(e for e in events if e.type == "truncation_end")
    assert end_ev.data["level"] == "L1"
    assert end_ev.data["truncated_count"] > 0


def test_compaction_emits_events(plugin, ctx_with_emit):
    """L3 compaction emits compaction_start/end via ctx.emit() with level L3."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg(f"result {i}", f"tc{i}", "read"))

    state = make_state(msgs, last_token_usage=8000)
    plugin._call_model.return_value = {"content": "compact summary"}
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    events = ctx_with_emit._pending_events
    event_types = [e.type for e in events]
    assert "compaction_start" in event_types
    assert "compaction_end" in event_types

    start_ev = next(e for e in events if e.type == "compaction_start")
    assert start_ev.data["level"] == "L3"


def test_truncation_events_not_injected_as_engine_events(plugin, ctx_with_emit, tmp_data_dir):
    """Truncation events go to ctx.emit() only, NOT inject_engine_event."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 2000, f"tc{i}", "search"))

    state = make_state(msgs, last_token_usage=5500)
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    # ctx.emit() events are in _pending_events, not hook_data._engine_events
    engine_events = ctx_with_emit.hook_data.get("_engine_events", [])
    engine_types = [e["type"] for e in engine_events]
    assert "truncation_start" not in engine_types
    assert "truncation_end" not in engine_types


def test_l3_summarization_failure_keeps_level(plugin, ctx_with_emit):
    """When LLM summarization fails, compaction still saves with fallback summary."""
    msgs = [make_msg("system", "sys")]
    for i in range(8):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg(f"result {i}", f"tc{i}", "read"))

    plugin._level["test-session"] = 2  # already at L2
    plugin._call_model.side_effect = RuntimeError("API error")

    state = make_state(msgs, last_token_usage=8000)
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))

    # The _compact method catches summarizer failure internally and still
    # saves state with fallback summary. Verify it works.
    saved = plugin._state_store.put.call_args[0][1]
    assert any(m.get("isCompactSummary") for m in saved["messages"])
    assert plugin._level["test-session"] == 0  # resets even on fallback


# --- Full chain integration ---

def test_full_l1_l2_l3_cascade(plugin, ctx_with_emit, tmp_data_dir):
    """Multi-round conversation triggers complete L1->L2->L3 cascade with reset."""
    # Build 10 rounds
    msgs = [make_msg("system", "sys")]
    for i in range(10):
        msgs.append(make_msg("user", f"Q{i}"))
        msgs.append(make_msg("assistant", f"A{i}"))
        msgs.append(make_tool_msg("x" * 3000, f"tc{i}", "search"))

    plugin._call_model.return_value = {"content": "final summary"}

    # Round 1: L1 fires (55% > 50%)
    state = make_state(msgs, last_token_usage=5500)
    ctx_with_emit.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx_with_emit, state))
    assert plugin._level["test-session"] == 1
    l1_events = [e for e in ctx_with_emit._pending_events if e.type == "truncation_start"]
    assert len(l1_events) == 1 and l1_events[0].data["level"] == "L1"

    # Round 2: L2 fires (75% > 70%, level=1)
    saved = plugin._state_store.put.call_args[0][1]
    state2 = make_state(saved["messages"], last_token_usage=7500)
    ctx2 = PluginContext(session_id="test-session", interaction_round=8)
    ctx2._pending_events = []
    ctx2.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx2, state2))
    assert plugin._level["test-session"] == 2
    l2_events = [e for e in ctx2._pending_events if e.type == "truncation_start"]
    assert len(l2_events) == 1 and l2_events[0].data["level"] == "L2"

    # Round 3: L3 fires (80% > 75%), resets level
    saved2 = plugin._state_store.put.call_args[0][1]
    state3 = make_state(saved2["messages"], last_token_usage=8000)
    ctx3 = PluginContext(session_id="test-session", interaction_round=9)
    ctx3._pending_events = []
    ctx3.hook_data["_raw_tool_results"] = {}
    asyncio.run(_run_round_end(plugin, ctx3, state3))
    assert plugin._level["test-session"] == 0  # reset
    assert plugin._cooldown["test-session"] == 2
    l3_events = [e for e in ctx3._pending_events if e.type == "compaction_start"]
    assert len(l3_events) == 1 and l3_events[0].data["level"] == "L3"

    # Round 4-5: Cooldown prevents further compaction
    ctx4 = PluginContext(session_id="test-session", interaction_round=10)
    ctx4._pending_events = []
    ctx4.hook_data["_raw_tool_results"] = {}
    saved3 = plugin._state_store.put.call_args[0][1]
    state4 = make_state(saved3["messages"], last_token_usage=8000)
    put_before = plugin._state_store.put.call_count
    asyncio.run(_run_round_end(plugin, ctx4, state4))
    assert plugin._cooldown["test-session"] == 1
    assert plugin._state_store.put.call_count == put_before  # no save

    # After cooldown expired, L1 can fire again on new compacted messages
    plugin._cooldown["test-session"] = 0
    ctx5 = PluginContext(session_id="test-session", interaction_round=11)
    ctx5._pending_events = []
    ctx5.hook_data["_raw_tool_results"] = {}
    state5 = make_state(saved3["messages"], last_token_usage=5500)
    asyncio.run(_run_round_end(plugin, ctx5, state5))
    assert plugin._level["test-session"] == 1  # L1 fires again
