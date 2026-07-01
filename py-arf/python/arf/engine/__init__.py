"""Engine module — API spec per Phase 6 design §14.

This module defines the Python API for the Engine. When the Rust Engine
is implemented (crates/arf-engine), these types will be replaced by PyO3
bindings. Until then, this module serves as:

  1. A living API reference — docstrings map to design doc sections
  2. Importable stubs for the phase6_flat integration test
  3. Design validation — writing the test drives out the API shape

Design reference: docs/dev/phase6/phase6-engine-design.md
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# §2.5 Checkpoint — 5 fixed positions
# ═══════════════════════════════════════════════════════════════════════

class Checkpoint(enum.Enum):
    """Five fixed checkpoint positions in the ReAct loop (§2.5)."""
    BeforeModelCall = "before_model_call"
    AfterModelCall = "after_model_call"
    BeforeToolExec = "before_tool_exec"
    AfterToolExec = "after_tool_exec"
    RoundEnd = "round_end"


# ═══════════════════════════════════════════════════════════════════════
# §2.3 Route — Strict | Discovery
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Capability:
    """Multi key-value requirements with AND semantics (§2.3.1)."""
    requirements: list[tuple[str, str]]

    def __init__(self, key: str, value: str):
        object.__setattr__(self, "requirements", [(key, value)])

    @classmethod
    def all_of(cls, **kwargs: str) -> "Capability":
        """Multi-requirement capability match (AND)."""
        return object.__new__(cls)


@dataclass(frozen=True)
class Route:
    """Binary route: Strict (exact NodeIds) or Discovery (capability match) (§2.3)."""
    kind: str  # "strict" | "discovery"
    node_ids: list[str] = field(default_factory=list)
    capability: Capability | None = None

    @staticmethod
    def strict(*, node_ids: list[str]) -> "Route":
        return Route(kind="strict", node_ids=node_ids)

    @staticmethod
    def discovery(*, capability: Capability) -> "Route":
        return Route(kind="discovery", capability=capability)


# ═══════════════════════════════════════════════════════════════════════
# §2.4 State
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class OverView:
    """Aggregated metrics — O(1) access (§2.4)."""
    round_count: int = 0
    turn_count: int = 0
    context_tokens: int = 0
    model_context_window: int = 200000
    runtime: float = 0.0
    last_user_message: str = ""


@dataclass
class State:
    """Engine-private session state (§2.4)."""
    messages: list[dict] = field(default_factory=list)
    over_view: OverView = field(default_factory=OverView)


# ═══════════════════════════════════════════════════════════════════════
# §7 AgentConfig
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AgentConfig:
    """Declarative agent configuration (§7).

    Does not hold NodeBinding — Nodes connect via bus.connect() (§3).
    EngineBuilder.build() validates routes against current BusGraph.
    """
    agent_id: str
    system_prompt_template: str
    model_config: dict
    max_turns: int = 10
    routes: dict[str, Route] = field(default_factory=dict)
    checkpoint_rules: list["CheckpointRule"] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# §2.5 CheckpointRule — when/build/route quad
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CheckpointRule:
    """App-injected trigger at a checkpoint position (§2.5).

    Engine calls when(state) → if true, calls build(state) → publishes
    the ActionMessage via route.
    """
    name: str
    trigger: Checkpoint
    when: Any  # callable(state) -> bool
    build: Any  # callable(state) -> ActionMessage
    route: Route

    @staticmethod
    def every_n_rounds(
        *,
        trigger: Checkpoint,
        every_n: int,
        build: Any,
        route: Route,
    ) -> "CheckpointRule":
        """Standard constructor: trigger every N rounds."""
        return CheckpointRule(
            name=f"every_{every_n}_rounds",
            trigger=trigger,
            when=lambda s: s.over_view.round_count > 0
            and s.over_view.round_count % every_n == 0,
            build=build,
            route=route,
        )

    @staticmethod
    def when_context_over(
        *,
        trigger: Checkpoint,
        ratio: float,
        build: Any,
        route: Route,
    ) -> "CheckpointRule":
        """Standard constructor: trigger when context > ratio * window."""
        return CheckpointRule(
            name=f"context_over_{int(ratio * 100)}pct",
            trigger=trigger,
            when=lambda s: s.over_view.context_tokens
            > int(s.over_view.model_context_window * ratio),
            build=build,
            route=route,
        )


# ═══════════════════════════════════════════════════════════════════════
# §2.1 ActionMessage — trait (Python: duck-typed dicts from build())
# ═══════════════════════════════════════════════════════════════════════

class MemoryOp:
    """Memory operation message (§11.1)."""
    @staticmethod
    def extract(*, messages: list[dict]) -> dict:
        return {"msg_type": "memory_op", "action": "extract", "messages": messages}


class CompactOp:
    """Compaction operation message (§11.3)."""
    @staticmethod
    def new(*, messages: list[dict]) -> dict:
        return {"msg_type": "compact_op", "messages": messages}


# ═══════════════════════════════════════════════════════════════════════
# Engine & EngineBuilder (§3, §6)
# ═══════════════════════════════════════════════════════════════════════

class Engine:
    """ReAct loop engine on the Bus (§6).

    Owns: State machine (idle/processing/waiting/stopped), 5 checkpoints,
          WaitEvent queue, Park/Resume mechanism.

    Does NOT own: any concrete Node instances, route tables,
                  CheckpointRule list, business interpretation of messages.
    """

    def __init__(self, bus: Any, config: AgentConfig):
        self._bus = bus
        self._config = config

    async def start_session(self, *, session_id: str) -> "Session":
        """Create a new session and start the engine."""
        ...


class Session:
    """A running agent session — holds state across chat() calls.

    Created by engine.start_session(). Each chat() call = one round.
    """

    def __init__(self, engine: Engine, session_id: str):
        self.engine = engine
        self.session_id = session_id
        self.state = State()

    async def chat(self, *, user_input: str) -> str:
        """Process one round of user input through the ReAct loop (§4).

        Engine:
        1. Appends user input to state.messages
        2. Runs Checkpoint::BeforeModelCall rules
        3. Publishes ModelCall(Query) → parks waiting for response
        4. Runs Checkpoint::AfterModelCall rules
        5. If tool_calls: publishes ToolExec(Query) → parks → goto 2
        6. Runs Checkpoint::RoundEnd rules
        7. Returns final_output → idle
        """
        ...


class EngineBuilder:
    """Assembles the Engine from AgentConfig + Bus (§3).

    Usage:
        engine = await EngineBuilder.new(bus=bus).build(config=config)
    """

    def __init__(self, bus: Any):
        self._bus = bus

    @staticmethod
    def new(*, bus: Any) -> "EngineBuilder":
        """Create a builder bound to a Bus."""
        return EngineBuilder(bus=bus)

    async def build(self, config: AgentConfig) -> Engine:
        """Validate routes against current BusGraph, then build.

        Raises BuildError if:
          - Strict route references a NodeId not currently online
          - Discovery route has no matching capabilities in BusGraph
        """
        ...
