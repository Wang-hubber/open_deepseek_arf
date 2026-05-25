"""Sub-configuration Pydantic models used by AgentConfig."""
from pydantic import BaseModel, Field
from typing import Literal


class ModelConfig(BaseModel):
    name: str
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    context_window: int = 131_072  # max tokens for this model
    kwargs: dict = Field(default_factory=dict)
    activation: Literal["kernel", "discoverable", "passive"] = "discoverable"


class PipelineStep(BaseModel):
    tool: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)

class SkillConfig(BaseModel):
    name: str
    description: str
    prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    activation: Literal["kernel", "discoverable", "passive"] = "discoverable"
    pipeline: list[PipelineStep] = Field(default_factory=list)


class ToolConfig(BaseModel):
    name: str
    description: str = ""          # optional for sub-agent tool references
    parameters: dict = Field(default_factory=dict)
    source: str | None = None
    provider: Literal["static_yaml", "mcp"] = "static_yaml"
    backend: Literal["function", "subprocess"] = "function"
    execution: dict = Field(default_factory=lambda: {"sandbox": "inherit", "timeout": "30s"})
    activation: Literal["kernel", "discoverable", "passive"] = "kernel"


class HookDefinition(BaseModel):
    name: str
    type: Literal[
        "session_start", "pre_tool_exec", "post_tool_exec",
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
    strategy: Literal["sliding_window", "summarization", "none"] = "sliding_window"
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    store: Literal["file", "sqlite", "none"] = "file"
    workspace: str = "./memory"
    retriever: Literal["recent_first", "semantic", "llm"] = "llm"
    writer: Literal["rule", "llm"] = "llm"
    max_tokens: int = 2000
    top_k: int = 5
    model: str = "quick"           # which configured model to use for memory ops
    temperature: float = 0.3
    thinking_enabled: bool = False  # disable reasoning for cost savings


class GuardrailsConfig(BaseModel):
    input: Literal["none", "regex_block", "llm_classifier"] = "none"
    output: Literal["none", "regex_clean", "llm_classifier"] = "regex_clean"
    tool_params: Literal["none", "path_check", "command_check"] = "path_check"


class ErrorConfig(BaseModel):
    tool_retry: int = 2
    tool_backoff: Literal["exponential", "linear", "none"] = "exponential"
    model_retry: int = 3
    model_5xx_action: Literal["fallback", "retry", "abort"] = "fallback"
    guardrail_block_action: Literal["abort", "ask_user"] = "abort"


class HumanLoopConfig(BaseModel):
    approval_points: Literal["always_auto", "tool_name_allowlist"] = "always_auto"
    allowlist: list[str] = Field(default_factory=list)
    channel: Literal["console", "websocket", "callback"] = "console"
    timeout: str = "3600s"


class StreamingConfig(BaseModel):
    transport: Literal["sse", "websocket", "callback"] = "sse"
    event_types: list[str] = Field(default_factory=lambda: ["all"])


class SandboxConfig(BaseModel):
    allow_escape: bool = False
    writable_dirs: list[str] = Field(default_factory=list)


class ToolRetrievalConfig(BaseModel):
    enabled: bool = False
    top_k: int = 10


class ReloadConfig(BaseModel):
    watch: bool = False
    signals: list[str] = Field(default_factory=lambda: ["SIGHUP"])


class HandoverRuleConfig(BaseModel):
    from_agent: str
    to_agent: str
    trigger: str


class HandoverConfig(BaseModel):
    rules: list[HandoverRuleConfig] = Field(default_factory=list)


class SupervisorConfig(BaseModel):
    type: Literal["round_robin", "llm_router", "custom"] = "round_robin"
    llm_model: str | None = None
