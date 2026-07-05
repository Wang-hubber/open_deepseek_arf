"""
[T] Team abstraction — TeamConfig + TeamBuilder + Team skeleton.

Phase 7 / V1.x task 8. Verifies:

- `TeamConfig.from_yaml` reads the team schema (team.id, team.bus,
  persistent_engines[].id / .config / .auto_subscribe,
  subagent_pools[].id / .config / .size / .max_queue_wait_ms).
- `TeamConfig.new(...)` plus `add_persistent_engine` / `add_subagent_pool`
  round-trips through `to_yaml` (greppable output).
- `TeamConfig.from_yaml` raises `FileNotFoundError` / `ValueError`
  distinctly on the two failure modes.
- `TeamBuilder.from_config(bus, config).build()` is async and yields a
  `Team` placeholder with the right config.
- `team.start()` flips `started = True` and awaits to `None`;
  `team.stop()` is an idempotent no-op awaitable.
- All five new types (`TeamConfig`, `TeamBuilder`, `Team`, `EngineSpec`,
  `PoolSpec`) are re-exported from the public `arf` package (not just
  the compiled `_arf` module).

Test angles: [构造] [方法] [边界] [trait] [序列化] [唯一性] [覆盖]
"""
from pathlib import Path

import pytest

from arf._arf import (
    EngineSpec,
    PoolSpec,
    Team,
    TeamBuilder,
    TeamConfig,
)


# ── T0  Public surface ─────────────────────────────────────────────────


def test_public_imports():
    """[构造] All five new types are re-exported from the public `arf`
    package (not just `arf._arf`). Locks the public surface so the
    re-export cannot be accidentally dropped.
    """
    from arf import EngineSpec as PublicEngineSpec
    from arf import PoolSpec as PublicPoolSpec
    from arf import Team as PublicTeam
    from arf import TeamBuilder as PublicTeamBuilder
    from arf import TeamConfig as PublicTeamConfig

    assert PublicTeamConfig is not None
    assert PublicTeamBuilder is not None
    assert PublicTeam is not None
    assert PublicEngineSpec is not None
    assert PublicPoolSpec is not None
    # Same class object as the compiled extension module, so we don't
    # accidentally satisfy the test with a stub.
    assert PublicTeamConfig is TeamConfig
    assert PublicTeamBuilder is TeamBuilder
    assert PublicTeam is Team
    assert PublicEngineSpec is EngineSpec
    assert PublicPoolSpec is PoolSpec


# ── T1  YAML parsing — full schema ─────────────────────────────────────


def test_team_config_from_yaml_full_schema(tmp_path: Path):
    """[方法][序列化] `from_yaml` reads every documented field, including
    auto_subscribe on a persistent_engine and max_queue_wait_ms on a
    subagent_pool.
    """
    yaml = tmp_path / "t.yaml"
    yaml.write_text(
        """
team:
  id: dev
  bus: shared
  description: development team
persistent_engines:
  - id: pm
    config: ./agents/pm.yaml
  - id: data
    config: ./agents/data.yaml
    auto_subscribe:
      - peer_message
      - checkpoint
subagent_pools:
  - id: tc
    config: ./agents/tc.yaml
    size: 4
    max_queue_wait_ms: 2000
"""
    )
    cfg = TeamConfig.from_yaml(str(yaml))
    assert cfg.team_id == "dev"
    assert cfg.bus_id == "shared"
    assert cfg.description == "development team"
    assert len(cfg.persistent_engines) == 2
    assert cfg.persistent_engines[0].engine_id == "pm"
    assert cfg.persistent_engines[0].config_path == "./agents/pm.yaml"
    assert cfg.persistent_engines[0].auto_subscribe == []
    assert cfg.persistent_engines[1].engine_id == "data"
    assert cfg.persistent_engines[1].auto_subscribe == [
        "peer_message",
        "checkpoint",
    ]
    assert len(cfg.subagent_pools) == 1
    assert cfg.subagent_pools[0].pool_id == "tc"
    assert cfg.subagent_pools[0].config_path == "./agents/tc.yaml"
    assert cfg.subagent_pools[0].size == 4
    assert cfg.subagent_pools[0].max_queue_wait_ms == 2000


