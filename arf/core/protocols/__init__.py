"""Protocol definitions — abstract interfaces for all framework domains."""
from arf.core.protocols.engine import StateStore
from arf.core.protocols.resources import (
    ToolResolver, ToolProvider, ToolRetriever, ToolBackend, ToolDefinition,
)
from arf.core.protocols.guardrails import (
    GuardRunner, InputGuardrail, OutputGuardrail, ToolGuardrail,
)
from arf.core.protocols.compaction import CompactionStrategy
from arf.core.protocols.communication import (
    AgentBus, TaskDelegator, AgentMessage, AgentInfo,
)
from arf.core.protocols.event_bus import EventBus
from arf.core.protocols.prompt import SystemPromptProvider
from arf.core.protocols.replay import ReplayController, ReplayTrace, TurnRecord
from arf.core.protocols.evaluation import (
    EvalCase, EvalDataset, EvalBenchmark,
    EvalSummary, EvalReport, EvalDiff,
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
    "StateStore",
    "ToolResolver", "ToolProvider", "ToolRetriever", "ToolBackend", "ToolDefinition",
    "GuardRunner", "InputGuardrail", "OutputGuardrail", "ToolGuardrail",
    "CompactionStrategy",
    "AgentBus", "TaskDelegator", "AgentMessage", "AgentInfo",
    "EventBus",
    "SystemPromptProvider",
    "ReplayController", "ReplayTrace", "TurnRecord",
    "EvalCase", "EvalDataset", "EvalBenchmark",
    "EvalSummary", "EvalReport", "EvalDiff",
    "PluginProtocol", "PluginContext",
    "Decision",
    "Executable",
    "ExecuteResult",
    "ExecutionError",
    "RetryPolicy",
    "Wave",
]
