"""Sub-configuration Pydantic models used by AgentConfig."""
from pydantic import BaseModel, Field
from typing import Literal


class PipelineSection(BaseModel):
    """A named section in the system prompt pipeline, ordered by priority."""
    priority: int
    section: str  # workspace | memory | critical_rules | inventory | language
    description: str = ""


class ModelConfig(BaseModel):
    type: Literal["quick", "deep"]
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    context_window: int = 131_072  # max tokens for this model
    kwargs: dict = Field(default_factory=dict)
    activation: Literal["kernel", "discoverable"] = "discoverable"


class PipelineStep(BaseModel):
    tool: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)

class SkillConfig(BaseModel):
    name: str
    description: str
    prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    activation: Literal["kernel", "discoverable"] = "discoverable"
    pipeline: list[PipelineStep] = Field(default_factory=list)


class ToolConfig(BaseModel):
    name: str
    description: str = ""          # optional for sub-agent tool references
    parameters: dict = Field(default_factory=dict)
    activation: Literal["kernel", "discoverable"] = "kernel"


class HookDefinition(BaseModel):
    name: str
    type: Literal[
        "session_start", "round_start", "round_end",
        "pre_tool_exec", "post_tool_exec",
        "pre_model_call", "post_model_call", "session_end",
    ]
    run: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    timeout: str = "30s"


class RoutingConfig(BaseModel):
    strategy: Literal["two_tier", "static"] = "two_tier"
    default: str = ""
    classify: dict[str, str] = Field(default_factory=dict)
    background: str | None = None
    fallback: dict[str, str] = Field(default_factory=dict)


class CompactionConfig(BaseModel):
    strategy: Literal["sliding_window", "none"] = "sliding_window"
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    store: Literal["file", "sqlite", "none"] = "file"
    workspace: str = "./memory"
    retriever: Literal["recent_first", "llm"] = "llm"
    writer: Literal["rule", "llm"] = "llm"
    max_tokens: int = 2000
    top_k: int = 5
    resident_file: str = "memory.md"
    max_size_kb: int = 300


class PermissionsConfig(BaseModel):
    """Tool permission lists — deny / ask / allow, plus custom deny patterns."""
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)


class RegexPatternConfig(BaseModel):
    """A regex pattern + replacement for output sanitization."""
    pattern: str
    replacement: str


class GuardrailsConfig(BaseModel):
    input: Literal["none", "regex_block", "llm_classifier"] = "none"
    output: Literal["none", "regex_clean", "llm_classifier"] = "regex_clean"
    tool_params: Literal["none", "path_check", "command_check"] = "path_check"
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    output_patterns: list[RegexPatternConfig] = Field(
        default_factory=list,
        description="Custom regex patterns for output sanitization. "
                    "Empty list = use framework built-in defaults. "
                    "Pass [{\"pattern\": \".*\", \"replacement\": \"[REDACTED]\"}] to disable defaults.",
    )


class ErrorConfig(BaseModel):
    tool_retry: int = 2
    tool_backoff: Literal["exponential", "linear", "none"] = "exponential"
    model_5xx_action: Literal["fallback", "retry", "abort"] = "fallback"
    guardrail_block_action: Literal["abort", "ask_user"] = "abort"


class HumanLoopConfig(BaseModel):
    approval_points: Literal["always_auto", "tool_name_allowlist"] = "always_auto"
    allowlist: list[str] = Field(default_factory=list)
    channel: Literal["console", "websocket", "callback"] = "console"
    timeout: str = "3600s"


class PathCheckFlags(BaseModel):
    """Individual path safety checks. Default: only workspace containment."""
    path_traversal: bool = False
    absolute_path: bool = False
    workspace_containment: bool = True
    symlink: bool = False


class SandboxConfig(BaseModel):
    allow_escape: bool = False
    writable_dirs: list[str] = Field(default_factory=list)
    checks: PathCheckFlags = Field(default_factory=PathCheckFlags)


class ToolRetrievalConfig(BaseModel):
    enabled: bool = False
    top_k: int = 10


class ConcurrencyConfig(BaseModel):
    strategy: Literal["parallel", "sequential"] = "parallel"
    max_concurrency: int = Field(default=5, ge=1)


class ReloadConfig(BaseModel):
    watch: bool = True
    poll_interval: float = 5.0
    signals: list[str] = Field(default_factory=lambda: ["SIGHUP"])


class HandoverContextConfig(BaseModel):
    """Context strategy for handoff between agents.

    raw_turns: number of recent conversation turns to include (-1 = all, 0 = none)
    task_summary: whether to generate an LLM task summary
    """
    raw_turns: int = Field(default=5, ge=-1)
    task_summary: bool = True


class HandoverRuleConfig(BaseModel):
    from_agent: str
    to_agent: str
    trigger: str
    context: HandoverContextConfig = Field(default_factory=HandoverContextConfig)


class HandoverConfig(BaseModel):
    rules: list[HandoverRuleConfig] = Field(default_factory=list)


class SupervisorConfig(BaseModel):
    type: Literal["round_robin", "llm_router", "custom"] = "round_robin"
    llm_model: str | None = None


class ProtectionRateLimitConfig(BaseModel):
    """Token bucket rate limiter per api_base."""
    requests_per_second: float = 5.0
    max_burst: int = 10


class ProtectionCircuitBreakerConfig(BaseModel):
    """Exponential cooldown circuit breaker per model."""
    failure_threshold: int = 3
    base_cooldown: str = "10s"
    cooldown_multiplier: float = 2.0
    max_cooldown: str = "300s"
    half_open_max_requests: int = 1


class ProtectionConfig(BaseModel):
    """API protection: rate limiting + circuit breaker."""
    enabled: bool = True
    rate_limit: ProtectionRateLimitConfig = Field(default_factory=ProtectionRateLimitConfig)
    circuit_breaker: ProtectionCircuitBreakerConfig = Field(default_factory=ProtectionCircuitBreakerConfig)


class ObservabilityConfig(BaseModel):
    """Trace, usage tracking, replay, and telemetry — all auto-wired by BaseAgent."""
    trace_dir: str = "./memory/traces"
    usage_dir: str = "./memory"
    trace_enabled: bool = True
    otel_exporter: Literal["none", "console", "otlp"] = "none"
