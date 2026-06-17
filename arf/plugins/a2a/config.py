"""A2A Plugin configuration model."""
from pydantic import BaseModel, Field


class A2APluginConfig(BaseModel):
    max_concurrent_tasks: int = Field(default=3, ge=1, description="Max concurrent sub-agents per session")
    max_task_timeout: float = Field(default=600.0, gt=0, description="Hard cap for sub-agent execution time (seconds)")
