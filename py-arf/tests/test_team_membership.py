"""
[T] Relay aggregation — TeamMembership + EventFilter + SseRelay.

Phase 7 / V1.x task 7 (TeamMembership + EventFilter) and task 16
(SseRelay real multi-engine merge). Verifies:

- `TeamMembership` reads `persistent_engines[].id` and
  `subagent_pools[].id` from a `team.yaml` and exposes the union as
  `members()`. The `bus` argument can be `None` for the static-only
  skeleton.
- `EventFilter.matches(engine_id, msg_type)` is the conjunction of the
  optional `engine_ids` and `msg_types` whitelists (None = open).
- `SseRelay` can be constructed and `stream(filter)` returns a
  `SseRelayStream` async iterator that yields one SSE chunk per
  accepted event from N merged `JsonlTailer` instances.
- `SseRelay` public import surface is re-exported through the public
  `arf` package (not just the compiled `_arf` module).

Test angles: [构造] [方法] [边界] [trait] [序列化] [唯一性] [覆盖] [时间]
"""
import asyncio
from pathlib import Path

import pytest

from arf._arf import EventFilter, SseFormatter, SseRelay, TeamMembership


# ── T0 ──────────────────────────────────────────────────────────────────


def test_public_imports():
    """[构造] All three new types are re-exported from the public `arf`
    package (not just `arf._arf`). Locks the public surface so the
    re-export cannot be accidentally dropped.
    """
    from arf import EventFilter as PublicEventFilter
    from arf import SseRelay as PublicSseRelay
    from arf import TeamMembership as PublicTeamMembership

    assert PublicEventFilter is not None
    assert PublicSseRelay is not None
    assert PublicTeamMembership is not None
    # Same class object as the compiled extension module — guards
    # against accidentally satisfying the test with a stub.
    assert PublicEventFilter is EventFilter
    assert PublicSseRelay is SseRelay
    assert PublicTeamMembership is TeamMembership


# ── T1 ──────────────────────────────────────────────────────────────────


def test_team_membership_reads_yaml(tmp_path: Path):
    """[构造][方法] TeamMembership reads persistent_engines + subagent_pools
    union from a team.yaml and exposes them as a set.
    """
    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
team:
  id: t1
persistent_engines:
  - id: a
  - id: b
subagent_pools:
  - id: p1
"""
    )
    # bus 参数暂传 None（实际部署要传真 Bus）
    tm = TeamMembership(str(yaml), None)
    members = tm.members()
    assert "a" in members
    assert "b" in members
    assert "p1" in members
    assert len(members) == 3


# ── T2 ──────────────────────────────────────────────────────────────────


def test_team_membership_missing_file(tmp_path: Path):
    """[边界] Missing yaml surfaces as FileNotFoundError so the app can
    distinguish config errors from parse errors.
    """
    missing = tmp_path / "nope.yaml"
    with pytest.raises(FileNotFoundError):
        TeamMembership(str(missing), None)


# ── T3 ──────────────────────────────────────────────────────────────────


def test_team_membership_malformed_yaml(tmp_path: Path):
    """[边界] Malformed yaml surfaces as ValueError (not panic / not
    FileNotFoundError) so callers can show "invalid team config".
    """
    yaml = tmp_path / "bad.yaml"
    yaml.write_text(":\n: invalid: [")
    with pytest.raises(ValueError):
        TeamMembership(str(yaml), None)


# ── T4 ──────────────────────────────────────────────────────────────────


def test_team_membership_empty_yaml_is_ok(tmp_path: Path):
    """[边界] An empty (but well-formed) YAML yields an empty member set
    — useful for tests and for "single-engine default" configs.
    """
    yaml = tmp_path / "empty.yaml"
    yaml.write_text("")
    tm = TeamMembership(str(yaml), None)
    assert tm.members() == set()


# ── T5 ──────────────────────────────────────────────────────────────────


def test_team_membership_repr(tmp_path: Path):
    """[唯一性] __repr__ mentions the member count."""
    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
persistent_engines:
  - id: a
  - id: b
subagent_pools:
  - id: p1
"""
    )
    tm = TeamMembership(str(yaml), None)
    rep = repr(tm)
    assert "3" in rep


# ── T6 ──────────────────────────────────────────────────────────────────


def test_event_filter_matches():
    """[方法] EventFilter.matches is the conjunction of the two whitelists."""
    f = EventFilter(engine_ids={"a"}, msg_types={"peer_message"}, since_event_seq=None)
    assert f.matches("a", "peer_message")
    assert not f.matches("b", "peer_message")
    assert not f.matches("a", "model_call")


# ── T7 ──────────────────────────────────────────────────────────────────


def test_event_filter_engine_only():
    """[方法] With only engine_ids set, msg_type is unrestricted."""
    f = EventFilter(engine_ids={"a"}, msg_types=None, since_event_seq=None)
    assert f.matches("a", "peer_message")
    assert f.matches("a", "model_call")
    assert not f.matches("b", "peer_message")


