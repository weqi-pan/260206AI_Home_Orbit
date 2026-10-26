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
    # 可选：不传就用 approval_default_minutes。传了也只是「希望窗口多长」，
    # 服务端会夹到 approval_max_minutes 以内（见 api/approvals.py）
    expires_at: datetime | None = None


class InstallationRequest(BaseModel):
    household_id: str
    deployment_id: str


class InstallationPermissionRequest(BaseModel):
    tool_id: str
    tool_version: str = Field(min_length=1, max_length=32)
