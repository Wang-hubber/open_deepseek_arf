"""
[T] Relay primitives — JsonlTailer + SseFormatter.

Phase 7 / V1.x task 6 (and task 15 for the polling upgrade). Verifies:

- `SseFormatter.format` produces the expected `id:` / `event:` / `data:`
  triple with the trailing blank-line terminator.
- `SseFormatter.parse_last_event_id` round-trips the
  `"{node_id}:{event_seq}"` form.
- `JsonlTailer` can be constructed and the async `__anext__` reads at
  least one line from a pre-populated JSONL file (cursor preserved
  across calls).
- `JsonlTailer` actually polls: a line appended to the file after the
  tailer is constructed is yielded on the next `__anext__`.

Test angles: [构造] [方法] [序列化] [唯一性] [时间]
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
    """[构造][方法] JsonlTailer construction + async __anext__ reads two lines."""
    p = tmp_path / "events.test.jsonl"
    p.write_text('{"kind":"event","x":1}\n{"kind":"event","x":2}\n')

    tailer = JsonlTailer(str(p), 0, 50)

    async def _read_two():
        lines = []
        # Drive via the asyncio protocol so a future port stays a
        # drop-in change. Both awaits must succeed and yield distinct
        # x values (cursor preservation across calls).
        for _ in range(2):
            lines.append(await _one_anext(tailer))
        return lines

    lines = asyncio.run(_read_two())
    parsed_xs = sorted(json.loads(line)["x"] for line in lines)
    assert parsed_xs == [1, 2]


async def _one_anext(tailer):
    """Helper that calls `__anext__` either as a coroutine, a Future,
    or a sync callable.

    The async Rust implementation returns a `_asyncio.Future` (via
    `pyo3_async_runtimes::future_into_py`), which is awaitable but is
    not a `collections.abc.Coroutine` — `asyncio.iscoroutine()`
    returns False for it. So we accept any awaitable.
    """
    val = tailer.__anext__()
    if asyncio.iscoroutine(val) or asyncio.isfuture(val) or hasattr(val, "__await__"):
        return await val
    return val


def test_jsonl_tailer_polling_yields_new_lines(tmp_path: Path):
    """[时间][方法] JsonlTailer polls a file and yields lines appended
    after construction.

    Phase 7 / V1.x task 15 — replaces the one-shot synchronous-read
    placeholder with true async polling. The first `__anext__` reads
    the pre-existing line; after we append a second line and wait
    `poll_interval_ms * 2`, the next `__anext__` returns the appended
    line.
    """
    p = tmp_path / "events.live.jsonl"
    p.write_text('{"kind":"event","x":1}\n')

    poll_ms = 50
    tailer = JsonlTailer(str(p), since_event_seq=0, poll_interval_ms=poll_ms)

    async def _drive():
        # 1. Read the pre-existing line.
        first = await _one_anext(tailer)
        first_parsed = json.loads(first)
        assert first_parsed["x"] == 1

        # 2. Append a new line while the tailer is alive.
        with p.open("a") as f:
            f.write('{"kind":"event","x":2}\n')

        # 3. Wait for the poll cycle, then read the appended line.
        # `asyncio.sleep` takes seconds, so convert ms → seconds.
        await asyncio.sleep(poll_ms / 1000.0 * 2)
        second = await _one_anext(tailer)
        return json.loads(second)

    second_parsed = asyncio.run(_drive())
    assert second_parsed["x"] == 2


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
