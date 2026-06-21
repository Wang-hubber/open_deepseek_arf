"""Tests for EvalPlugin annotation API."""
import json
import tempfile
from pathlib import Path

import pytest
from arf.plugins.eval.plugin import EvalPlugin


class TestEvalPluginAnnotate:
    @pytest.fixture
    def data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_annotate_writes_user_annotation_to_trace(self, data_dir):
        # Setup: create session trace directory with existing events
        sid = "test_session"
        trace_dir = data_dir / sid / "traces"
        trace_dir.mkdir(parents=True)
        trace_file = trace_dir / f"{sid}.jsonl"
        # Pre-populate with a session_start event
        trace_file.write_text(json.dumps({
            "type": "session_start", "session_id": sid,
            "turn": 0, "timestamp": 1.0,
        }) + "\n", encoding="utf-8")

        plugin = EvalPlugin(config={
            "data_dir": str(data_dir),
            "annotation_enabled": True,
        })

        plugin.annotate(
            session_id=sid,
            round=2,
            rating="like",
            comment="回答准确且完整",
        )

        # Verify the annotation was written to the trace file
        lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # original + annotation
        annotation = json.loads(lines[1])
        assert annotation["type"] == "user_annotation"
        assert annotation["session_id"] == sid
        assert annotation["round"] == 2
        assert annotation["data"]["rating"] == "like"
        assert annotation["data"]["comment"] == "回答准确且完整"
        assert "annotated_at" in annotation["data"]

    def test_annotate_respects_annotation_enabled(self, data_dir):
        """annotate() writes only when annotation_enabled is True."""
        sid = "test_session"
        trace_dir = data_dir / sid / "traces"
        trace_dir.mkdir(parents=True)
        trace_file = trace_dir / f"{sid}.jsonl"
        trace_file.write_text("", encoding="utf-8")

        # When disabled (default) — annotate() should NOT write
        plugin_disabled = EvalPlugin(config={"data_dir": str(data_dir)})
        plugin_disabled.annotate(
            session_id=sid, round=0, rating="dislike", comment="bad",
        )
        lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1 and lines[0] == ""  # 1 empty string = nothing written

        # When enabled — annotate() should write
        plugin_enabled = EvalPlugin(config={
            "data_dir": str(data_dir),
            "annotation_enabled": True,
        })
        plugin_enabled.annotate(
            session_id=sid, round=1, rating="like", comment="good",
        )
        lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        annotation = json.loads(lines[0])
        assert annotation["type"] == "user_annotation"
        assert annotation["data"]["rating"] == "like"
