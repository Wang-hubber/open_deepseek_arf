"""Memory configuration models — three independent layers, each gated by enabled."""
from pydantic import BaseModel, Field


class ProjectMemoryConfig(BaseModel):
    enabled: bool = True
    auto_generate: bool = True
    rolling_update: bool = True
    compaction_interval: int = Field(default=30, ge=1)
    max_size_kb: int = Field(default=100, ge=1)


class UserMemoryConfig(BaseModel):
    enabled: bool = True
    extract_interval: int = Field(default=5, ge=1)
    max_size_kb: int = Field(default=50, ge=1)


class SecretsConfig(BaseModel):
    enabled: bool = True
    master_key_env: str = "ARF_MASTER_KEY"


class MemoryConfig(BaseModel):
    project: ProjectMemoryConfig = Field(default_factory=ProjectMemoryConfig)
    user: UserMemoryConfig = Field(default_factory=UserMemoryConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
