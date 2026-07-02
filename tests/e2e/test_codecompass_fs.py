"""E2E tests for codecompass-fs example (Phase 8).

Covers all MVP capabilities. Uses mock model (no API key).
Run:
    cd /home/wangxie/open_deepseek_arf
    PYTHONPATH=py-arf/python:examples/python/codecompass_fs pytest tests/e2e/test_codecompass_fs.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# Ensure both codecompass_fs and py-arf binding are on path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "py-arf" / "python"))
sys.path.insert(0, str(_REPO_ROOT / "examples" / "python" / "codecompass_fs"))

from app import CodecompassApp  # noqa: E402
from subagent_launcher import SubagentLauncher, SubagentSpec, SubagentOutput  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory(prefix="codecompass_e2e_") as d:
        yield Path(d)


@pytest.fixture
async def app(workdir):
    a = CodecompassApp(workdir=workdir / "run", mode="mock")
    await a.start()
    yield a
    await a.shutdown()


# ── Multi-session archive + switch ────────────────────────────────────

@pytest.mark.asyncio
async def test_session_list_empty(app):
    sessions = await app.list_sessions()
    assert sessions == []


@pytest.mark.asyncio
async def test_session_create_and_list(app):
    await app.start_session("sess-1", "first task")
    await app.start_session("sess-2", "second task")
    sessions = await app.list_sessions()
    assert len(sessions) == 2
    ids = {s["session_id"] for s in sessions}
    assert ids == {"sess-1", "sess-2"}


@pytest.mark.asyncio
async def test_session_switch_isolates_state(app):
    """Switching between sessions must not leak messages."""
    await app.start_session("s1", "task A")
    await app.start_session("s2", "task B")
    await app.chat("s1", "hello A")
    await app.chat("s2", "hello B")
    s1 = await app.session_store.get("s1")
    s2 = await app.session_store.get("s2")
    # Each session only contains its own user message + assistant reply
    s1_user = [m for m in s1["state"]["messages"] if m.get("role") == "user"]
    s2_user = [m for m in s2["state"]["messages"] if m.get("role") == "user"]
    assert len(s1_user) == 1
    assert len(s2_user) == 1
    assert s1_user[0]["content"] == "hello A"
    assert s2_user[0]["content"] == "hello B"


@pytest.mark.asyncio
async def test_session_persists_across_app_restart(workdir):
    """Session data should survive an app restart (file-based)."""
    db_path = workdir / "sessions.db"
    a1 = CodecompassApp(workdir=workdir / "run1", session_db_path=db_path)
    await a1.start()
    await a1.start_session("s1", "persistent task")
    await a1.chat("s1", "remember me")
    await a1.shutdown()

    a2 = CodecompassApp(workdir=workdir / "run2", session_db_path=db_path)
    await a2.start()
    sessions = await a2.list_sessions()
    assert any(s["session_id"] == "s1" for s in sessions)
    s1 = await a2.session_store.get("s1")
    assert s1["title"] == "persistent task"
    assert any(m.get("content") == "remember me" for m in s1["state"]["messages"])
    await a2.shutdown()


# ── Multi-round conversation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_round_chat_increments_state(app):
    await app.start_session("s1", "chat test")
    for i in range(3):
        out = await app.chat("s1", f"round {i} message")
        assert out.startswith("Ack:") or out.startswith("[")
    s = await app.session_store.get("s1")
    assert s["state"]["over_view"]["round_count"] == 3
    assert s["state"]["over_view"]["turn_count"] >= 3
    # 3 user + 3 assistant = 6 messages
    assert len(s["state"]["messages"]) == 6


# ── Interrupt + recover ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_records_interrupted_state(app):
    await app.start_session("s1", "interrupt test")
    await app.chat("s1", "first message")
    s = await app.session_store.get("s1")
    await app.session_store.snapshot(
        "s1", s["state"], checkpoint="AfterModelCall", turn_index=1
    )
    s2 = await app.session_store.get("s1")
    assert s2["status"] == "interrupted"
    assert "last_checkpoint" in s2
    assert s2["last_checkpoint"]["checkpoint"] == "AfterModelCall"
    assert s2["last_checkpoint"]["turn_index"] == 1


@pytest.mark.asyncio
async def test_recovery_preserves_messages(app):
    await app.start_session("s1", "recover test")
    await app.chat("s1", "msg before crash")
    s = await app.session_store.get("s1")
    await app.session_store.snapshot("s1", s["state"], checkpoint="RoundEnd", turn_index=2)
    # Restart: re-load session, see snapshot, resume
    s_recovered = await app.session_store.get("s1")
    assert s_recovered["status"] == "interrupted"
    assert any(m.get("content") == "msg before crash"
               for m in s_recovered["state"]["messages"])


# ── Multi-MCP nodes ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_nodes_present(app):
    """All 4 MCP namespaces should be registered on the bus."""
    assert "fs" in app.mcp_nodes
    assert "code" in app.mcp_nodes
    assert "git" in app.mcp_nodes
    assert "web" in app.mcp_nodes
    for ns, node in app.mcp_nodes.items():
        assert node.node_id is not None
        assert str(node.node_id) == f"mcp/{ns}"


# ── Compact ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compact_when_messages_exceed_threshold(app):
    await app.start_session("s1", "compact test")
    for i in range(10):
        await app.chat("s1", f"message {i}")
    s = await app.session_store.get("s1")
    before = len(s["state"]["messages"])
    result = await app.compactor.compact("s1", keep_tail=3)
    assert result["status"] == "compacted"
    assert result["messages_before"] == before
    assert result["messages_after"] < before
    s2 = await app.session_store.get("s1")
    # The new state has 1 summary + 3 kept = 4 messages
    assert len(s2["state"]["messages"]) == 4
    assert s2["state"]["messages"][0]["role"] == "system"
    assert "COMPACTED" in s2["state"]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_compact_skipped_when_too_few_messages(app):
    await app.start_session("s1", "skip compact test")
    await app.chat("s1", "only one")
    result = await app.compactor.compact("s1", keep_tail=5)
    assert result["status"] == "skipped"


# ── Subagent delegation (F7) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_subagent_delegation_returns_output(app):
    out = await app.delegate_to_subagent("s1", "summarize codebase")
    assert "subagent" in out.lower() or "summary" in out.lower()
    assert len(out) > 0


@pytest.mark.asyncio
async def test_subagent_launcher_basic(app):
    launcher = SubagentLauncher(app.bus, app.main_engine)
    spec = SubagentSpec(name="helper", task="do a thing", parent_session_id="parent-1")
    out = await launcher.delegate(spec)
    assert isinstance(out, SubagentOutput)
    assert out.subagent_id.startswith("subagent-")
    assert out.status in {"success", "failed"}


@pytest.mark.asyncio
async def test_subagent_depth_limit(app):
    launcher = SubagentLauncher(app.bus, app.main_engine)
    # Pre-set depth at max
    launcher._depth["root"] = 2
    spec = SubagentSpec(
        name="nested", task="nested", parent_session_id="root", max_depth=2
    )
    out = await launcher.delegate(spec)
    assert out.status == "failed"
    assert "max_depth" in out.output


# ── Peer agent messages ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_peer_message_acknowledged(app):
    out = await app.send_peer_message("main", "peer-a", "hello peer")
    assert "peer" in out.lower()
    assert "ack" in out.lower() or "hello" in out.lower()


@pytest.mark.asyncio
async def test_peer_engines_registered(app):
    assert "peer-a" in app.peer_engines
    assert "peer-b" in app.peer_engines


# ── Memory operations (F1 + arf-core) ────────────────────────────────

def test_memory_op_constructs():
    """Sanity check: F1 ActionMessage types are tested in Rust workspace.
    See crates/arf-core/src/lib.rs for the Rust-side coverage."""
    from arf import ActionMessage
    assert ActionMessage is not None


def test_subagent_delegate_type():
    """F1 new types are covered by Rust unit tests in crates/arf-core."""
    from arf import ActionMessage
    assert ActionMessage is not None


def test_peer_message_type():
    """F1 new types are covered by Rust unit tests in crates/arf-core."""
    from arf import ActionMessage
    assert ActionMessage is not None


# ── App lifecycle ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_shutdown_clean(app):
    # Just verify the fixture teardown didn't raise
    assert app.bus is not None
    assert app.model is not None
    assert app.main_engine is not None


@pytest.mark.asyncio
async def test_session_delete(app):
    await app.start_session("s1", "to delete")
    assert len(await app.list_sessions()) == 1
    await app.delete_session("s1")
    assert len(await app.list_sessions()) == 0


# ── Skill presence (smoke) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_skills_directory_populated(app):
    skills = app.workdir / "skills"
    assert skills.exists()
    assert (skills / "refactor" / "SKILL.md").exists()
    assert (skills / "debug" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_tools_directory_populated(app):
    tools = app.workdir / "tools"
    assert tools.exists()
    assert (tools / "read_file" / "tool.toml").exists()
    assert (tools / "read_file" / "main.sh").exists()
    assert (tools / "grep" / "tool.toml").exists()
