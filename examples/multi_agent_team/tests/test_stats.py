"""Task 18c — TokenStats aggregator unit tests.

Pure function tests; no API server, no LLM calls.
"""

import json
from pathlib import Path

import pytest

from stats import (
    EngineStats,
    aggregate_engine,
    aggregate_team,
    iter_events,
    team_rollup,
)


def _write(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")


def _round_start(round_n: int, at: str = "2026-07-06T00:00:00Z") -> dict:
    return {"kind": "event", "event_type": "round_start", "round": round_n, "at": at}


def _round_end(round_n: int, duration_ms: int, at: str = "2026-07-06T00:00:05Z") -> dict:
    return {
        "kind": "event", "event_type": "round_end",
        "round": round_n, "duration_ms": duration_ms, "at": at,
    }


def _model_call(model: str, in_t: int, out_t: int, turn: int = 1, round: int = 1) -> dict:
    return {
        "kind": "event", "event_type": "model_call_end",
        "model": model, "input_tokens": in_t, "output_tokens": out_t,
        "total_tokens": in_t + out_t, "turn": turn, "round": round,
        "at": "2026-07-06T00:00:01Z",
    }


def _tool_call(name: str, success: bool, duration_ms: int, error: str | None = None) -> dict:
    return {
        "kind": "event", "event_type": "tool_call_end",
        "tool_name": name, "success": success, "duration_ms": duration_ms,
        "error": error, "turn": 1, "round": 1,
        "at": "2026-07-06T00:00:02Z",
    }


def _peer_sent(target_node: str = "B") -> dict:
    return {
        "kind": "event", "event_type": "peer_message_sent",
        "correlation_id": "c1", "target_node": target_node, "attempt": 1,
        "at": "2026-07-06T00:00:00Z",
    }


def _peer_received() -> dict:
    return {"kind": "event", "event_type": "peer_reply_received", "correlation_id": "c1"}


# [构造] iter_events 跳过 snapshot/save 和损坏行
def test_iter_events_filters_kind(tmp_path: Path):
    p = tmp_path / "events.x.jsonl"
    _write(p, [
        {"kind": "snapshot", "checkpoint": "BeforeModelCall"},
        {"kind": "save", "data": {}},
        {"kind": "event", "event_type": "round_start", "round": 1},
        "this is not json",
        {"kind": "event", "event_type": "round_end", "round": 1, "duration_ms": 100},
    ])
    events = list(iter_events(p))
    assert len(events) == 2
    assert [e["event_type"] for e in events] == ["round_start", "round_end"]


# [构造] 不存在文件 → 空
def test_iter_events_missing_file_empty(tmp_path: Path):
    assert list(iter_events(tmp_path / "nope.jsonl")) == []


# [方法] round 配对 (round_start + round_end → by_round entry with duration)
def test_aggregate_round_pairing(tmp_path: Path):
    p = tmp_path / "events.A.jsonl"
    _write(p, [_round_start(1), _round_end(1, 5000)])
    s = aggregate_engine(p, "A")
    assert s.rounds.total_rounds == 1
    assert s.rounds.total_duration_ms == 5000
    assert s.rounds.by_round[0]["duration_ms"] == 5000
    assert s.rounds.by_round[0]["started_at"] is not None
    assert s.rounds.by_round[0]["ended_at"] is not None


# [方法] 多 round 各自分开配对
def test_aggregate_multiple_rounds(tmp_path: Path):
    p = tmp_path / "events.A.jsonl"
    _write(p, [
        _round_start(1), _round_end(1, 100),
        _round_start(2), _round_end(2, 200),
        _round_start(3, at="2026-07-06T00:01:00Z"),
    ])
    s = aggregate_engine(p, "A")
    assert s.rounds.total_rounds == 3
    assert s.rounds.total_duration_ms == 300  # round 1 + 2 都完成
    # round 3 只有 start 没 end → duration 未填
    rd3 = [r for r in s.rounds.by_round if r["round"] == 3][0]
    assert "ended_at" not in rd3


# [方法] model_call_end 累加 token + by_model 分桶
def test_aggregate_model_call_tokens(tmp_path: Path):
    p = tmp_path / "events.A.jsonl"
    _write(p, [
        _model_call("deepseek-chat", 100, 50),
        _model_call("deepseek-chat", 200, 100),
        _model_call("qwen3-max", 150, 75),
    ])
    s = aggregate_engine(p, "A")
    assert s.model_calls.total_calls == 3
    assert s.model_calls.total_input_tokens == 450
    assert s.model_calls.total_output_tokens == 225
    assert s.model_calls.total_tokens == 675
    assert s.model_calls.by_model == {
        "deepseek-chat": 450,  # 150 + 300
        "qwen3-max": 225,
    }


# [方法] tool_call_end success + failure 分桶
def test_aggregate_tool_call_success_failure(tmp_path: Path):
    p = tmp_path / "events.A.jsonl"
    _write(p, [
        _tool_call("write_file", True, 100),
        _tool_call("write_file", True, 50),
        _tool_call("read_file", False, 5, error="permission denied"),
    ])
    s = aggregate_engine(p, "A")
    assert s.tool_calls.total_calls == 3
    assert s.tool_calls.success_count == 2
    assert s.tool_calls.failure_count == 1
    assert s.tool_calls.total_duration_ms == 155
    assert s.tool_calls.by_tool["write_file"]["calls"] == 2
    assert s.tool_calls.by_tool["write_file"]["success"] == 2
    assert s.tool_calls.by_tool["read_file"]["failure"] == 1


# [方法] peer_message_sent/received 计数
def test_aggregate_peer_message_count(tmp_path: Path):
    p = tmp_path / "events.A.jsonl"
    _write(p, [
        _peer_sent(), _peer_sent(), _peer_sent(),
        _peer_received(), _peer_received(),
    ])
    s = aggregate_engine(p, "A")
    assert s.peer_messages_sent == 3
    assert s.peer_messages_received == 2


# [方法] team rollup 累加 + 失败率/平均时长
def test_team_rollup_aggregation():
    a = EngineStats(engine_id="A")
    a.model_calls.total_calls = 2
    a.model_calls.total_input_tokens = 100
    a.model_calls.total_output_tokens = 50
    a.model_calls.total_tokens = 150
    a.model_calls.by_model = {"deepseek-chat": 150}
    a.tool_calls.total_calls = 3
    a.tool_calls.success_count = 2
    a.tool_calls.failure_count = 1
    a.tool_calls.total_duration_ms = 150
    a.tool_calls.by_tool = {
        "write_file": {"calls": 3, "success": 2, "failure": 1, "total_duration_ms": 150},
    }
    a.peer_messages_sent = 5
    a.peer_messages_received = 3

    b = EngineStats(engine_id="B")
    b.model_calls.total_calls = 1
    b.model_calls.total_input_tokens = 50
    b.model_calls.total_output_tokens = 25
    b.model_calls.total_tokens = 75
    b.model_calls.by_model = {"qwen3-max": 75}
    b.tool_calls.total_calls = 1
    b.tool_calls.success_count = 1
    b.tool_calls.total_duration_ms = 30
    b.tool_calls.by_tool = {
        "read_file": {"calls": 1, "success": 1, "failure": 0, "total_duration_ms": 30},
    }
    b.peer_messages_sent = 2
    b.peer_messages_received = 4

    out = team_rollup({"A": a, "B": b})
    assert out["team_engines"] == 2
    assert out["total_model_calls"] == 3
    assert out["total_input_tokens"] == 150
    assert out["total_tokens"] == 225
    assert out["by_model"] == {"deepseek-chat": 150, "qwen3-max": 75}
    assert out["total_tool_calls"] == 4
    assert out["total_tool_success"] == 3
    assert out["total_tool_failure"] == 1
    assert out["tool_failure_rate"] == pytest.approx(0.25)
    assert out["avg_tool_duration_ms"] == pytest.approx(45.0)  # 180/4
    assert out["total_peer_messages_sent"] == 7
    assert out["total_peer_messages_received"] == 7


# [边界] 空 rollup
def test_team_rollup_empty():
    out = team_rollup({})
    assert out["team_engines"] == 0
    assert out["total_model_calls"] == 0
    assert out["tool_failure_rate"] == 0.0
    assert out["avg_tool_duration_ms"] == 0.0


# [边界] 缺字段 event 不崩（缺 input_tokens / output_tokens / total_tokens）
def test_aggregate_tolerates_missing_fields(tmp_path: Path):
    p = tmp_path / "events.A.jsonl"
    _write(p, [
        {"kind": "event", "event_type": "model_call_end"},  # 全缺
        {"kind": "event", "event_type": "tool_call_end", "tool_name": "x"},
    ])
    s = aggregate_engine(p, "A")
    assert s.model_calls.total_calls == 1
    assert s.model_calls.total_input_tokens == 0
    assert s.tool_calls.total_calls == 1
    assert s.tool_calls.success_count == 0  # default False
    assert s.tool_calls.by_tool["x"]["calls"] == 1


# [边界] aggregate_team 多 engine
def test_aggregate_team_multi_engine(tmp_path: Path):
    p_a = tmp_path / "events.A.jsonl"
    p_b = tmp_path / "events.B.jsonl"
    _write(p_a, [_round_start(1), _round_end(1, 100), _model_call("m", 10, 5)])
    _write(p_b, [_round_start(1), _round_end(1, 200), _peer_sent()])
    out = aggregate_team(tmp_path, "team1", ["A", "B"])
    assert set(out.keys()) == {"A", "B"}
    assert out["A"].model_calls.total_calls == 1
    assert out["B"].peer_messages_sent == 1