# ── T2  YAML parsing — minimal schema + defaults ───────────────────────


def test_team_config_from_yaml_minimal(tmp_path: Path):
    """[方法][边界] `from_yaml` with a bare-minimum YAML still fills the
    required defaults (size=1, max_queue_wait_ms=None, auto_subscribe=[]).
    """
    yaml = tmp_path / "min.yaml"
    yaml.write_text(
        """
team:
  id: solo
  bus: shared
persistent_engines:
  - id: only
    config: ./only.yaml
"""
    )
    cfg = TeamConfig.from_yaml(str(yaml))
    assert cfg.team_id == "solo"
    assert cfg.bus_id == "shared"
    assert cfg.description is None
    assert len(cfg.persistent_engines) == 1
    assert cfg.persistent_engines[0].auto_subscribe == []
    assert cfg.subagent_pools == []


def test_team_config_from_yaml_defaults_when_team_section_missing(tmp_path: Path):
    """[边界] With no `team:` section, the defaults (`default` / `shared`)
    kick in so the binding doesn't crash on an empty YAML.
    """
    yaml = tmp_path / "empty.yaml"
    yaml.write_text("# nothing here\n")
    cfg = TeamConfig.from_yaml(str(yaml))
    assert cfg.team_id == "default"
    assert cfg.bus_id == "shared"
    assert cfg.persistent_engines == []
    assert cfg.subagent_pools == []


# ── T3  YAML error paths ───────────────────────────────────────────────


def test_team_config_from_yaml_missing_file(tmp_path: Path):
    """[边界] A non-existent path raises `FileNotFoundError`."""
    with pytest.raises(FileNotFoundError):
        TeamConfig.from_yaml(str(tmp_path / "does_not_exist.yaml"))


def test_team_config_from_yaml_malformed(tmp_path: Path):
    """[边界] A syntactically broken YAML raises `ValueError`."""
    yaml = tmp_path / "bad.yaml"
    yaml.write_text("this: is: not: valid: yaml: ::\n  - oops")
    with pytest.raises(ValueError):
        TeamConfig.from_yaml(str(yaml))


# ── T4  Programmatic add + round-trip ──────────────────────────────────


def test_team_config_dynamic_add_and_to_yaml(tmp_path: Path):
    """[方法][序列化] `new(...)` + `add_persistent_engine` +
    `add_subagent_pool` then `to_yaml` writes both IDs and the
    engine-level auto_subscribe list.
    """
    cfg = TeamConfig("dev", "shared")
    cfg.add_persistent_engine("pm", "./pm.yaml", [])
    cfg.add_persistent_engine("data", "./data.yaml", ["peer_message"])
    cfg.add_subagent_pool("tc", "./tc.yaml", 4, None)
    out = tmp_path / "out.yaml"
    cfg.to_yaml(str(out))
    text = out.read_text()
    assert "team:" in text
    assert "id: dev" in text
    assert "bus: shared" in text
    assert "pm" in text
    assert "data" in text
    assert "peer_message" in text
    assert "tc" in text
    assert "size: 4" in text


def test_team_config_to_yaml_omits_empty_sections(tmp_path: Path):
    """[边界] An empty config emits only the `team:` header (no empty
    persistent_engines / subagent_pools blocks)."""
    cfg = TeamConfig("solo", "shared")
    out = tmp_path / "solo.yaml"
    cfg.to_yaml(str(out))
    text = out.read_text()
    assert "team:" in text
    assert "persistent_engines" not in text
    assert "subagent_pools" not in text


# ── T5  Construction defaults ──────────────────────────────────────────


