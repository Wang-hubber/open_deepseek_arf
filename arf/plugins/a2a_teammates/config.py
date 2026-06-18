"""Peer Team Plugin configuration model."""
from pydantic import BaseModel, Field


class MemberConfig(BaseModel):
    role: str = Field(..., description="Member role (e.g., pm, dev, data)")
    agent_name: str = Field(..., description="Name of the configured agent for this role")
    entry_point: bool = Field(default=False, description="Whether this member serves as a recovery entry point")


class PeerTeamConfig(BaseModel):
    group_id: str = Field(default="default", description="Unique group identifier")
    members: list[MemberConfig] = Field(..., min_length=1, description="Team members")
