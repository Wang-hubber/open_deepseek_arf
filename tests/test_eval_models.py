"""Unit tests for eval data models and JSON serialization."""
import pytest

from arf.evaluation.models import EvalCase, EvalBenchmark


class TestEvalCase:
    def test_minimal(self):
        c = EvalCase(id="c1", input="hello")
        assert c.expected_tools is None
        assert c.expected_output_contains is None

    def test_full(self):
        c = EvalCase(id="c1", input="hello",
                     expected_tools=["file_writer"],
                     expected_output_contains=["hello.py"],
                     max_turns=3)
        assert c.max_turns == 3


class TestEvalBenchmarkJson:
    @pytest.fixture
    def benchmark(self):
        return EvalBenchmark(
            name="file_ops_v1",
            source_session="default",
            created_at=1716812345.0,
            cases=[
                EvalCase(id="c0", input="create hello.py",
                         expected_tools=["file_writer"],
                         expected_output_contains=["hello.py"]),
                EvalCase(id="c1", input="read it back"),
            ],
        )

    def test_to_json_roundtrip(self, benchmark, tmp_path):
        p = tmp_path / "bm.json"
        benchmark.to_json(str(p))
        loaded = EvalBenchmark.from_json(str(p))
        assert loaded.name == "file_ops_v1"
        assert loaded.source_session == "default"
        assert len(loaded.cases) == 2
        assert loaded.cases[0].input == "create hello.py"
        assert loaded.cases[0].expected_tools == ["file_writer"]

    def test_from_json_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            EvalBenchmark.from_json(str(tmp_path / "nope.json"))

    def test_defaults(self):
        bm = EvalBenchmark(name="test")
        assert bm.cases == []
        assert bm.source_session is None
