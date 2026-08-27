from dataclasses import dataclass

from sqlalchemy import select

from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.agent.platform.policy_errors import PlatformToolError
from backend.app.db.models import AgentDeployment, AgentInstallation, InstallationPermission, ToolDefinition


@dataclass(frozen=True)
class ToolAuthority:
    installation_id: str
    permission_id: str
    granted_by_account_id: str | None
    tool_id: str
    key: str
    version: str
    risk_level: str


class ExecutionAuthority:
    def __init__(self, session, registry=None):
        self.session = session
        self.registry = registry or BuiltinToolRegistry.default()

    def installation(self, deployment_id, household_id, agent_version_id):
        deployment = self.session.scalar(select(AgentDeployment).where(
            AgentDeployment.id == deployment_id, AgentDeployment.household_id == household_id))
        installation = self.session.scalar(select(AgentInstallation).where(
            AgentInstallation.deployment_id == deployment_id, AgentInstallation.household_id == household_id,
            AgentInstallation.status == "installed").order_by(AgentInstallation.id))
        if not deployment or not installation:
            raise PlatformToolError("INSTALLATION_REQUIRED")
        if deployment.agent_version_id != agent_version_id or installation.agent_version_id != agent_version_id:
            raise PlatformToolError("INSTALLATION_VERSION_MISMATCH")
        return deployment, installation

    def tools(self, deployment_id, household_id, agent_version_id, bound_tool_ids=None):
        _deployment, installation = self.installation(deployment_id, household_id, agent_version_id)
        statement = select(ToolDefinition, InstallationPermission).join(
            InstallationPermission, InstallationPermission.tool_id == ToolDefinition.id).where(
                InstallationPermission.installation_id == installation.id,
                InstallationPermission.household_id == household_id,
                InstallationPermission.tool_version == ToolDefinition.version,
                ToolDefinition.enabled.is_(True))
        if bound_tool_ids is not None:
            statement = statement.where(ToolDefinition.id.in_(bound_tool_ids))
        result = []
        for row, permission in self.session.execute(statement).all():
            try:
                metadata = self.registry.resolve(row.key).metadata
            except KeyError:
                continue
            if metadata.version != row.version or metadata.risk_level != row.risk_level:
                continue
            result.append(ToolAuthority(installation.id, permission.id, permission.granted_by_account_id,
                                        row.id, row.key, row.version, row.risk_level))
        return tuple(result)

    def recheck(self, context, key, version):
        matches = [item for item in self.tools(context.deployment_id, context.household_id,
                                               context.agent_version_id) if item.key == key and item.version == version]
        if not matches:
            raise PlatformToolError("PERMISSION_REQUIRED")
        return matches[0]
