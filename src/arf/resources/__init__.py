"""Resource base classes -- Model, Tool, Skill."""

from abc import ABC
from dataclasses import dataclass, field
from typing import Literal


ResourceType = Literal["model", "tool", "skill"]


@dataclass
class Resource(ABC):
    """Unified base class for all resource types."""

    type: ResourceType
    name: str
    description: str = ""
    metadata: dict = field(default_factory=dict)
    source: str = "user"       # "system" | "user"
    readonly: bool = False


@dataclass
class Model(Resource):
    model_type: Literal[
        "deep_thinking", "quick_thinking", "quick_no_thinking",
        "embedding", "rerank", "vision", "tts", "stt", "vlm", "other",
    ] = "deep_thinking"
    config: dict = field(default_factory=dict)
    config_template: dict = field(default_factory=dict)
    depends_on: list[dict] = field(default_factory=list)
    required: bool = False
    configured: bool = False


@dataclass
class Tool(Resource):
    schema: dict = field(default_factory=dict)
    depends_on: list[dict] = field(default_factory=list)
    required: bool = False
    configured: bool = False


@dataclass
class Skill(Resource):
    prompt_template: str = ""
    sub_skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    depends_on: list[dict] = field(default_factory=list)
    required: bool = False
    configured: bool = False
