"""AgentConfig and AdvancedConfig — user-facing configuration models."""
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
import yaml
from arf.core.config_base import (
    ModelConfig, SkillConfig, ToolConfig, HookDefinition,
    RoutingConfig, CompactionConfig, MemoryConfig,
    GuardrailsConfig, ErrorConfig, HumanLoopConfig,
    StreamingConfig, SandboxConfig, ToolRetrievalConfig,
    ReloadConfig, HandoverConfig, SupervisorConfig,
)


class AdvancedConfig(BaseModel):
    """All internal framework mechanisms with production-grade defaults."""
    loop_strategy: Literal["react", "direct", "plan_execute"] = "react"
    max_turns: int = 50
    critical_rules: str = ""
    routing: RoutingConfig | None = None
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
    guardrails: GuardrailsConfig | None = None
    errors: ErrorConfig | None = None
    human_loop: HumanLoopConfig | None = None
    streaming: StreamingConfig | None = None
    sandbox: SandboxConfig | None = None
    tool_retrieval: ToolRetrievalConfig | None = None
    reload: ReloadConfig | None = None

    @classmethod
    def default(cls) -> "AdvancedConfig":
        return cls()

    @classmethod
    def auto_derive(cls, tools_count: int, models_count: int) -> "AdvancedConfig":
        adv = cls.default()
        if tools_count > 20:
            adv.tool_retrieval = ToolRetrievalConfig(enabled=True, top_k=10)
        if models_count > 1:
            adv.routing = RoutingConfig(strategy="two_tier")
        return adv


class AgentConfig(BaseModel):
    """Agent = name + description + 4 core resources."""
    schema_version: str = Field(default="1.0", frozen=True)
    name: str
    description: str
    models: list[ModelConfig]
    skills: list[SkillConfig] = Field(default_factory=list)
    tools: list[ToolConfig] = Field(default_factory=list)
    hooks: list[HookDefinition] = Field(default_factory=list)
    advanced: AdvancedConfig | None = None
    agents: list["AgentConfig"] | None = None
    handover: HandoverConfig | None = None
    supervisor: SupervisorConfig | None = None

    def effective_advanced(self) -> AdvancedConfig:
        if self.advanced is not None:
            return self.advanced
        total_tools = len(self.tools) + sum(len(s.tools) for s in self.skills)
        return AdvancedConfig.auto_derive(total_tools, len(self.models))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        version = raw.pop("schema_version", "0.0")
        if version not in {"1.0", "0.0"}:
            raise ValueError(f"Unsupported schema version: {version}")
        return cls(**raw)

    def to_yaml(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(exclude_none=True, exclude={"schema_version"})
        header = f"# arf_version: {self.schema_version}\n"
        (d / "agent.yaml").write_text(header + yaml.dump(data, allow_unicode=True), encoding="utf-8")
