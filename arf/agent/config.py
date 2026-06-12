"""AgentConfig and AdvancedConfig — user-facing configuration models."""
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
import yaml
from arf.core.config_base import (
    McpServerConfig, ModelConfig, SkillConfig, ToolConfig, HookDefinition,
    CompactionConfig, MemoryConfig,
    GuardrailsConfig, ErrorConfig, ConcurrencyConfig, SandboxConfig, ToolRetrievalConfig,
    ReloadConfig, SupervisorConfig,
    ProtectionConfig, ObservabilityConfig, PromotionConfig, SessionConfig,
)


class RecoveryConfig(BaseModel):
    """Engine-level error recovery budgets. Each recovery path has an
    independent max-attempt counter to prevent infinite retry loops."""
    max_continuation: int = Field(default=3, ge=0, le=10)
    max_compaction: int = Field(default=3, ge=0, le=10)
    max_transport_retry: int = Field(default=3, ge=0, le=10)
    backoff_base: float = Field(default=1.0, ge=0.1, le=60.0)
    backoff_max: float = Field(default=30.0, ge=1.0, le=300.0)


class AdvancedConfig(BaseModel):
    """All internal framework mechanisms with production-grade defaults."""
    max_turns: int = 50
    max_undo_depth: int = 3           # max undo steps (RoundManager rolling window)
    call_timeout: float = 120.0       # per-call timeout, None = no limit
    session_timeout: float | None = None  # overall invoke timeout, None = no limit
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
    guardrails: GuardrailsConfig | None = None
    errors: ErrorConfig | None = None
    tool_retrieval: ToolRetrievalConfig | None = None
    concurrency: ConcurrencyConfig | None = None
    sandbox: SandboxConfig | None = None
    reload: ReloadConfig | None = None
    protection: ProtectionConfig | None = None
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    promotion: PromotionConfig | None = None
    observability: ObservabilityConfig | None = None
    session: SessionConfig | None = None

    @classmethod
    def default(cls) -> "AdvancedConfig":
        return cls()

    @classmethod
    def auto_derive(cls, tools_count: int, models_count: int) -> "AdvancedConfig":
        adv = cls.default()
        if tools_count > 20:
            adv.tool_retrieval = ToolRetrievalConfig(enabled=True, top_k=10)
        return adv


class PrefixConfig(BaseModel):
    """Stable prefix content — role definition and critical rules.

    Framework guarantees ordering: role → critical_rules.
    Both are highly stable to maximize API prompt cache hits.
    """
    role: str = ""
    critical_rules: str = ""


class SystemPromptConfig(BaseModel):
    """System prompt configuration with prefix/suffix separation.

    prefix — role + critical_rules (stable, cache target)
    suffix — template string with $INVENTORY, $MEMORY, $WORKSPACE etc.

    Placeholders use $VAR syntax (Python string.Template).
    """
    prefix: PrefixConfig = Field(default_factory=PrefixConfig)
    suffix: str = ""


class AgentConfig(BaseModel):
    """Agent = name + role + task + system_prompt + 4 core resources.
    User-facing: model/skill/tool/hook. Framework auto-handles the rest."""
    schema_version: str = Field(default="1.0", frozen=True)
    session_mode: Literal["auto", "ask", "plan"] = "ask"
    name: str
    role: str = ""
    task: str = ""
    description: str = ""
    system_prompt: SystemPromptConfig = Field(default_factory=SystemPromptConfig)
    models: list[ModelConfig] = Field(default_factory=list)  # optional — filesystem is source of truth
    model_defs: list[dict] = Field(default_factory=list)      # NEW: top-level model definitions {model, api_base, api_key_env, kwargs}
    agent_models: list[dict] = Field(default_factory=list)    # NEW: agent model refs [{model: x}, {model: y, kwargs: {}}]
    plugins_config: dict = Field(default_factory=dict)        # NEW: plugin configs with model refs
    skills: list[SkillConfig] = Field(default_factory=list)  # optional — filesystem is source of truth
    tools: list[ToolConfig] = Field(default_factory=list)    # optional — filesystem is source of truth
    plugins: list[str] = Field(default_factory=list)  # plugin names to activate from arf/plugins/
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    hooks: list[HookDefinition] = Field(default_factory=list)
    advanced: AdvancedConfig | None = None
    supervisor: SupervisorConfig | None = None

    def get_model_registry(self):
        """Build a ModelRegistry from model_defs (new format).

        Returns None if model_defs is empty (old format / filesystem mode).
        """
        if not self.model_defs:
            return None
        from arf.core.model_registry import ModelRegistry
        return ModelRegistry(self.model_defs)

    def get_agent_model_configs(self) -> list | None:
        """Resolve agent model refs from the registry. Returns None if no model_defs."""
        registry = self.get_model_registry()
        if not registry:
            return None
        refs = self.agent_models or [{"model": n} for n in registry.list_names()]
        return registry.resolve_list(refs)

    def get_plugin_model_config(self, plugin_name: str) -> "ResolvedModelConfig | None":
        """Resolve a plugin's model reference, returning ResolvedModelConfig or None.

        Supports two modes:
        - Reference: model name found in model_defs -> resolve + merge overrides
        - Inline: model name NOT in model_defs -> treat plugin_cfg as full definition
        """
        from arf.core.model_registry import ResolvedModelConfig

        registry = self.get_model_registry()
        if not registry:
            return None
        plugin_cfg = self.plugins_config.get(plugin_name, {})
        if not isinstance(plugin_cfg, dict) or "model" not in plugin_cfg:
            return None
        model_name = plugin_cfg["model"]
        overrides = {k: v for k, v in plugin_cfg.items() if k != "model"}
        if registry.has(model_name):
            cfg = registry.resolve(model_name)
            return ResolvedModelConfig(
                model=cfg.model,
                api_base=overrides.get("api_base", cfg.api_base),
                api_key_env=overrides.get("api_key_env", cfg.api_key_env),
                kwargs={**cfg.kwargs, **overrides.get("kwargs", {})},
            )
        else:
            return ResolvedModelConfig(
                model=model_name,
                api_base=overrides.get("api_base", "https://api.deepseek.com"),
                api_key_env=overrides.get("api_key_env", "DEEPSEEK_API_KEY"),
                kwargs=overrides.get("kwargs", {}),
            )

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
        # Models are defined inline in agent.yaml via model_defs (new format)
        # or models (legacy format). Filesystem ModelProvider is removed.
        return config

    def to_yaml(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(exclude_none=True, exclude={"schema_version"})
        header = f"# arf_version: {self.schema_version}\n"
        (d / "agent.yaml").write_text(header + yaml.dump(data, allow_unicode=True), encoding="utf-8")
