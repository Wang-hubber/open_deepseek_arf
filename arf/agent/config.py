"""AgentConfig and AdvancedConfig — user-facing configuration models."""
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
import yaml
from arf.core.config_base import (
    ModelConfig, SkillConfig, ToolConfig, HookDefinition,
    RoutingConfig, CompactionConfig, MemoryConfig,
    PipelineSection,
    GuardrailsConfig, ErrorConfig, HumanLoopConfig, ConcurrencyConfig, SandboxConfig, ToolRetrievalConfig,
    ReloadConfig, HandoverConfig, SupervisorConfig,
    ProtectionConfig,
)


class AdvancedConfig(BaseModel):
    """All internal framework mechanisms with production-grade defaults."""
    loop_strategy: Literal["react", "direct", "plan_execute"] = "react"
    max_turns: int = 50
    max_undo_depth: int = 3           # max undo steps (RoundManager rolling window)
    system_model: str | None = None  # model name for background tasks (memory, routing, compaction)
    routing: RoutingConfig | None = None
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
    guardrails: GuardrailsConfig | None = None
    errors: ErrorConfig | None = None
    human_loop: HumanLoopConfig | None = None
    tool_retrieval: ToolRetrievalConfig | None = None
    concurrency: ConcurrencyConfig | None = None
    sandbox: SandboxConfig | None = None
    reload: ReloadConfig | None = None
    protection: ProtectionConfig | None = None

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


class SystemPromptConfig(BaseModel):
    """System prompt template with critical rules.
    Supports {{PLACEHOLDERS}} filled by engine at runtime.
    When pipeline is configured, sections are assembled in priority order;
    otherwise falls back to simple placeholder replacement."""
    template: str = ""
    critical_rules: str = ""
    pipeline: list[PipelineSection] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """Agent = name + role + task + system_prompt + 4 core resources.
    User-facing: model/skill/tool/hook. Framework auto-handles the rest."""
    schema_version: str = Field(default="1.0", frozen=True)
    name: str
    role: str = ""
    task: str = ""
    description: str = ""
    system_prompt: SystemPromptConfig = Field(default_factory=SystemPromptConfig)
    models: list[ModelConfig] = Field(default_factory=list)  # optional — filesystem is source of truth
    skills: list[SkillConfig] = Field(default_factory=list)  # optional — filesystem is source of truth
    tools: list[ToolConfig] = Field(default_factory=list)    # optional — filesystem is source of truth
    plugins: list[str] = Field(default_factory=list)  # plugin names to activate from arf/plugins/
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
        config = cls(**raw)
        # Auto-load models from filesystem (filesystem is source of truth,
        # agent.yaml overrides are merged on top).
        models_dir = Path(path).parent / "models"
        if models_dir.exists():
            from arf.resources.providers.model_provider import ModelProvider
            fs_models = ModelProvider(models_dir).list()
            agent_models = {m.type: m for m in config.models}
            merged: list[ModelConfig] = []
            for fm in fs_models:
                if fm.type in agent_models:
                    merged.append(
                        fm.model_copy(update=agent_models[fm.type].model_dump(exclude_none=True))
                    )
                else:
                    merged.append(fm)
            for t, am in agent_models.items():
                if not any(m.type == t for m in merged):
                    merged.append(am)
            config.models = merged
        return config

    def to_yaml(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(exclude_none=True, exclude={"schema_version"})
        header = f"# arf_version: {self.schema_version}\n"
        (d / "agent.yaml").write_text(header + yaml.dump(data, allow_unicode=True), encoding="utf-8")
