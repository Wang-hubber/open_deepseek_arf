"""A2A Plugin configuration model."""
from pydantic import BaseModel


class A2APluginConfig(BaseModel):
    max_concurrent_tasks: int = 3
    max_task_timeout: float = 600.0