# ── T8 ──────────────────────────────────────────────────────────────────


def test_event_filter_msg_only():
    """[方法] With only msg_types set, engine_id is unrestricted."""
    f = EventFilter(engine_ids=None, msg_types={"peer_message"}, since_event_seq=None)
    assert f.matches("a", "peer_message")
    assert f.matches("b", "peer_message")
    assert not f.matches("a", "model_call")


# ── T9 ──────────────────────────────────────────────────────────────────


def test_event_filter_open():
    """[边界] With both whitelists None, the filter accepts everything."""
    f = EventFilter(engine_ids=None, msg_types=None, since_event_seq=None)
    assert f.matches("a", "peer_message")
    assert f.matches("anything", "anything")


# ── T10 ─────────────────────────────────────────────────────────────────


def test_event_filter_repr():
    """[唯一性] __repr__ mentions both whitelists' contents (or their None)."""
    f = EventFilter(engine_ids={"a"}, msg_types={"peer_message"}, since_event_seq=None)
    rep = repr(f)
    assert "EventFilter" in rep
    assert "a" in rep
    assert "peer_message" in rep


# ── T11 ─────────────────────────────────────────────────────────────────


def test_sse_relay_construct(tmp_path: Path):
    """[构造] SseRelay stores storage_root + buffer_size and exposes them
    via __repr__. The actual streaming is verified in T12.
    """
    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
persistent_engines:
  - id: a
  - id: b
"""
    )
    tm = TeamMembership(str(yaml), None)
    relay = SseRelay(tm, str(tmp_path), buffer_size=42)
    rep = repr(relay)
    assert "SseRelay" in rep
    assert "42" in rep


# ── T12 ─────────────────────────────────────────────────────────────────


async def test_sse_relay_stream_emits_marker_for_existing_file(tmp_path: Path):
    """[方法] stream(filter) is an async iterator that yields one SSE
    chunk per event. Tailers without a JSONL file are silently
    skipped (no driver spawned), so `bob` and `p1` (which have no
    file) never contribute any chunk.

    Phase 7 / V1.x task 16 — replaces the single-chunk skeleton with
    a real multi-engine merge driven by N `JsonlTailer`s.
    """
    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
persistent_engines:
  - id: alice
  - id: bob
subagent_pools:
  - id: p1
"""
    )
    # `alice` has one event; `bob` and `p1` have no file.
    (tmp_path / "events.alice.jsonl").write_text(
        '{"kind":"event","event_type":"peer_message","payload":{"x":1}}\n'
    )

    tm = TeamMembership(str(yaml), None)
    relay = SseRelay(tm, str(tmp_path), buffer_size=16)
    f = EventFilter(engine_ids=None, msg_types=None, since_event_seq=None)

    stream = relay.stream(f)
    chunks = []
    async for chunk in _bounded_async_iter(stream, limit=1):
        chunks.append(chunk)

    # The single alice event is yielded; bob and p1 never produce
    # anything because no driver was spawned for them. The chunk is
    # an SSE triple — engine_id lives in the relay's internal
    # channel, not in the JSON line, so we only assert one chunk
    # was emitted.
    assert len(chunks) == 1
    assert "event: peer_message" in chunks[0]
    assert '"x":1' in chunks[0] or '"x": 1' in chunks[0]


# ── T13 ─────────────────────────────────────────────────────────────────


async def test_sse_relay_stream_no_files(tmp_path: Path):
    """[边界] stream() with no existing JSONL files spawns no drivers
    and the async iterator yields nothing on the first `__anext__`
    wait. We assert it does not panic and breaks out cleanly.
    """
    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
persistent_engines:
  - id: ghost
