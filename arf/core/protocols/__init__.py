"""Protocol definitions — abstract interfaces for all framework domains."""
from arf.core.protocols.engine import (
    LoopStrategy, StateStore, ToolExecutor, Planner,
)
from arf.core.protocols.resources import (
    ToolResolver, ToolProvider, ToolRetriever, ToolBackend, ToolDefinition,
)
from arf.core.protocols.hooks import HookRunner
from arf.core.protocols.guardrails import (
    GuardRunner, InputGuardrail, OutputGuardrail, ToolGuardrail,
)
from arf.core.protocols.compaction import CompactionStrategy
from arf.core.protocols.sandbox import ToolSandbox
from arf.core.protocols.concurrency import TaskScheduler
from arf.core.protocols.human_loop import ApprovalPoint, ApprovalChannel
from arf.core.protocols.communication import (
    AgentBus, PeerAgent, TaskDelegator, Supervisor,
    SharedWorkspace, Lock, ConsensusProtocol,
    AgentMessage, AgentInfo,
)
from arf.core.protocols.event_bus import EventBus, EventStream
from arf.core.protocols.tracer import Tracer
from arf.core.protocols.prompt import SystemPromptProvider
from arf.core.protocols.replay import ReplayController, ReplayTrace, TurnRecord
from arf.core.protocols.evaluation import (
    EvalRunner, MetricCalculator, EvalCase, EvalDataset, EvalBenchmark,
    EvalSummary, EvalReport, EvalDiff, BenchmarkBuilder, EvalComparator,
)
from arf.core.protocols.plugin import PluginProtocol
from arf.core.plugin_context import PluginContext
from arf.core.execution import (
    Decision,
    Executable,
    ExecuteResult,
    ExecutionError,
    RetryPolicy,
    Wave,
)

__all__ = [
    "LoopStrategy", "StateStore", "ToolExecutor", "Planner",
    "ToolResolver", "ToolProvider", "ToolRetriever", "ToolBackend", "ToolDefinition",
    "HookRunner",
    "GuardRunner", "InputGuardrail", "OutputGuardrail", "ToolGuardrail",
    "CompactionStrategy",
    "ToolSandbox",
    "TaskScheduler",
    "ApprovalPoint", "ApprovalChannel",
    "AgentBus", "PeerAgent", "TaskDelegator", "Supervisor",
    "SharedWorkspace", "Lock", "ConsensusProtocol",
    "AgentMessage", "AgentInfo",
    "EventBus", "EventStream",
    "Tracer",
    "SystemPromptProvider",
    "ReplayController", "ReplayTrace", "TurnRecord",
    "EvalRunner", "MetricCalculator", "EvalCase", "EvalDataset", "EvalBenchmark",
    "EvalSummary", "EvalReport", "EvalDiff", "BenchmarkBuilder", "EvalComparator",
    "PluginProtocol", "PluginContext",
    "Decision",
    "Executable",
    "ExecuteResult",
    "ExecutionError",
    "RetryPolicy",
    "Wave",
]
