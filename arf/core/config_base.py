"""Sub-configuration Pydantic models used by AgentConfig."""
from pydantic import BaseModel, Field
from typing import Literal


class ModelConfig(BaseModel):
    type: Literal["quick", "deep"]
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    context_window: int = 131_072  # max tokens for this model
    max_token: int | None = None  # per-call output token limit (maps to API max_tokens)
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
    pipeline: list[PipelineStep] = Field(default_factory=list)


class ToolConfig(BaseModel):
    name: str
    description: str = ""          # optional for sub-agent tool references
    parameters: dict = Field(default_factory=dict)
    allowed_dir: str | None = None


class HookDefinition(BaseModel):
    name: str
    type: Literal[
        "session_start", "round_start", "round_end",
        "pre_tool_exec", "post_tool_exec",
        "pre_model_call", "post_model_call", "session_end",
        "post_permission", "sandbox_persist",
    ]
    run: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    timeout: str = "30s"


class CompactionConfig(BaseModel):
    strategy: Literal["sliding_window", "none"] = "sliding_window"
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    store: Literal["file", "sqlite", "none"] = "file"
    workspace: str = "./data/memory"
    retriever: Literal["recent_first", "llm"] = "llm"
    writer: Literal["rule", "llm"] = "llm"
    max_tokens: int = 2000
    top_k: int = 5
    resident_file: str = "memory.md"
    max_size_kb: int = 300


class ApprovalConfig(BaseModel):
    """Approval channel config for tools in the 'ask' list.

    Only active when permissions.ask is non-empty. Tools in 'ask' require
    explicit user approval before execution.
    """
    channel: Literal["console", "websocket", "callback"] = "console"
    timeout: str = "60s"


class PermissionsConfig(BaseModel):
    """Unified tool permission model — deny / ask / allow.

    Three actions:
      - deny: hard block, tool call rejected
      - ask:  require user approval (uses ApprovalConfig for channel/timeout)
      - allow: execute immediately

    deny_patterns provide regex-based blocking for dangerous URI schemes etc.

    policy: per-agent override (only active when global session_mode=ask)
      - None:  follow global session_mode (default)
      - auto:  all tools allowed for this agent
      - ask:   evaluate deny/ask/allow lists
      - plan:  read-only for this agent
    """
    policy: Literal["auto", "ask", "plan"] | None = None
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)


class RegexPatternConfig(BaseModel):
    """A regex pattern + replacement for output sanitization."""
    pattern: str
    replacement: str


class DangerousPatternConfig(BaseModel):
    """A dangerous behavior pattern — matched pre-execution, blocked if found."""
    name: str
    pattern: str
    description: str = ""


class SensitivePatternConfig(BaseModel):
    """A sensitive info pattern — matched post-execution and pre-output, redacted."""
    name: str
    pattern: str
    replacement: str = "[REDACTED]"


class ContentGuardConfig(BaseModel):
    """Content safety configuration — dangerous behavior + sensitive info patterns.

    App config appends to framework built-in defaults. App can override a
    built-in rule by using the same 'name'.
    """
    enabled: bool = True
    dangerous_patterns: list[DangerousPatternConfig] = Field(default_factory=list)
    sensitive_patterns: list[SensitivePatternConfig] = Field(default_factory=list)


class GuardrailsConfig(BaseModel):
    input: Literal["none", "regex_block", "llm_classifier"] = "none"
    output: Literal["none", "regex_clean", "llm_classifier"] = "regex_clean"
    tool_params: Literal["none", "path_check", "command_check"] = "path_check"
    content_guard: ContentGuardConfig = Field(default_factory=ContentGuardConfig)
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


class PathCheckFlags(BaseModel):
    """Individual path safety checks. All enabled by default for security."""
    path_traversal: bool = True
    absolute_path: bool = True
    workspace_containment: bool = True
    symlink: bool = True


class SandboxConfig(BaseModel):
    checks: PathCheckFlags = Field(default_factory=PathCheckFlags)
    blacklist: list[str] = Field(
        default_factory=lambda: [".git", "__pycache__", "logs", ".env"],
        description="Paths excluded from sandbox copy"
    )
    auto_destroy: bool = Field(
        default=False,
        description="Auto-delete sandbox on session end"
    )


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


class PromotionConfig(BaseModel):
    """Permission gating strategy configuration."""
    strategy: Literal["auto", "ask", "plan"] = "ask"
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)


class McpServerConfig(BaseModel):
    """External MCP server connection configuration."""
    name: str
    transport: Literal["sse", "http", "stdio"] = "sse"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    api_key_env: str = ""
    timeout: str = "30s"
