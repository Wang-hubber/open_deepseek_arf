"""
[T] Relay primitives — JsonlTailer + SseFormatter.

Phase 7 / V1.x task 6. Verifies:

- `SseFormatter.format` produces the expected `id:` / `event:` / `data:`
  triple with the trailing blank-line terminator.
- `SseFormatter.parse_last_event_id` round-trips the
  `"{node_id}:{event_seq}"` form.
- `JsonlTailer` can be constructed and the simplified `__anext__`
  reads at least one line from a JSONL file.

Test angles: [构造] [方法] [序列化] [唯一性]
"""
import asyncio
import json
from pathlib import Path

import pytest

from arf._arf import JsonlTailer, SseFormatter


# ── T0 ──────────────────────────────────────────────────────────────────


def test_public_imports():
    """[构造] JsonlTailer + SseFormatter are re-exported from the public
    `arf` package (not just `arf._arf`). Locks the public surface so
    the re-export cannot be accidentally dropped.
    """
    from arf import JsonlTailer as PublicJsonlTailer
    from arf import SseFormatter as PublicSseFormatter

    assert PublicJsonlTailer is not None
    assert PublicSseFormatter is not None
    # Same class object as the compiled extension module, so we don't
    # accidentally satisfy the test with a stub.
    assert PublicJsonlTailer is JsonlTailer
    assert PublicSseFormatter is SseFormatter


# ── T1 ──────────────────────────────────────────────────────────────────


def test_sse_formatter_format_and_parse():
    """[方法][序列化] format() builds SSE triple; parse_last_event_id() round-trips."""
    # format() output structure
    sse = SseFormatter.format('{"a": 1}', 42, "peer_message")
    assert "id: 42" in sse
    assert "event: peer_message" in sse
    assert 'data: {"a": 1}' in sse
    assert sse.endswith("\n\n")

    # parse_last_event_id() round-trip
    node_id, seq = SseFormatter.parse_last_event_id("engine-A:42")
    assert node_id == "engine-A"
    assert seq == 42

    # parse_last_event_id() with malformed seq falls back to 0
    nid_fallback, seq_fallback = SseFormatter.parse_last_event_id("engine-B:notanumber")
    assert nid_fallback == "engine-B"
    assert seq_fallback == 0

    # parse_last_event_id() without ':' keeps the whole string and returns seq=0
    raw_id, raw_seq = SseFormatter.parse_last_event_id("solo")
    assert raw_id == "solo"
    assert raw_seq == 0


# ── T2 ──────────────────────────────────────────────────────────────────


def test_jsonl_tailer_reads_lines(tmp_path: Path):
    """[构造][方法] JsonlTailer construction + placeholder __anext__ reads one line."""
    p = tmp_path / "events.test.jsonl"
    p.write_text('{"kind":"event","x":1}\n{"kind":"event","x":2}\n')

    tailer = JsonlTailer(str(p), 0, 50)

    # The simplified `__anext__` returns a PyStr synchronously after
    # running a one-shot tokio runtime under the hood. Drive it via the
    # asyncio protocol so a future port to true async-tailing stays a
    # drop-in change.
    line = asyncio.run(_one_anext(tailer))
    parsed = json.loads(line)
    assert parsed["x"] in (1, 2)


async def _one_anext(tailer):
    """Helper that calls `__anext__` either as a coroutine or as a sync callable.

    Today the simplified Rust implementation returns a `str` synchronously,
    but the same helper works once `__anext__` is upgraded to a true
    coroutine — that is, callers should not depend on either shape.
    """
    val = tailer.__anext__()
    if asyncio.iscoroutine(val):
        return await val
    return val


# ── T3 ──────────────────────────────────────────────────────────────────


def test_jsonl_tailer_repr_includes_path(tmp_path: Path):
    """[唯一性] tailer __repr__ reflects file path and starting offset."""
    p = tmp_path / "x.jsonl"
    p.write_text("")

    tailer = JsonlTailer(str(p), since_event_seq=7, poll_interval_ms=25)
    rep = repr(tailer)
    assert "x.jsonl" in rep
    assert "since=7" in rep
    assert "poll_ms=25" in rep
