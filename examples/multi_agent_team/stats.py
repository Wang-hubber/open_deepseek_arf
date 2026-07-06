"""JSONL → TokenStats on-demand aggregation (Task 18c).

Each call to /stats/* scans the JSONL files from scratch. Files are small
(per-engine, append-only) so the cost is negligible — milliseconds even
for thousands of events.

Public API:
    iter_events(jsonl_path)              → Iterator[dict]
    aggregate_engine(jsonl_path, eid)     → EngineStats
    aggregate_team(root, team_id, members) → dict[eid, EngineStats]
    team_rollup(per_engine)              → dict
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class ModelCallStats:
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    by_model: dict[str, int] = field(default_factory=dict)


@dataclass
class ToolCallStats:
    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: int = 0
    by_tool: dict[str, dict] = field(default_factory=dict)


@dataclass
class RoundStats:
    total_rounds: int = 0
    total_duration_ms: int = 0
    by_round: list[dict] = field(default_factory=list)


@dataclass
class EngineStats:
    engine_id: str
    rounds: RoundStats = field(default_factory=RoundStats)
    model_calls: ModelCallStats = field(default_factory=ModelCallStats)
    tool_calls: ToolCallStats = field(default_factory=ToolCallStats)
    peer_messages_sent: int = 0
    peer_messages_received: int = 0


def iter_events(jsonl_path: Path) -> Iterator[dict]:
    """Yield parsed event dicts from one JSONL file (skips snapshot/save)."""
    if not jsonl_path.exists():
        return
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(v, dict):
                continue
            if v.get("kind") != "event":
                continue
            yield v


def _absorb_model_call(stats: ModelCallStats, ev: dict) -> None:
    stats.total_calls += 1
    in_t = int(ev.get("input_tokens") or 0)
    out_t = int(ev.get("output_tokens") or 0)
    total_t = int(ev.get("total_tokens") or (in_t + out_t))
    stats.total_input_tokens += in_t
    stats.total_output_tokens += out_t
    stats.total_tokens += total_t
    model = ev.get("model") or "unknown"
    stats.by_model[model] = stats.by_model.get(model, 0) + total_t


def _absorb_tool_call(stats: ToolCallStats, ev: dict) -> None:
    stats.total_calls += 1
    success = bool(ev.get("success", False))
    if success:
        stats.success_count += 1
    else:
        stats.failure_count += 1
    duration = int(ev.get("duration_ms") or 0)
    stats.total_duration_ms += duration
    tool = ev.get("tool_name") or "unknown"
    bucket = stats.by_tool.setdefault(
        tool, {"calls": 0, "success": 0, "failure": 0, "total_duration_ms": 0}
    )
    bucket["calls"] += 1
    bucket["success" if success else "failure"] += 1
    bucket["total_duration_ms"] += duration


def _absorb_round_start(stats: RoundStats, ev: dict) -> None:
    stats.total_rounds += 1
    stats.by_round.append({
        "round": ev.get("round"),
        "started_at": ev.get("at"),
    })


def _absorb_round_end(stats: RoundStats, ev: dict) -> None:
    r = ev.get("round")
    duration = int(ev.get("duration_ms") or 0)
    # Match the most recent round_start with the same round number that
    # hasn't already been ended.
    for entry in reversed(stats.by_round):
        if entry.get("round") == r and "started_at" in entry and "ended_at" not in entry:
            entry["ended_at"] = ev.get("at")
            entry["duration_ms"] = duration
            stats.total_duration_ms += duration
            return


def aggregate_engine(jsonl_path: Path, engine_id: str) -> EngineStats:
    """Rebuild full EngineStats from a single JSONL file."""
    stats = EngineStats(engine_id=engine_id)
    for ev in iter_events(jsonl_path):
        et = ev.get("event_type", "")
        if et == "round_start":
            _absorb_round_start(stats.rounds, ev)
        elif et == "round_end":
            _absorb_round_end(stats.rounds, ev)
        elif et == "model_call_end":
            _absorb_model_call(stats.model_calls, ev)
        elif et == "tool_call_end":
            _absorb_tool_call(stats.tool_calls, ev)
        elif et == "peer_message_sent":
            stats.peer_messages_sent += 1
        elif et == "peer_reply_received":
            stats.peer_messages_received += 1
    return stats


def aggregate_team(
    storage_root: Path, team_id: str, members: list[str]
) -> dict[str, EngineStats]:
    """Per-engine stats for every member of `team_id`."""
    return {
        m: aggregate_engine(storage_root / f"events.{m}.jsonl", m)
        for m in members
    }


def team_rollup(per_engine: dict[str, EngineStats]) -> dict:
    """Sum per-engine stats into team-session totals."""
    total_rounds = sum(e.rounds.total_rounds for e in per_engine.values())
    total_model_calls = sum(e.model_calls.total_calls for e in per_engine.values())
    total_input_tokens = sum(e.model_calls.total_input_tokens for e in per_engine.values())
    total_output_tokens = sum(e.model_calls.total_output_tokens for e in per_engine.values())
    total_tokens = sum(e.model_calls.total_tokens for e in per_engine.values())
    by_model: dict[str, int] = {}
    for e in per_engine.values():
        for model, tokens in e.model_calls.by_model.items():
            by_model[model] = by_model.get(model, 0) + tokens

    total_tool_calls = sum(e.tool_calls.total_calls for e in per_engine.values())
    total_tool_success = sum(e.tool_calls.success_count for e in per_engine.values())
    total_tool_failure = sum(e.tool_calls.failure_count for e in per_engine.values())
    total_tool_duration_ms = sum(e.tool_calls.total_duration_ms for e in per_engine.values())
    by_tool: dict[str, dict] = {}
    for e in per_engine.values():
        for tool, b in e.tool_calls.by_tool.items():
            dest = by_tool.setdefault(
                tool, {"calls": 0, "success": 0, "failure": 0, "total_duration_ms": 0}
            )
            for k in ("calls", "success", "failure", "total_duration_ms"):
                dest[k] += b.get(k, 0)

    total_peer_sent = sum(e.peer_messages_sent for e in per_engine.values())
    total_peer_recv = sum(e.peer_messages_received for e in per_engine.values())

    return {
        "team_engines": len(per_engine),
        "total_rounds": total_rounds,
        "total_model_calls": total_model_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "by_model": by_model,
        "total_tool_calls": total_tool_calls,
        "total_tool_success": total_tool_success,
        "total_tool_failure": total_tool_failure,
        "tool_failure_rate": (
            total_tool_failure / total_tool_calls if total_tool_calls else 0.0
        ),
        "avg_tool_duration_ms": (
            total_tool_duration_ms / total_tool_calls if total_tool_calls else 0.0
        ),
        "by_tool": by_tool,
        "total_peer_messages_sent": total_peer_sent,
        "total_peer_messages_received": total_peer_recv,
    }