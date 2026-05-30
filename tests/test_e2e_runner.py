"""Tests for e2e_runner.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "arf_default_assistant"))

from e2e_runner import E2ERunner


def test_load_personas():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    personas = runner.load_personas()
    assert len(personas) == 4
    names = {p["name"] for p in personas}
    assert names == {"coding", "writer", "novelist", "rpg"}
    for p in personas:
        assert "context" in p
        assert "focus_modules" in p
        assert "conversation_style" in p


def test_state_init():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    state = runner.init_state(total_rounds=40)
    assert state["round"] == 0
    assert state["total_rounds"] == 40
    assert state["rounds"] == []
    assert state["issues"] == []


def test_state_advance():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    runner.init_state(total_rounds=40)
    round_data = {
        "persona": "coding",
        "message": "test message",
        "response_content": "test response",
        "sse_types": ["chunk", "done"],
        "error": None,
    }
    runner.record_round(round_data)
    assert runner.state["round"] == 1
    assert len(runner.state["rounds"]) == 1
    assert runner.state["rounds"][0]["persona"] == "coding"


def test_trace_needed_triggers():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    runner.init_state(total_rounds=40)
    for i in range(5):
        runner.record_round({"persona": "coding", "message": "msg", "response_content": "ok", "sse_types": ["done"], "error": None})
    assert runner.trace_check_needed(interval=5) is True


def test_trace_not_needed_early():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    runner.init_state(total_rounds=40)
    for i in range(2):
        runner.record_round({"persona": "coding", "message": "msg", "response_content": "ok", "sse_types": ["done"], "error": None})
    assert runner.trace_check_needed(interval=5) is False


def test_issue_recording():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    runner.init_state(total_rounds=40)
    runner.record_issue(module="compaction", severity="high", description="context_summary not generated", evidence="trace shows missing summary event")
    assert len(runner.state["issues"]) == 1
    assert runner.state["issues"][0]["module"] == "compaction"
    assert runner.state["issues"][0]["severity"] == "high"


def test_parse_sse_events_normal():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    raw = (
        'data: {"type":"chunk","content":"hello"}\n\n'
        'data: {"type":"done","response":"hello","history":[],"session_id":"default"}\n\n'
    )
    events = runner._parse_sse(raw)
    assert len(events) == 2
    assert events[0]["type"] == "chunk"
    assert events[1]["type"] == "done"


def test_parse_sse_with_initial_comment():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    raw = (
        ': ' + ' ' * 2048 + '\n\n'
        'data: {"type":"chunk","content":"hi"}\n\n'
        'data: {"type":"done","response":"hi","history":[],"session_id":"default"}\n\n'
    )
    events = runner._parse_sse(raw)
    assert len(events) == 2
    assert events[0]["type"] == "chunk"


def test_detect_error_events():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    events = [{"type": "chunk"}, {"type": "done"}]
    assert runner._has_error(events) is False

    events_with_error = [{"type": "chunk"}, {"type": "error", "detail": "API error"}]
    assert runner._has_error(events_with_error) is True


def test_detect_empty_response():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    events = [{"type": "done", "response": "hello"}]
    assert runner._is_empty_response(events) is False

    events_empty = [{"type": "done", "response": ""}]
    assert runner._is_empty_response(events_empty) is True

    events_no_done = [{"type": "chunk"}]
    assert runner._is_empty_response(events_no_done) is True


def test_issue_markdown_export():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    runner.init_state()
    runner.record_issue("compaction", "high", "context_summary missing", "round 12 trace")
    runner.record_issue("routing", "low", "fallback not logged", "round 15 trace")
    md = runner.export_issues_markdown()
    assert "## compaction" in md
    assert "## routing" in md
    assert "**High**" in md
    assert "context_summary missing" in md


import tempfile


def test_validate_memory_md_valid():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    content = "## User Identity\n- Name: Test User\n- Role: Developer\n\n## Preferences\n- Prefers Python\n"
    assert runner._validate_memory_md(content) is True


def test_validate_memory_md_empty():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    assert runner._validate_memory_md("") is False
    assert runner._validate_memory_md("NO_NEW_MEMORY") is False


def test_validate_memory_md_no_headings():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    assert runner._validate_memory_md("just some text\n- bullet\n") is False


def test_parse_memory_md_categories():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    content = "## User Identity\n- Name: Test\n- Role: Dev\n\n## Preferences\n- Likes Python\n"
    cats = runner._parse_memory_md_categories(content)
    assert len(cats) == 2
    assert "User Identity" in cats
    assert "Preferences" in cats
    assert len(cats["User Identity"]) == 2
    assert cats["User Identity"][0] == "Name: Test"


def test_validate_memory_entry_valid():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    entry = {"id": "1", "content": "test", "category": "fact", "timestamp": 1.0}
    assert runner._validate_memory_entry(entry) is True


def test_validate_memory_entry_invalid():
    runner = E2ERunner(app_dir=Path(__file__).parent.parent / "app" / "arf_default_assistant")
    assert runner._validate_memory_entry({"id": "1"}) is False
    assert runner._validate_memory_entry({"id": "1", "content": "x", "category": "invalid", "timestamp": 1.0}) is False


import time


def test_mock_pid_file_path():
    from e2e_runner import MOCK_PID_FILE
    assert MOCK_PID_FILE == Path("/tmp/e2e_mock_server.pid")


def test_mock_server_responds_503():
    import subprocess
    import sys
    from urllib.request import urlopen
    from urllib.error import HTTPError

    server_script = """
import http.server
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_error(503, "Service Unavailable")
    def do_POST(self):
        self.send_error(503, "Service Unavailable")
    def log_message(self, format, *args):
        pass
http.server.HTTPServer(('127.0.0.1', 19998), Handler).serve_forever()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", server_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    try:
        try:
            urlopen("http://127.0.0.1:19998/", timeout=2)
        except HTTPError as e:
            assert e.code == 503
    finally:
        proc.kill()
        proc.wait()
