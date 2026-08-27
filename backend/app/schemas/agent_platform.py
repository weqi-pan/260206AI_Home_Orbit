from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VersionPublishRequest(BaseModel):
    household_id: str
    definition: dict[str, Any]


class DeploymentRequest(BaseModel):
    household_id: str
    agent_version_id: str


class ToolGrantRequest(BaseModel):
    household_id: str
    tool_version: str = "1"


class ApprovalDecisionRequest(BaseModel):
    expires_at: datetime


class InstallationRequest(BaseModel):
    household_id: str
    deployment_id: str


class InstallationPermissionRequest(BaseModel):
    tool_id: str
    tool_version: str = Field(min_length=1, max_length=32)