def test_team_config_new_defaults():
    """[构造][trait] A freshly constructed `TeamConfig` has empty engine
    and pool lists, `description=None`, `peer_topology=None`, and the
    two required string fields.
    """
    cfg = TeamConfig("dev", "shared")
    assert cfg.team_id == "dev"
    assert cfg.bus_id == "shared"
    assert cfg.description is None
    assert cfg.peer_topology is None
    assert cfg.persistent_engines == []
    assert cfg.subagent_pools == []
    assert repr(cfg) == (
        "TeamConfig(team_id='dev', bus_id='shared', "
        "persistent_engines=0, subagent_pools=0)"
    )


def test_engine_spec_and_pool_spec_constructors():
    """[构造] `EngineSpec` and `PoolSpec` are constructable directly with
    positional kwargs (mirrors how the YAML round-trip produces them).
    """
    e = EngineSpec("pm", "./pm.yaml", ["peer_message"])
    assert e.engine_id == "pm"
    assert e.config_path == "./pm.yaml"
    assert e.auto_subscribe == ["peer_message"]

    p = PoolSpec("tc", "./tc.yaml", 4, 2000)
    assert p.pool_id == "tc"
    assert p.config_path == "./tc.yaml"
    assert p.size == 4
    assert p.max_queue_wait_ms == 2000

    p_default = PoolSpec("solo", "./solo.yaml")
    assert p_default.size == 1
    assert p_default.max_queue_wait_ms is None

    e_default = EngineSpec("solo", "./solo.yaml")
    assert e_default.auto_subscribe == []


# ── T6  TeamBuilder + Team async surface ──────────────────────────────


@pytest.mark.asyncio
async def test_team_builder_smoke():
    """[方法] `TeamBuilder.from_config(None, cfg).build()` returns a
    `Team` carrying the config; `team.config.team_id` round-trips.
    `bus = None` is acceptable since the Team skeleton does not yet
    connect to a Bus.
    """
    cfg = TeamConfig("dev", "shared")
    builder = TeamBuilder.from_config(None, cfg)
    team = await builder.build()
    assert isinstance(team, Team)
    assert team.config.team_id == "dev"
    assert team.config.bus_id == "shared"
    assert team.started is False
    assert repr(team) == "Team(team_id='dev', started=False)"


@pytest.mark.asyncio
async def test_team_start_and_stop():
    """[方法][trait] `team.start()` flips `started` to True and awaits to
    None; `team.stop()` is an idempotent no-op awaitable.
    """
    cfg = TeamConfig("dev", "shared")
    builder = TeamBuilder.from_config(None, cfg)
    team = await builder.build()
    assert team.started is False
    result = await team.start()
    assert result is None
    assert team.started is True
    # Stop is idempotent.
    assert await team.stop() is None
    assert await team.stop() is None
    assert team.started is True  # stop doesn't clear the flag


@pytest.mark.asyncio
async def test_team_config_snapshot_is_independent():
    """[trait][唯一性] The Team's `config` getter returns a clone — mutating
    it on the Python side does not change the Team's view (and vice
    versa). Both the engine and pool lists on the snapshot must be
    writable from Python without affecting the original config.
    """
    cfg = TeamConfig("dev", "shared")
    cfg.add_persistent_engine("pm", "./pm.yaml", [])
    builder = TeamBuilder.from_config(None, cfg)
    team = await builder.build()

    snap = team.config
    assert len(snap.persistent_engines) == 1
    # Append to the snapshot; the original config must be untouched.
    snap.add_persistent_engine("data", "./data.yaml", [])
    assert len(snap.persistent_engines) == 2
    assert len(team.config.persistent_engines) == 1
    # And the original config mutation does not bleed into the snapshot.
    cfg.add_subagent_pool("tc", "./tc.yaml", 4, None)
    # The team's snapshot is independent of subsequent mutations to the
    # original config (snapshot was captured at build time).
    assert len(team.config.subagent_pools) == 0
    assert len(snap.subagent_pools) == 0
    assert len(cfg.subagent_pools) == 1