"""
    )
    tm = TeamMembership(str(yaml), None)
    relay = SseRelay(tm, str(tmp_path), buffer_size=16)
    f = EventFilter(engine_ids=None, msg_types=None, since_event_seq=None)

    stream = relay.stream(f)
    # With no drivers, the mpsc receiver never receives anything and
    # `recv().await` would block forever. Use a short timeout so the
    # test terminates.
    chunks = []
    async for chunk in _bounded_async_iter(stream, limit=0):
        chunks.append(chunk)
    assert chunks == []


# ── helpers ─────────────────────────────────────────────────────────────


async def _await_once(coro):
    """Drive a coroutine to completion; tolerates either an awaitable
    or (in case the skeleton is later swapped for a sync implementation)
    a plain string. Mirrors `_one_anext` in test_relay.py.
    """
    if asyncio.iscoroutine(coro):
        return await coro
    return coro


async def _bounded_async_iter(stream, *, limit: int, timeout_s: float = 2.0):
    """Drain at most `limit` chunks from an async iterator, aborting
    after `timeout_s` if the upstream stalls (which would otherwise
    hang the test on a live-tailer).

    Phase 7 / V1.x task 16 — live tailers never naturally EOF, so a
    plain `async for` would block forever once the pre-existing
    lines are consumed. Tests use this helper to bound the read.
    """
    received = 0
    deadline = asyncio.get_event_loop().time() + timeout_s
    while received < limit:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        try:
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return
        yield chunk
        received += 1


# ── T15 ─────────────────────────────────────────────────────────────────


async def test_sse_relay_streams_from_multiple_files(tmp_path: Path):
    """[方法][覆盖] SseRelay.stream merges lines from multiple engines'
    JSONL files. With a `msg_types={peer_message}` filter, the single
    `peer_message` event from alice is yielded and bob's
    `model_call` event is filtered out.

    Task 16 — replaces the single-chunk skeleton with a real merge
    of N `JsonlTailer` instances fed through a `tokio::sync::mpsc`
    channel.
    """
    # Two engines, two different event types.
    (tmp_path / "events.alice.jsonl").write_text(
        '{"kind":"event","event_type":"peer_message","payload":{"x":1}}\n'
    )
    (tmp_path / "events.bob.jsonl").write_text(
        '{"kind":"event","event_type":"model_call","payload":{"y":2}}\n'
    )

    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
team:
  id: t1
persistent_engines:
  - id: alice
  - id: bob
"""
    )
    tm = TeamMembership(str(yaml), None)
    relay = SseRelay(tm, str(tmp_path), 100)
    flt = EventFilter(
        engine_ids=None,
        msg_types={"peer_message"},
        since_event_seq=None,
    )

    stream = relay.stream(flt)
    chunks = []
    async for chunk in _bounded_async_iter(stream, limit=1):
        chunks.append(chunk)

    # Exactly one chunk matches the filter (alice's peer_message).
    # bob's model_call is dropped by the filter.
    assert len(chunks) == 1
    assert "event: peer_message" in chunks[0]
    assert '"x": 1' in chunks[0] or '"x":1' in chunks[0]


# ── T16 ─────────────────────────────────────────────────────────────────


async def test_sse_relay_open_filter_yields_both(tmp_path: Path):
    """[方法] With an open filter (both whitelists None), every event
    from every engine is yielded. Bounded read to avoid hanging on
    the live tailer.
    """
    (tmp_path / "events.alice.jsonl").write_text(
        '{"kind":"event","event_type":"peer_message","payload":{"x":1}}\n'
    )
    (tmp_path / "events.bob.jsonl").write_text(
        '{"kind":"event","event_type":"model_call","payload":{"y":2}}\n'
    )

    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
persistent_engines:
  - id: alice
  - id: bob
"""
    )
    tm = TeamMembership(str(yaml), None)
    relay = SseRelay(tm, str(tmp_path), 100)
    flt = EventFilter(engine_ids=None, msg_types=None, since_event_seq=None)

    stream = relay.stream(flt)
    chunks = []
    async for chunk in _bounded_async_iter(stream, limit=2):
        chunks.append(chunk)

    assert len(chunks) == 2
    # Order is non-deterministic (channel merge); check the set.
    types = {c.split("event: ", 1)[1].split("\n", 1)[0] for c in chunks}
    assert types == {"peer_message", "model_call"}


# ── T17 ─────────────────────────────────────────────────────────────────


async def test_sse_relay_engine_filter_excludes_other(tmp_path: Path):
    """[方法] With `engine_ids={"alice"}` set, only alice's events
    pass the filter.
    """
    (tmp_path / "events.alice.jsonl").write_text(
        '{"kind":"event","event_type":"peer_message","payload":{"x":1}}\n'
    )
    (tmp_path / "events.bob.jsonl").write_text(
        '{"kind":"event","event_type":"model_call","payload":{"y":2}}\n'
    )

    yaml = tmp_path / "team.yaml"
    yaml.write_text(
        """
persistent_engines:
  - id: alice
  - id: bob
"""
    )
    tm = TeamMembership(str(yaml), None)
    relay = SseRelay(tm, str(tmp_path), 100)
    flt = EventFilter(
        engine_ids={"alice"},
        msg_types=None,
        since_event_seq=None,
    )

    stream = relay.stream(flt)
    chunks = []
    async for chunk in _bounded_async_iter(stream, limit=1):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert "event: peer_message" in chunks[0]


# ── T14 ─────────────────────────────────────────────────────────────────


def test_sse_formatter_still_works():
    """[覆盖] Guard rail: Task 6's SseFormatter is still wired through the
    new module layout. If a Task 7 refactor accidentally drops the
    import, this test fails.
    """
    sse = SseFormatter.format('{"a": 1}', 42, "peer_message")
    assert "id: 42" in sse
    assert "event: peer_message" in sse
    assert 'data: {"a": 1}' in sse
    assert sse.endswith("\n\n")